"""API stateless: master từ Sheet (giả lập), dán link order -> preview / tải .docx; server không lưu gì."""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import docx
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.config import Settings
from app.main import create_app
from app.master.repository import MasterRepository
from app.services.generator import DOCX_MIME, BbntGenerator
from app.services.templates import TemplateRegistry

from .conftest import (
    MASTER_XLSX,
    ORDER_URL_FB,
    ORDER_URL_SHISEIDO,
    FakeSheets,
    order_rows,
    sheet_id,
    vn_clock,
)

USER = {"X-User-Email": "hiep.pham@hannaholala.com"}
ORGANICA = {"channel": "FB", "entity": "EZS", "brand_codes": ["ORGANICA"], "period_months": ["05.26"],
            "campaign_name": "BDAY", "order_sheet_url": ORDER_URL_FB}
SHISEIDO = {"channel": "FB", "entity": "SASSY", "group_code": "SHISEIDO_GRP",
            "period_months": ["07.26", "08.26", "09.26"], "campaign_name": "Q3", "order_sheet_url": ORDER_URL_SHISEIDO}


class Env:
    def __init__(self, tables: dict[str, list[list[Any]]], sheets: FakeSheets) -> None:
        self.tables, self.sheets = tables, sheets
        self.clock = vn_clock(2026, 6, 3)
        self.master_reads = 0
        settings = Settings.from_env({"MASTER_FILE": str(MASTER_XLSX), "DEV_AUTH_HEADER": "true"})
        self.app = create_app(settings)

        def source() -> Any:
            self.master_reads += 1
            return self.tables

        self.repo = MasterRepository(source, ttl_seconds=60)
        self.templates = TemplateRegistry(settings.templates_dir)
        o = self.app.dependency_overrides
        o[deps.get_master_repo] = lambda: self.repo
        o[deps.get_sheet_reader] = lambda: self.sheets
        o[deps.get_templates] = lambda: self.templates
        o[deps.get_generator] = lambda: BbntGenerator(self.templates, lambda: self.clock())
        self.client = TestClient(self.app)

    def post(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        return self.client.post(path, json=body, headers=USER, params=params)


@pytest.fixture
def env(master_tables, fake_sheets) -> Env:
    return Env(master_tables, fake_sheets)


def docx_text(content: bytes) -> str:
    d = docx.Document(io.BytesIO(content))
    parts = [p.text for p in d.paragraphs]
    parts += [c.text for t in d.tables for r in t.rows for c in r.cells]
    return "\n".join(parts)


# =========================================================================== preview
def test_preview_organica(env):
    r = env.post("/api/v1/bbnt/preview", ORGANICA)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["bbnt_number"] == "06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26"
    assert b["file_name"] == "BBNT_06.26_BBNT-ORGANICA-EZ-FB-CAMP-05.26.docx"
    assert b["program_name"] == "ORGANICA BDAY 05.26" and b["issue_date"] == "2026-06-03"
    assert b["contract"]["contract_no"] == "01/2026/HĐDV/EZS-ORGANICA" and b["contract"]["master_row"] == 2
    f = {k: Decimal(str(v)) if isinstance(v, (int, float, str)) and k != "tax_mode" and k != "tax_label"
         and k != "total_payable_words" else v for k, v in b["figures"].items()}
    assert f["gmv_estimated"] == 6_500_000                      # Đã giao + hoàn + huỷ trong kỳ, đúng brand
    assert f["nmv"] == 3_500_000                                # chỉ Đã giao
    assert f["commission_amount"] == 630_000 and f["vat_amount"] == 50_400
    assert f["tax_deduction_amount"] == 0 and f["total_payable"] == 680_400
    assert b["figures"]["tax_label"] == "VAT 8%"
    assert b["figures"]["total_payable_words"].startswith("Sáu trăm tám mươi nghìn bốn trăm đồng")
    assert b["other_brand_line_count"] == 1                     # dòng Anessa bị loại
    assert {w["code"] for w in b["warnings"]} == {"RETURNED", "CANCELLED", "OUT_OF_PERIOD"}
    assert [i["product_name"] for i in b["items"]] == ["Serum Organica", "Kem dưỡng Organica"]


def test_preview_group_sassy(env):
    env.clock = vn_clock(2026, 10, 5)
    b = env.post("/api/v1/bbnt/preview", SHISEIDO).json()
    assert b["bbnt_number"] == "10.26/BBNT-SHISEIDO_GRP-SS-FB-CAMP-07-09.26"
    assert b["brands"] == ["SHISEIDO", "ANESSA"] and b["scope_code"] == "SHISEIDO_GRP"
    assert Decimal(b["figures"]["nmv"]) == 15_000_000
    assert Decimal(b["figures"]["commission_amount"]) == 2_250_000
    assert Decimal(b["figures"]["tax_deduction_amount"]) == 22_500
    assert Decimal(b["figures"]["vat_amount"]) == 0
    assert Decimal(b["figures"]["total_payable"]) == 2_227_500


# =========================================================================== generate
def test_generate_returns_docx_and_stores_nothing(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = env.post("/api/v1/bbnt/generate", ORGANICA)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == DOCX_MIME
    assert unquote(r.headers["x-bbnt-number"]) == "06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26"
    assert r.headers["x-bbnt-total-payable"] == "680400" and r.headers["x-bbnt-warnings"] == "3"
    assert "BBNT_06.26_BBNT-ORGANICA-EZ-FB-CAMP-05.26.docx" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    text = docx_text(r.content)
    for s in ("06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26", "ORGANICA BDAY 05.26", "01/2026/HĐDV/EZS-ORGANICA",
              "CÔNG TY TNHH ORGANICA (MẪU)", "0071000123456", "680.400", "Serum Organica"):
        assert s in text, s
    assert "{{" not in text
    assert list(tmp_path.iterdir()) == []                       # không ghi file nào ra đĩa


def test_redo_gives_same_number_and_rereads_order(env):
    a = env.post("/api/v1/bbnt/generate", ORGANICA)
    env.sheets.sheets[sheet_id(ORDER_URL_FB)].append(
        ["25/05/2026", "FB007", "Đã giao", "Organica", "ORG-01", "Serum Organica", 1, 1_000_000])
    b = env.post("/api/v1/bbnt/generate", ORGANICA)
    assert a.headers["x-bbnt-number"] == b.headers["x-bbnt-number"]     # làm lại -> cùng số
    assert (a.headers["x-bbnt-total-payable"], b.headers["x-bbnt-total-payable"]) == ("680400", "874800")
    assert len(env.sheets.calls) == 2                           # file order luôn đọc mới, không cache
    env.clock = vn_clock(2026, 7, 2)                            # lập sang tháng sau -> tiền tố tháng lập đổi
    c = env.post("/api/v1/bbnt/preview", ORGANICA).json()
    assert c["bbnt_number"] == "07.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26"


def test_master_edit_picked_up_after_refresh(env):
    assert env.post("/api/v1/bbnt/preview", ORGANICA).json()["contract"]["commission_rate"] == "0.18"
    h = env.tables["HOP_DONG"]
    h[1][h[0].index("% hoa hồng")] = "20%"
    assert env.post("/api/v1/bbnt/preview", ORGANICA).json()["contract"]["commission_rate"] == "0.18"   # cache
    b = env.post("/api/v1/bbnt/preview", ORGANICA, refresh="true").json()
    assert b["contract"]["commission_rate"] == "0.2" and Decimal(b["figures"]["commission_amount"]) == 700_000
    assert env.master_reads == 2


# =========================================================================== errors
@pytest.mark.parametrize("over, status, code", [
    ({"entity": "SASSY"}, 422, "CONTRACT_NOT_FOUND"),
    ({"period_months": ["12.25"]}, 422, "CONTRACT_PERIOD_MISMATCH"),
    ({"period_months": ["07.26"]}, 422, "INVALID_INPUT"),                       # kỳ sau tháng lập
    ({"brand_codes": ["ORGANICA", "ANESSA"]}, 422, "BRANDS_NOT_SAME_GROUP"),
    ({"brand_codes": ["XYZ"]}, 422, "BRAND_NOT_FOUND"),
    ({"order_sheet_url": "https://docs.google.com/spreadsheets/d/1NotSharedAbCdEfGhIjKlMn/edit"}, 502,
     "ORDER_SHEET_UNREADABLE"),
])
def test_business_errors(env, over, status, code):
    r = env.post("/api/v1/bbnt/generate", {**ORGANICA, **over})
    assert r.status_code == status, r.text
    assert r.json()["code"] == code


def test_no_delivered_orders(env):
    env.sheets.sheets[sheet_id(ORDER_URL_FB)] = order_rows([
        ["03/05/2026", "FB001", "Đã hủy", "Organica", "ORG-01", "Serum", 1, 100_000]])
    r = env.post("/api/v1/bbnt/generate", ORGANICA)
    assert r.status_code == 422 and r.json()["code"] == "NO_DELIVERED_ORDERS"


def test_sheet_data_errors_listed_by_row(env):
    env.sheets.sheets[sheet_id(ORDER_URL_FB)] = order_rows([
        ["03/05/2026", "FB001", "Đã giao", "Organica", "ORG-01", "Serum", 1, "abc"]])
    r = env.post("/api/v1/bbnt/generate", ORGANICA)
    body = r.json()
    assert r.status_code == 422 and body["code"] == "SHEET_DATA_ERRORS"
    assert body["details"][0]["row_number"] == 3


def test_mapping_mismatch(env):
    env.sheets.sheets[sheet_id(ORDER_URL_FB)] = [["Mã đơn", "Tiền"], ["X", 1]]
    r = env.post("/api/v1/bbnt/generate", ORGANICA)
    assert r.status_code == 422 and r.json()["code"] == "MAPPING_MISMATCH"


@pytest.mark.parametrize("bad", [
    {"brand_codes": None},
    {"group_code": "SHISEIDO_GRP"},                                          # cả brand lẫn group
    {"order_sheet_url": "https://example.com/sheet"},
    {"period_months": ["13.26"]},
    {"collected_on_behalf": -1},
    {"unknown": 1},
])
def test_payload_validation(env, bad):
    body = {k: v for k, v in {**ORGANICA, **bad}.items() if v is not None}
    assert env.post("/api/v1/bbnt/preview", body).status_code == 422


def test_auth_required(env):
    assert env.client.post("/api/v1/bbnt/generate", json=ORGANICA).status_code == 401
    assert env.client.get("/api/v1/master").status_code == 401


# =========================================================================== master endpoints
def test_master_options(env):
    b = env.client.get("/api/v1/master", headers=USER).json()
    assert b["channels"] == ["FB", "TTS"]
    assert {e["code"] for e in b["entities"]} == {"EZS", "SASSY"}
    assert {(g["channel"], g["code"]) for g in b["groups"]} == {("FB", "SHISEIDO_GRP")}
    assert next(g for g in b["groups"])["brand_codes"] == ["SHISEIDO", "ANESSA"]
    assert len(b["contracts"]) == 2 and b["mappings"][0]["is_default"] is True
    assert b["error_count"] == 0


def test_master_check_lists_issues(env):
    env.tables["BRAND"].append(["FB", "BAD CODE", "x"])
    b = env.client.get("/api/v1/master/check", headers=USER).json()
    assert b["ok"] is False
    assert b["issues"][0]["tab"] == "BRAND" and b["issues"][0]["row"] == 6


def test_readyz(env):
    r = env.client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "master": "ok", "templates": "ok"}


def test_app_uses_its_own_settings_without_overrides(monkeypatch):
    """create_app(settings) phải dùng đúng settings truyền vào (không đọc lại biến môi trường)."""
    monkeypatch.delenv("MASTER_SHEET_URL", raising=False)
    monkeypatch.delenv("MASTER_FILE", raising=False)
    settings = Settings.from_env({"MASTER_FILE": str(MASTER_XLSX), "DEV_AUTH_HEADER": "true",
                                  "CORS_ALLOWED_ORIGINS": "http://localhost:3000"})
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/master", headers=USER).json()["channels"] == ["FB", "TTS"]
    assert client.get("/readyz").json()["master"] == "ok"
    pre = client.options("/api/v1/master", headers={"Origin": "http://localhost:3000",
                                                    "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "x-user-email"})
    assert pre.status_code == 200                       # local dev: frontend gửi X-User-Email được


def test_order_without_brand_column_keeps_all_lines(env):
    """BRAND_NAME tuỳ chọn: file order không có cột Brand -> không lọc brand (trước đây loại hết dòng)."""
    header = ["Ngày đặt hàng", "Mã đơn hàng", "Trạng thái", "SKU", "Tên sản phẩm", "Số lượng",
              "Doanh thu sau voucher"]
    env.sheets.sheets[sheet_id(ORDER_URL_FB)] = [["BÁO CÁO"], header,
                                                 ["03/05/2026", "FB001", "Đã giao", "ORG-01", "Serum", 2, 1_000_000]]
    r = env.post("/api/v1/bbnt/preview", ORGANICA)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["other_brand_line_count"] == 0 and Decimal(str(b["figures"]["nmv"])) == 1_000_000
