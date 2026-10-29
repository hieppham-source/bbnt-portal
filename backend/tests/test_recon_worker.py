import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import DEFAULT_TEMPLATES_DIR
from app.master.repository import MasterRepository
from app.recon import worker
from app.recon.bbnt_job import CaseBbnt, ContractRow, make_bbnt
from app.recon.rules import CellAction
from app.recon.worker import Action, OrderLink, plan_cases
from app.services.templates import TemplateRegistry

from .conftest import MASTER_XLSX, ORDER_URL_FB, ORDER_URL_SHISEIDO, ORGANICA_MAY, SHISEIDO_Q3, order_rows
from .test_recon_rules import HEADER

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
CONTRACTS = [ContractRow("01/2026/HĐDV/EZS-ORGANICA", "ORGANICA", "EZS", "FB", "Đã ký"),
             ContractRow("05/2026/HĐDV/SS-SHISEIDO", "SHISEIDO_GRP", "SASSY", "FB", "Đã ký")]
LINKS = [OrderLink("Organica", "BDAY - 05/2026", ORDER_URL_FB),
         OrderLink("SHISEIDO", "Q3 - 09/2026", ORDER_URL_SHISEIDO),
         OrderLink("ANESSA", "Q3 - 09/2026", ORDER_URL_SHISEIDO)]
ORDERS = {ORDER_URL_FB: ORGANICA_MAY, ORDER_URL_SHISEIDO: SHISEIDO_Q3}


def row(brand, campaign, end, entity, *, group="", op="NON OP", nmv=None, k=None, gmv=None, status="", notes=""):
    r = [""] * len(HEADER)
    r[0], r[1], r[2], r[3], r[5], r[6], r[7] = "10/2026", group, brand, campaign, entity, end, op
    r[8], r[10], r[12], r[14], r[20] = nmv, k, gmv, notes, status
    return r


def sheet(*rows):
    return [[], [], [], [], [], HEADER, *rows]


@pytest.fixture
def run(master_tables):
    master = MasterRepository(lambda: master_tables, ttl_seconds=60).get()
    templates = TemplateRegistry(DEFAULT_TEMPLATES_DIR)

    def go(*rows, contracts=CONTRACTS, today=TODAY, links=LINKS, orders=ORDERS, now=NOW, issued_at=None,
           master_=master, scope_from=date(2026, 1, 1), require_revenue=False):   # mẫu: tháng 05–09, M trống
        return plan_cases(sheet(*rows), links, contracts, master_, templates, worker._fixture_opener(orders),
                          today=today, now=now, issued_at=issued_at, scope_from=scope_from,
                          require_revenue=require_revenue)
    return go


def test_single_brand_draft_plans_all_three_cells(run):
    [p] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS"))
    assert p.action is Action.DRAFT and p.reasons == []
    assert p.send_from == date(2026, 6, 25)
    assert p.bbnt_number == "10.26/BBNT-ORGANICA-EZ-FB-BDAY.0526"
    assert [(c.column, c.action) for c in p.cells] == [("nmv", CellAction.WRITE), ("commission", CellAction.WRITE),
                                                       ("gmv", CellAction.WRITE)]
    assert all(c.target > 0 for c in p.cells)


def test_existing_different_figure_goes_to_review(run):
    [p] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS", nmv=1))
    assert p.action is Action.REVIEW and "FIGURES_DIFFER_FBJT" in p.reasons
    assert p.cells[0].action is CellAction.REVIEW and p.cells[1].action is CellAction.WRITE


def test_group_link_splits_figures_per_brand_and_k_sums_to_bbnt(run, master_tables):
    plans = run(row("SHISEIDO", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"),
                row("ANESSA", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"), today=date(2026, 10, 25))
    [p] = plans
    assert p.action is Action.DRAFT, p.reasons
    assert p.rows == [7, 8] and p.brands == ["ANESSA", "SHISEIDO"]
    k = {c.row_number: c.target for c in p.cells if c.column == "commission"}
    nmv = {c.row_number: c.target for c in p.cells if c.column == "nmv"}
    assert nmv == {7: Decimal(10_000_000), 8: Decimal(5_000_000)}
    bbnt = make_bbnt(TemplateRegistry(DEFAULT_TEMPLATES_DIR), MasterRepository(lambda: master_tables).get(),
                     CaseBbnt("SASSY", ["ANESSA", "SHISEIDO"], "Q3 - 09/2026", "05/2026/HĐDV/SS-SHISEIDO"),
                     worker._fixture_opener(ORDERS)(ORDER_URL_SHISEIDO), issued_at=NOW, render=False).result
    assert sum(k.values()) == worker._commission_k(bbnt).quantize(Decimal(1))   # tổng các dòng = K của BBNT
    assert abs(k[7] - 2 * k[8]) <= 2                                             # chia theo NMV 10tr : 5tr


def test_wait_skip_and_blocking_reasons(run):
    plans = run(
        row("ORGANICA", "BDAY - 05/2026", "20/10/2026", "EZS"),                     # chưa tới G + 25
        row("NOLINK", "MIX - 07/2026", "31/07/2026", "EZS"),                        # không có link order
        row("SHISEIDO", "Q3 - 09/2026", "", "SASSY"),                               # chưa có ngày kết thúc
    )
    by_rows = {tuple(p.rows): p for p in plans}
    assert by_rows[(7,)].action is Action.WAIT and by_rows[(7,)].send_from == date(2026, 11, 14)
    assert by_rows[(8,)].action is Action.SKIP and by_rows[(8,)].reasons == ["LINK_NOT_FOUND"]
    assert by_rows[(9,)].action is Action.WAIT and by_rows[(9,)].reasons == ["NO_CAMP_END"]


def test_op_and_contract_problems_block(run):
    [op] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS", op="OP"))
    assert (op.action, op.reasons) == (Action.SKIP, ["NOT_FB_NON_OP"])      # OP: ngoài phạm vi bot
    [nc] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS"), contracts=[])
    assert (nc.action, nc.reasons) == (Action.REVIEW, ["CONTRACT_NOT_UNIQUE"])


def test_cli_dry_run_prints_plan(tmp_path, capsys):
    fixture = {"fbjt": sheet(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS", gmv=7_200_000)),
               "links": [vars(x) for x in LINKS], "contracts": [vars(x) for x in CONTRACTS], "orders": ORDERS}
    f = tmp_path / "fixture.json"
    f.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    assert worker.main(["dry-run", "--fixture", str(f), "--master-file", str(MASTER_XLSX),
                        "--today", "2026-10-28", "--scope-from", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    assert "[DRAFT] dòng 7 · ORGANICA · BDAY - 05/2026" in out and "commission:" in out


# ------------------------------------------------------------------ case biên (plan bước 8, yêu cầu 28/10/2026)
ORG = ("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS")
ORG_ID = "1OrderFbAbCdEfGhIjKlMnOpQr"


def test_same_sheet_different_url_forms_is_one_case(run):
    links = [*LINKS, OrderLink("ORGANICA", "BDAY - 05/2026", f"https://docs.google.com/spreadsheets/d/{ORG_ID}"
                                                             "/edit?usp=sharing")]
    [p] = run(row(*ORG), links=links)
    assert p.action is Action.DRAFT and p.order_sheet_id == ORG_ID


def test_two_sheets_for_same_key_is_ambiguous(run):
    other = "https://docs.google.com/spreadsheets/d/1AnotherOrderSheetXyzAbCdEf/edit"
    [p] = run(row(*ORG), links=[*LINKS, OrderLink("Organica", "BDAY - 05/2026", other)])
    assert (p.action, p.reasons, p.order_sheet_id) == (Action.SKIP, ["LINK_AMBIGUOUS"], None)


def test_missing_and_invalid_link_skip(run):
    [p] = run(row(*ORG), links=[OrderLink("ORGANICA", "BDAY - 05/2026", "chưa có link")])
    assert (p.action, p.reasons) == (Action.SKIP, ["LINK_INVALID"])
    [p] = run(row(*ORG), links=[])
    assert (p.action, p.reasons) == (Action.SKIP, ["LINK_NOT_FOUND"])


def test_duplicate_fbjt_rows_and_invalid_row_go_to_review(run):
    plans = run(row(*ORG), row(*ORG), row("SHISEIDO", "Q3 - 09/2026", "30/09/2026", "SASSY", nmv="abc"))
    assert sorted((p.rows[0], p.action, tuple(p.reasons)) for p in plans) == [
        (7, Action.REVIEW, ("FBJT_ROW_DUPLICATE",)), (8, Action.REVIEW, ("FBJT_ROW_DUPLICATE",)),
        (9, Action.REVIEW, ("FBJT_ROW_INVALID",))]


def test_one_link_mixed_campaign_or_entity_goes_to_review(run):
    links = [*LINKS, OrderLink("ORGANICA", "MIX - 05/2026", ORDER_URL_FB)]
    [p] = run(row(*ORG), row("ORGANICA", "MIX - 05/2026", "31/05/2026", "EZS"), links=links)
    assert p.action is Action.REVIEW and p.reasons == ["CAMPAIGN_MISMATCH"]
    [p] = run(row("SHISEIDO", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"),
              row("ANESSA", "Q3 - 09/2026", "30/09/2026", "EZS", group="SHISEIDO_GRP"))
    assert p.action is Action.REVIEW and p.reasons == ["ENTITY_MISMATCH"]


def test_group_link_missing_a_brand_row_goes_to_review(run):
    """Link group 2 brand nhưng FB.JT chỉ còn 1 dòng (dòng kia Done/thiếu) -> không chia số được."""
    [p] = run(row("SHISEIDO", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"))
    assert p.action is Action.REVIEW


@pytest.mark.parametrize("contracts, reason", [
    ([*CONTRACTS, ContractRow("02/2026/HĐDV/EZS-ORGANICA", "ORGANICA", "EZS", "FB", "Đã ký")], "CONTRACT_NOT_UNIQUE"),
    ([ContractRow("01/2026/HĐDV/EZS-ORGANICA", "ORGANICA", "EZS", "FB", "Chờ ký")], "CONTRACT_NOT_UNIQUE"),
    ([ContractRow("01/2026/HĐDV/EZS-ORGANICA", "ORGANICA", "EZS", "TTS", "Đã ký")], "CONTRACT_NOT_UNIQUE"),
    ([ContractRow("99/2026/KHONG-CO-TRONG-MASTER", "ORGANICA", "EZS", "FB", "Đã ký")], "CONTRACT_NOT_FOUND"),
])
def test_contract_must_be_unique_signed_fb_and_in_portal_master(run, contracts, reason):
    [p] = run(row(*ORG), contracts=contracts)
    assert (p.action, p.reasons) == (Action.REVIEW, [reason])


def test_op_contract_model_blocks_even_if_fbjt_op_empty(run, master_tables):
    hd = master_tables["HOP_DONG"]
    model = hd[0].index("Mô hình")
    for r in hd[1:]:
        if r[0] == "01/2026/HĐDV/EZS-ORGANICA":
            r[model] = "OP"
    [p] = run(row(*ORG), master_=MasterRepository(lambda: master_tables).get())
    assert p.action is Action.REVIEW and "CONTRACT_MODEL_OP" in p.reasons


def test_nmv_only_delivered_and_excluded_statuses_are_reported(run):
    url = "https://docs.google.com/spreadsheets/d/1StatusMixAbCdEfGhIjKlMnOp/edit"
    orders = {**ORDERS, url: order_rows([
        ["03/05/2026", "X1", "Đã giao", "Organica", "ORG-01", "Serum", 1, 1_000_000],
        ["04/05/2026", "X2", "Hoàn hàng", "Organica", "ORG-01", "Serum", 1, 500_000],
        ["05/05/2026", "X3", "Đã hủy", "Organica", "ORG-01", "Serum", 1, 2_000_000],
        ["06/05/2026", "X4", "Đang giao", "Organica", "ORG-01", "Serum", 1, 3_000_000],   # status lạ
        ["07/05/2026", "X5", "Hoàn thành", "Organica", "ORG-01", "Serum", 1, 100_000],     # Q9: tính NMV
        ["08/05/2026", "X6", "Confirmed", "Organica", "ORG-01", "Serum", 1, 10_000],       # Q9: tính NMV
    ])}
    [p] = run(row(*ORG), links=[OrderLink("ORGANICA", "BDAY - 05/2026", url)], orders=orders)
    cells = {c.column: c.target for c in p.cells}
    assert cells["nmv"] == 1_110_000 and cells["gmv"] == 6_610_000
    assert p.warnings == {"RETURNED": 1, "CANCELLED": 1, "UNKNOWN_STATUS": 1}
    # Status lạ bị LOẠI khỏi NMV và chỉ cảnh báo, không chặn (Q9 chốt 28/10/2026).
    assert p.action is Action.DRAFT


def test_k_keeps_ezs_vat_8_and_sassy_deduct_1(run):
    [ezs] = run(row(*ORG))
    c = {x.column: x.target for x in ezs.cells}
    rev = (c["nmv"] * Decimal("0.18")).quantize(Decimal(1))
    assert c["commission"] == rev + (rev * Decimal("0.08")).quantize(Decimal(1))
    [ss] = run(row("SHISEIDO", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"),
               row("ANESSA", "Q3 - 09/2026", "30/09/2026", "SASSY", group="SHISEIDO_GRP"), today=date(2026, 10, 25))
    nmv = sum(x.target for x in ss.cells if x.column == "nmv")
    rev = (nmv * Decimal("0.15")).quantize(Decimal(1))
    assert sum(x.target for x in ss.cells if x.column == "commission") == rev - (rev * Decimal("0.01")).quantize(
        Decimal(1))


def test_rerun_same_case_is_deterministic(run):
    assert run(row(*ORG)) == run(row(*ORG))


def test_rerun_in_another_month_keeps_number_only_with_stored_issued_at(run):
    nov = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    [fresh] = run(row(*ORG), now=nov, today=nov.date())
    assert fresh.bbnt_number == "11.26/BBNT-ORGANICA-EZ-FB-BDAY.0526"      # lập mới -> tháng lập mới
    [again] = run(row(*ORG), now=nov, today=nov.date(), issued_at={ORG_ID: NOW})
    assert again.bbnt_number == "10.26/BBNT-ORGANICA-EZ-FB-BDAY.0526"      # đã lưu issued_at -> số cũ
    assert [c.target for c in again.cells] == [c.target for c in run(row(*ORG))[0].cells]


def test_issue_after_tax_rule_expiry_goes_to_review(run):
    jan = datetime(2027, 1, 5, 2, 0, tzinfo=UTC)
    [p] = run(row(*ORG), now=jan, today=jan.date())
    assert (p.action, p.reasons) == (Action.REVIEW, ["TAX_RULE_MISSING"])   # không đoán thuế suất 2027
    [p] = run(row(*ORG), now=jan, today=jan.date(), issued_at={ORG_ID: NOW})
    assert p.action is Action.DRAFT


FIXTURE = Path(__file__).parent / "fixtures" / "recon_dry_run.json"


def test_representative_fixture_covers_all_actions(capsys):
    assert worker.main(["dry-run", "--fixture", str(FIXTURE), "--master-file", str(MASTER_XLSX),
                        "--today", "2026-10-28", "--scope-from", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    tags = [line.split("]")[0] + "]" for line in out.splitlines() if line.startswith("[")]
    assert sorted(set(tags)) == ["[DRAFT]", "[REVIEW]", "[SKIP]", "[WAIT]"], out


@pytest.mark.parametrize("valid_from, valid_to, ok", [
    (datetime(2026, 1, 1), datetime(2026, 10, 28), True),     # ngày lập BBNT = ngày cuối hiệu lực
    (datetime(2026, 1, 1), datetime(2026, 9, 30), False),     # hết hạn trước ngày lập
    (datetime(2026, 11, 1), None, False),                     # chưa hiệu lực
])
def test_contract_must_be_valid_on_bbnt_date(run, master_tables, valid_from, valid_to, ok):
    hd = master_tables["HOP_DONG"]
    i_from, i_to = hd[0].index("Hiệu lực từ"), hd[0].index("Hiệu lực đến")
    for r in hd[1:]:
        if r[0] == "01/2026/HĐDV/EZS-ORGANICA":
            r[i_from], r[i_to] = valid_from, valid_to
    [p] = run(row(*ORG), master_=MasterRepository(lambda: master_tables).get())
    assert (p.action is Action.DRAFT) is ok
    assert ("CONTRACT_NOT_VALID_ON_BBNT_DATE" in p.reasons) is not ok


def test_join_tolerates_spacing_date_format_and_brand_punctuation(run):
    links = [OrderLink("Organica.", "BDAY- 5.26", ORDER_URL_FB)]     # gõ khác FB.JT nhưng cùng nghĩa
    [p] = run(row(*ORG), links=links)
    assert p.action is Action.DRAFT


def test_link_not_found_hints_same_brand_same_month(run):
    links = [OrderLink("ORGANICA", "MIX FOOD - 05/2026", ORDER_URL_FB)]
    [p] = run(row(*ORG), links=links)
    assert p.reasons == ["LINK_NOT_FOUND"] and p.hints == ["NON OP cùng brand, cùng tháng: MIX FOOD - 05/2026"]
    assert "gợi ý: NON OP" in "\n".join(worker._report([p])) and "MIX FOOD" not in "\n".join(worker.summary([p]))


@pytest.mark.parametrize("h, action, reason", [
    ("NON OP", Action.DRAFT, None), ("Non OP", Action.DRAFT, None), ("OP", Action.SKIP, "NOT_FB_NON_OP"),
    ("", Action.REVIEW, "OP_COLUMN_BLANK"), ("x", Action.REVIEW, "OP_COLUMN_UNKNOWN")])
def test_column_h_non_op_op_or_review(run, h, action, reason):
    [p] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS", op=h))
    assert p.action is action and p.reasons == ([reason] if reason else [])


def test_scope_starts_august_2026(run):
    from app.recon.rules import SCOPE_FROM
    assert SCOPE_FROM == date(2026, 8, 1)
    plans = run(row(*ORG), row("ORGANICA", "BDAY 2026", "", "EZS"), row("SHISEIDO", "THÁNG 9", "30/09/2026", "SASSY"),
                scope_from=SCOPE_FROM)
    by = {p.rows[0]: (p.action, p.reasons) for p in plans}
    assert by[7] == (Action.SKIP, ["BEFORE_SCOPE"])                   # BDAY - 05/2026: nghiệm thu tay
    assert by[8] == (Action.SKIP, ["BEFORE_SCOPE"])                   # không có tháng, G trống
    assert by[9][1] != ["BEFORE_SCOPE"]                               # không có tháng nhưng G = 30/09 -> trong phạm vi


@pytest.mark.parametrize("status, action, reason", [
    ("", Action.DRAFT, None), ("Brand chưa gửi data", Action.DRAFT, None),
    ("Đã Confirm số + Chờ brand gửi BBNT", Action.DRAFT, None),
    ("Đã done BBNT + Chờ KT xuất HĐ nháp", Action.SKIP, "BBNT_ALREADY_DONE"),
    ("Đã xuất hóa đơn + Chờ tiền", Action.SKIP, "BBNT_ALREADY_DONE"),
    ("Chờ xử lý", Action.REVIEW, "STATUS_UNKNOWN")])
def test_only_rows_before_bbnt_are_drafted(run, status, action, reason):
    [p] = run(row(*ORG, status=status))
    assert p.action is action and p.reasons == ([reason] if reason else [])


def test_merged_line_note_goes_to_review(run):
    [p] = run(row(*ORG, notes="GỘP LINE 1389"))
    assert (p.action, p.reasons) == (Action.REVIEW, ["MERGED_LINE"]) and p.hints == ["NOTES = 'GỘP LINE 1389'"]


@pytest.mark.parametrize("gmv, action, reason", [
    (None, Action.SKIP, "NO_REVENUE"), (0, Action.SKIP, "NO_REVENUE"),
    (7_200_000, Action.DRAFT, None), (5, Action.REVIEW, "FIGURES_DIFFER_FBJT")])
def test_only_rows_with_revenue_in_column_m(run, gmv, action, reason):
    [p] = run(row(*ORG, gmv=gmv), require_revenue=True)
    assert p.action is action and p.reasons == ([reason] if reason else [])
