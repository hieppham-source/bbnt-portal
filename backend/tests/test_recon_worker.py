import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.config import DEFAULT_TEMPLATES_DIR
from app.master.repository import MasterRepository
from app.recon import worker
from app.recon.bbnt_job import CaseBbnt, ContractRow, make_bbnt
from app.recon.rules import CellAction
from app.recon.worker import Action, OrderLink, plan_cases
from app.services.templates import TemplateRegistry

from .conftest import MASTER_XLSX, ORDER_URL_FB, ORDER_URL_SHISEIDO, ORGANICA_MAY, SHISEIDO_Q3
from .test_recon_rules import HEADER

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
CONTRACTS = [ContractRow("01/2026/HĐDV/EZS-ORGANICA", "ORGANICA", "EZS", "FB", "Đã ký"),
             ContractRow("05/2026/HĐDV/SS-SHISEIDO", "SHISEIDO_GRP", "SASSY", "FB", "Đã ký")]
LINKS = [OrderLink("Organica", "BDAY - 05/2026", ORDER_URL_FB),
         OrderLink("SHISEIDO", "Q3 - 09/2026", ORDER_URL_SHISEIDO),
         OrderLink("ANESSA", "Q3 - 09/2026", ORDER_URL_SHISEIDO)]
ORDERS = {ORDER_URL_FB: ORGANICA_MAY, ORDER_URL_SHISEIDO: SHISEIDO_Q3}


def row(brand, campaign, end, entity, *, group="", op="", nmv=None, k=None, gmv=None):
    r = [""] * len(HEADER)
    r[0], r[1], r[2], r[3], r[5], r[6], r[7] = "10/2026", group, brand, campaign, entity, end, op
    r[8], r[10], r[12] = nmv, k, gmv
    return r


def sheet(*rows):
    return [[], [], [], [], [], HEADER, *rows]


@pytest.fixture
def run(master_tables):
    master = MasterRepository(lambda: master_tables, ttl_seconds=60).get()
    templates = TemplateRegistry(DEFAULT_TEMPLATES_DIR)

    def go(*rows, contracts=CONTRACTS, today=TODAY):
        return plan_cases(sheet(*rows), LINKS, contracts, master, templates, worker._fixture_opener(ORDERS),
                          today=today, now=NOW)
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
    assert (op.action, op.reasons) == (Action.REVIEW, ["NOT_FB_NON_OP"])
    [nc] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS"), contracts=[])
    assert (nc.action, nc.reasons) == (Action.REVIEW, ["CONTRACT_NOT_UNIQUE"])


def test_cli_dry_run_prints_plan(tmp_path, capsys):
    fixture = {"fbjt": sheet(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "EZS")),
               "links": [vars(x) for x in LINKS], "contracts": [vars(x) for x in CONTRACTS], "orders": ORDERS}
    f = tmp_path / "fixture.json"
    f.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    assert worker.main(["dry-run", "--fixture", str(f), "--master-file", str(MASTER_XLSX),
                        "--today", "2026-10-28"]) == 0
    out = capsys.readouterr().out
    assert "[DRAFT] dòng 7 · ORGANICA · BDAY - 05/2026" in out and "commission:" in out
