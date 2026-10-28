"""Kết nối Google của bot (plan bước 5) — dữ liệu giả lập, không gọi Google."""
from typing import Any

import pytest

from app.recon import worker
from app.recon.bbnt_job import ContractRow
from app.recon.google_bot import (
    BotConfigError,
    BotEnv,
    BotSheets,
    chip_url,
    parse_brand_op,
    parse_contracts,
    parse_order_links,
    refresh_access_token,
)
from app.recon.rules import entity_code
from app.recon.worker import Action, OrderLink

from . import test_recon_worker as tw
from .test_recon_worker import CONTRACTS, LINKS, ORG, row

run = tw.run   # dùng lại fixture của worker

ENV = {"RECON_GOOGLE_CLIENT_ID": "cid", "RECON_GOOGLE_CLIENT_SECRET": "FAKE-SECRET-123",
       "RECON_GOOGLE_REFRESH_TOKEN": "FAKE-REFRESH-456",
       "RECON_FBJT_ID": "fbjt", "RECON_ORDER_LINKS_ID": "links",
       "MASTER_SHEET_URL": "https://docs.google.com/spreadsheets/d/1MasterAbCdEfGhIjKlMnOpQrSt/edit"}


class Resp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code, self._body = status, body
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._body


class Session:
    def __init__(self, resp: Resp) -> None:
        self.resp, self.calls, self.headers = resp, [], {}

    def post(self, url: str, **kw: Any) -> Resp:
        self.calls.append((url, kw))
        return self.resp

    def get(self, url: str, **kw: Any) -> Resp:
        self.calls.append((url, kw))
        return self.resp


def test_env_reports_missing_names_only():
    with pytest.raises(BotConfigError, match="RECON_GOOGLE_REFRESH_TOKEN") as e:
        BotEnv.from_env({**ENV, "RECON_GOOGLE_REFRESH_TOKEN": " "})
    assert "FAKE-SECRET-123" not in str(e.value)


def test_refresh_access_token_ok_and_error_hides_secrets():
    env = BotEnv.from_env(ENV)
    s = Session(Resp(200, {"access_token": "AT"}))
    assert refresh_access_token(env, s) == "AT"
    assert s.calls[0][1]["data"]["grant_type"] == "refresh_token"
    with pytest.raises(BotConfigError, match="invalid_grant") as e:
        refresh_access_token(env, Session(Resp(400, {"error": "invalid_grant"})))
    assert "FAKE-REFRESH-456" not in str(e.value) and "FAKE-SECRET-123" not in str(e.value)


@pytest.mark.parametrize("text, code", [("Cty EZS", "EZS"), ("EZS", "EZS"), ("HKD SASSY ", "SASSY"),
                                        ("SASSY", "SASSY"), ("", None), ("Công ty khác", None)])
def test_entity_code(text, code):
    assert entity_code(text) == code


DATA_CONTRACT = [
    [],
    ["GROUP BRAND", "BRAND", "Email nhận hóa đơn", "STATUS", "SỐ HỢP ĐỒNG ", "NGÀY ĐẾN HẠN", "COMMISSION",
     "FINAL", "HÌNH THỨC HỢP TÁC", "PHÁP NHÂN A\n(BRAND)", "PHÁP NHÂN B\n(EZ/SS)"],
    ["SHISEIDO_GRP", "SHISEIDO", "", "Đã ký", "05/2026/HĐDV/SS-SHISEIDO", 46147, 0.15, 0.15, "NON OP", "A", "SASSY"],
    ["ORGANICA", "ORGANICA", "", "Hết hạn", "00/2025/OLD", 46000, 0.18, 0.18, "NON OP", "A", "Cty EZS"],
    ["", "", "", "", ""],
]


def test_parse_contracts_rows_for_brand_and_group():
    got = parse_contracts(DATA_CONTRACT)
    assert ContractRow("05/2026/HĐDV/SS-SHISEIDO", "SHISEIDO", "SASSY", "FB", "Đã ký") in got
    assert ContractRow("05/2026/HĐDV/SS-SHISEIDO", "SHISEIDO_GRP", "SASSY", "FB", "Đã ký") in got
    assert ContractRow("00/2025/OLD", "ORGANICA", "EZS", "FB", "Hết hạn") in got and len(got) == 3


def test_parse_contracts_missing_header():
    with pytest.raises(BotConfigError):
        parse_contracts([["BRAND", "STATUS"]])


def test_parse_brand_op():
    assert parse_brand_op([["GROUP", "BRAND", "PIC", "CMS", "OP"], ["G", "Organica", "x", 0.2, ""],
                           ["G", "OPBRAND", "x", 0.2, "OP"]]) == {"organica": "", "opbrand": "OP"}


def cell(text: str, uri: str | None = None, hyperlink: str | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"formattedValue": text}
    if uri:
        c["chipRuns"] = [{"chip": {"richLinkProperties": {"uri": uri}}}]
    if hyperlink:
        c["hyperlink"] = hyperlink
    return c


GRID = [
    {"values": [cell(h) for h in ("MONTH", "CAMP", "MASTER", "BRAND", "PIC", "ORDER link order sheet (ĐƯA BRAND)",
                                  "GỬI BRAND", "NOTE", "GROUP BRAND")]},
    {"values": [cell("JAN'26")]},
    {"values": [cell("ORGANICA"), cell("BDAY - 05/2026"), cell("x"), cell("Organica"), cell("PIC"),
                cell("BDAY 05", uri="https://docs.google.com/spreadsheets/d/1ChipAbCdEfGhIjKlMnOpQrSt/edit")]},
    {"values": [cell("X"), cell("MIX - 05/2026"), cell("x"), cell("Plain"), cell("PIC"),
                cell("link", hyperlink="https://docs.google.com/spreadsheets/d/1HyperAbCdEfGhIjKlMnOpQ/edit")]},
    {"values": [cell("X"), cell("MIX - 06/2026"), cell("x"), cell("Nolink")]},
]


def test_parse_order_links_chip_hyperlink_and_missing():
    assert parse_order_links(GRID) == [
        ("Organica", "BDAY - 05/2026", "https://docs.google.com/spreadsheets/d/1ChipAbCdEfGhIjKlMnOpQrSt/edit"),
        ("Plain", "MIX - 05/2026", "https://docs.google.com/spreadsheets/d/1HyperAbCdEfGhIjKlMnOpQ/edit"),
        ("Nolink", "MIX - 06/2026", ""),
    ]
    assert chip_url(cell("chỉ có chữ")) == "chỉ có chữ"      # worker -> LINK_INVALID


def test_grid_rows_request_and_error():
    s = Session(Resp(200, {"sheets": [{"data": [{"rowData": GRID}]}]}))
    assert BotSheets("AT", s).grid_rows("links", "NON OP 26") == GRID
    assert s.headers["Authorization"] == "Bearer AT" and "chipRuns" in s.calls[0][1]["params"]["fields"]
    with pytest.raises(BotConfigError, match="403"):
        BotSheets("AT", Session(Resp(403, {}))).grid_rows("links", "NON OP 26")


# ---------------------------------------------------------------------------------------------- worker + dữ liệu thật
def test_worker_accepts_real_entity_labels(run):
    [p] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "Cty EZS"))
    assert p.action is Action.DRAFT
    [p] = run(row("ORGANICA", "BDAY - 05/2026", "31/05/2026", "Công ty lạ"))
    assert p.reasons == ["ENTITY_UNKNOWN"]


def test_data_brand_op_blocks(master_tables, run):
    from app.config import DEFAULT_TEMPLATES_DIR
    from app.master.repository import MasterRepository
    from app.services.templates import TemplateRegistry

    from .test_recon_worker import NOW, ORDERS, TODAY, sheet
    plans = worker.plan_cases(sheet(row(*ORG)), LINKS, CONTRACTS, MasterRepository(lambda: master_tables).get(),
                              TemplateRegistry(DEFAULT_TEMPLATES_DIR), worker._fixture_opener(ORDERS),
                              today=TODAY, now=NOW, brand_op={"organica": "OP"})
    assert (plans[0].action, plans[0].reasons) == (Action.REVIEW, ["OP_CONFLICT_DATA_BRAND"])


def test_unreadable_order_sheet_goes_to_review(run):
    url = "https://docs.google.com/spreadsheets/d/1NoAccessAbCdEfGhIjKlMnOpQ/edit"
    [p] = run(row(*ORG), links=[OrderLink("ORGANICA", "BDAY - 05/2026", url)])
    assert (p.action, p.reasons) == (Action.REVIEW, ["ORDER_SHEET_UNREADABLE"])


def test_summary_has_no_brand_or_amount(run):
    plans = run(row(*ORG), row("NOLINK", "MIX - 07/2026", "31/07/2026", "EZS"))
    text = "\n".join(worker.summary(plans))
    assert "DRAFT: 1" in text and "SKIP: 1" in text and "LINK_NOT_FOUND: 1" in text
    assert "ORGANICA" not in text and "NOLINK" not in text and "." not in text


def test_live_without_env_fails_fast(monkeypatch, capsys):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert worker.main(["dry-run", "--live", "--summary"]) == 1
    assert "Thiếu biến môi trường" in capsys.readouterr().err
