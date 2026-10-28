from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.recon.audit_gate import (
    AcceptanceFacts,
    EmailKind,
    PaymentReminderFacts,
    SendMode,
    evaluate_acceptance,
    evaluate_payment_reminder,
    review_only,
)
from app.recon.fbjt import SchemaError, col_letter, map_header, parse_rows, row_key
from app.recon.rules import (
    BusinessCalendar,
    CellAction,
    acceptance_send_date,
    allocate_total,
    campaign_program_name,
    campaign_suffix,
    confirm_deadline,
    invoices_total,
    late_fee,
    parse_invoices,
    payment_due,
    plan_cell,
    reminder_due,
)
from app.recon.state_machine import (
    InvalidTransition,
    Stage,
    State,
    Trigger,
    resume,
    stage_from_status,
    transition,
)

NOW = datetime(2026, 10, 27, 9, tzinfo=UTC)


# --- đuôi số BBNT (bảng 12.3 là test cố định) ------------------------------------------------------------
@pytest.mark.parametrize("campaign,suffix", [
    ("MIX - 07/2026", "MIX.0726"), ("HAIR - 07/2026", "HAIR.0726"), ("KBEAUTY - 07/2026", "KBEAUTY.0726"),
    ("MIX DERMA - 07/2026", "MD.0726"), ("J BEAUTY - 07/2026", "JB.0726"),
    ("TEFAL EXCLUSIVE - 01/2026", "TE.0126"), ("AMORE SBD - 01/2026", "AS.0126"), ("mix - 7/2026", "MIX.0726"),
])
def test_campaign_suffix(campaign: str, suffix: str) -> None:
    assert campaign_suffix(campaign) == suffix


@pytest.mark.parametrize("bad", ["", "MIX 07/2026", "MIX - 13/2026", "MIX - 07/26", "- 07/2026"])
def test_campaign_suffix_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        campaign_suffix(bad)
    with pytest.raises(ValueError):
        campaign_program_name(bad)


@pytest.mark.parametrize("campaign, name", [
    ("MIX - 07/2026", "MIX 0726"), ("KBEAUTY - 07/2026", "KBEAUTY 0726"), ("Mix Derma - 7/2026", "MIX DERMA 0726"),
    ("J BEAUTY - 12/2026", "J BEAUTY 1226"),
])
def test_campaign_program_name(campaign: str, name: str) -> None:
    assert campaign_program_name(campaign) == name


# --- hoá đơn ---------------------------------------------------------------------------------------------
def test_parse_invoices_formats() -> None:
    note = "HĐ: 1358 (08/10/2026) ; HD: 1377 (15/10/2026) hđ:179 (07/10/2026) HĐ: 9 (31/02/2026)"
    got = [(i.number, i.issued_on) for i in parse_invoices(note)]
    assert got == [("1358", date(2026, 10, 8)), ("1377", date(2026, 10, 15)), ("179", date(2026, 10, 7))]
    assert parse_invoices(None) == [] and parse_invoices("08/06/2026 - trả BBNT => Trang 08/10") == []


def test_invoices_total_merges_same_number() -> None:
    assert invoices_total([("1330", Decimal(10)), ("1330", Decimal(5)), ("1331", Decimal(1))]) == {
        "1330": Decimal(15), "1331": Decimal(1)}


# --- lịch & mốc thời gian --------------------------------------------------------------------------------
def test_business_days_skip_weekend_and_holidays() -> None:
    cal = BusinessCalendar(frozenset({date(2026, 10, 30)}))
    assert not cal.is_business_day(date(2026, 10, 31))          # thứ 7
    assert not cal.is_business_day(date(2026, 9, 2))            # lễ dương lịch cố định
    assert not cal.is_business_day(date(2026, 10, 30))          # lễ đã duyệt
    # T3 27/10 + 3 ngày làm việc: 28, 29, (30 lễ), (31, 1 cuối tuần), 2/11
    assert confirm_deadline(date(2026, 10, 27), cal) == date(2026, 11, 2)
    assert BusinessCalendar().add_business_days(date(2026, 10, 31), 1) == date(2026, 11, 2)   # bắt đầu thứ 7


def test_payment_due_and_acceptance_date() -> None:
    assert payment_due(date(2026, 10, 8), BusinessCalendar()) == date(2026, 10, 22)
    assert acceptance_send_date(date(2026, 7, 30)) == date(2026, 8, 24)


def test_late_fee_and_reminder_cycle() -> None:
    assert late_fee(Decimal(17268768), 12) == Decimal(103613)
    assert late_fee(Decimal(17268768), 0) == 0 and late_fee(Decimal(0), 5) == 0
    assert reminder_due(None, date(2026, 10, 27))
    assert not reminder_due(date(2026, 10, 25), date(2026, 10, 27))
    assert reminder_due(date(2026, 10, 24), date(2026, 10, 27))


# --- chia K group, ghi ô ---------------------------------------------------------------------------------
def test_allocate_total_puts_remainder_on_largest_nmv() -> None:
    raw = {"AESTURA": Decimal("100.4"), "INNISFREE": Decimal("200.4")}
    out = allocate_total(Decimal(302), raw, {"AESTURA": Decimal(1), "INNISFREE": Decimal(9)})
    assert out == {"AESTURA": Decimal(100), "INNISFREE": Decimal(202)}
    assert sum(out.values()) == 302


def test_plan_cell() -> None:
    assert plan_cell(None, Decimal(5)) is CellAction.WRITE
    assert plan_cell(Decimal(5), Decimal("5.0")) is CellAction.NOOP
    assert plan_cell(Decimal(6), Decimal(5)) is CellAction.REVIEW
    assert plan_cell(None, Decimal(5), is_formula=True) is CellAction.REVIEW


# --- FB.JT -----------------------------------------------------------------------------------------------
HEADER = ["Month", "GROUP BRAND", "BRAND", "CAMPAIGN (KHỚP HARAVAN)", "Ngày bắt đầu camp", "BÊN B",
          "Ngày kết thúc camp", "OP", "REAL REV\n(NMV)", "% \nCancel\nRate", "COMISSION", "%", "HRV GMV", "PIC",
          "NOTES - Ghi chú", "TITLE MAIL", "BBNT\nStatus", "Ngày \ncập nhật status", "Ngày xuất\nHóa Đơn\nNHÁP",
          "Oanh note tình trạng hđ VAT và thanh toán", "Status", "Hiếu \nasks\nPIC", "CHECK", "CHECK", "Status"]


def _sheet(*rows: list[object]) -> list[list[object]]:
    return [[], [], [], [], [], HEADER, *rows]


def test_map_header_uses_first_status_column_u() -> None:
    cols = map_header(HEADER)
    assert cols.letter("status") == "U" and cols.letter("op") == "H" and cols.letter("camp_end") == "G"
    assert [cols.letter(c) for c in ("nmv", "commission", "gmv")] == ["I", "K", "M"]
    assert col_letter(26) == "AA"


def test_map_header_missing_or_duplicated() -> None:
    with pytest.raises(SchemaError, match="thiếu cột 'op'"):
        map_header([h for h in HEADER if h != "OP"])
    with pytest.raises(SchemaError, match="xuất hiện 2 lần"):
        map_header([*HEADER, "BRAND"])


def test_parse_rows_skips_done_blank_and_bad_rows() -> None:
    base = ["7", "AMORE", "AESTURA", "MIX - 07/2026", "", "Cty EZS", "30/7/26", "", "72,680,000", "", "17268768",
            "", 77090000, "THANH NHÃ", "", "AMORE X EZS - FB", "", "", "", "HĐ: 1334 (08/10/2026)"]
    out = parse_rows(_sheet(
        [*base, "Đã xuất hóa đơn + Chờ tiền", "", "", "", "Done"],
        [*base[:20], "Done trả tiền"],
        ["7", "X", ""],                                            # không có brand
        [*base[:8], "abc", *base[9:], ""],                         # NMV không đọc được
    ))
    assert [r.row_number for r in out.rows] == [7]
    r = out.rows[0]
    assert (r.nmv, r.commission, r.gmv, r.camp_end) == (Decimal(72680000), Decimal(17268768), Decimal(77090000),
                                                         date(2026, 7, 30))
    assert r.status == "Đã xuất hóa đơn + Chờ tiền" and r.is_non_op and not r.is_done
    assert [i[0] for i in out.issues] == [10]
    assert row_key(" Aestura ", "MIX -  07/2026") == ("aestura", "mix - 07/2026")


def test_parse_rows_requires_header_row() -> None:
    with pytest.raises(SchemaError):
        parse_rows([["Month"]])


# --- state machine ---------------------------------------------------------------------------------------
def test_transitions() -> None:
    t = transition(State.WAITING, State.BBNT_DRAFTED, trigger=Trigger.WORKER, actor="worker", at=NOW)
    assert (t.from_state, t.to_state) == (State.WAITING, State.BBNT_DRAFTED)
    with pytest.raises(InvalidTransition):                      # không nhảy cóc sang gửi
        transition(State.WAITING, State.ACCEPTANCE_SENT, trigger=Trigger.WORKER, actor="worker", at=NOW)
    with pytest.raises(InvalidTransition):                      # bot không tự kết thúc
        transition(State.AWAITING_PAYMENT, State.DONE, trigger=Trigger.EMAIL, actor="worker", at=NOW)
    assert transition(State.AWAITING_PAYMENT, State.DONE, trigger=Trigger.SHEET, actor="sheet", at=NOW)
    with pytest.raises(InvalidTransition):
        transition(State.DONE, State.WAITING, trigger=Trigger.USER, actor="kam@x", at=NOW)
    with pytest.raises(InvalidTransition):                      # chỉ người mới Giữ
        transition(State.WAITING, State.ON_HOLD, trigger=Trigger.WORKER, actor="worker", at=NOW)
    assert resume(State.AWAITING_PAYMENT, actor="kam@x", at=NOW).to_state is State.AWAITING_PAYMENT


def test_stage_from_status() -> None:
    assert stage_from_status("") is Stage.PRE_INVOICE
    assert stage_from_status(" đã xuất hóa đơn nháp + chờ brand confirm") is Stage.INVOICE_DRAFT
    assert stage_from_status("Đã xuất hóa đơn + Chờ tiền") is Stage.INVOICE_ISSUED
    assert stage_from_status("Done") is Stage.DONE
    assert stage_from_status("Close camp") is None


# --- audit gate ------------------------------------------------------------------------------------------
GOOD_ACCEPTANCE = AcceptanceFacts(
    non_op=True, order_links_found=1, order_link_readable=True, contracts_found=1, contract_in_portal_master=True,
    generator_errors=0, figures_match_sheet=True, brand_emails=2, loopmail_threads_found=1, already_sent=False,
    template_configured=True, within_send_window=True, on_hold=False)


def test_acceptance_auto_when_all_pass() -> None:
    r = evaluate_acceptance(GOOD_ACCEPTANCE)
    assert r.passed and r.mode is SendMode.AUTO


@pytest.mark.parametrize("change,code", [
    ({"non_op": False}, "NOT_FB_NON_OP"), ({"order_links_found": 2}, "ORDER_LINK_NOT_UNIQUE"),
    ({"contracts_found": 0}, "CONTRACT_NOT_UNIQUE"), ({"generator_errors": 1}, "BBNT_GENERATOR_ERRORS"),
    ({"figures_match_sheet": False}, "FIGURES_MISMATCH_FBJT"), ({"brand_emails": 0}, "NO_BRAND_EMAIL"),
    ({"loopmail_threads_found": 2}, "LOOPMAIL_NOT_UNIQUE"), ({"already_sent": True}, "ALREADY_SENT"),
])
def test_acceptance_goes_to_review(change: dict[str, object], code: str) -> None:
    r = evaluate_acceptance(AcceptanceFacts(**{**GOOD_ACCEPTANCE.__dict__, **change}))  # type: ignore[arg-type]
    assert code in r.reasons and r.mode is SendMode.REVIEW


def test_missing_template_blocks_even_auto_kinds() -> None:
    r = evaluate_acceptance(AcceptanceFacts(**{**GOOD_ACCEPTANCE.__dict__, "template_configured": False}))
    assert r.mode is SendMode.BLOCKED
    assert review_only(EmailKind.CONFIRM_BBNT_REMINDER, template_configured=True).mode is SendMode.REVIEW


def _payment(**change: object) -> PaymentReminderFacts:
    base: dict[str, object] = dict(
        non_op=True, status_is_invoice_issued=True, invoices_parsed=True, due_date=date(2026, 10, 22),
        today=date(2026, 10, 27), commission_positive_and_matched=True, loopmail_known=True,
        last_reminder=date(2026, 10, 23), brand_replied_since_last=False, template_configured=True, on_hold=False)
    return PaymentReminderFacts(**{**base, **change})  # type: ignore[arg-type]


def test_payment_reminder_rules() -> None:
    assert evaluate_payment_reminder(_payment()).mode is SendMode.AUTO
    assert "NOT_OVERDUE" in evaluate_payment_reminder(_payment(today=date(2026, 10, 22))).reasons
    assert "REMINDED_RECENTLY" in evaluate_payment_reminder(_payment(last_reminder=date(2026, 10, 25))).reasons
    assert "BRAND_REPLIED" in evaluate_payment_reminder(_payment(brand_replied_since_last=True)).reasons
    assert "ON_HOLD" in evaluate_payment_reminder(_payment(on_hold=True)).reasons
