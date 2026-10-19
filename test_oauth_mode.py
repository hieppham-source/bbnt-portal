"""Backend đọc Google Sheet bằng OAuth2 access token của người dùng (không service account)."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.config import Settings
from app.errors import BbntError
from app.google_token import SHEETS_SCOPE, AccessTokenVerifier
from app.main import create_app
from app.services import sheets as sheets_mod
from app.services.generator import BbntGenerator
from app.services.sheets import (
    GoogleSheetsReader,
    OrderWorkbook,
    SheetAccessError,
    SheetAuthError,
    parse_sheet_url,
)

from .conftest import MASTER_XLSX, ORDER_URL_FB, ORGANICA_MAY, sheet_id, vn_clock

MASTER_URL = "https://docs.google.com/spreadsheets/d/1MasterSheetIdAbCdEfGhIjKlMnOp/edit"
USER = {"X-User-Email": "hiep.pham@hannaholala.com"}
BODY = {"channel": "FB", "entity": "EZS", "brand_codes": ["ORGANICA"], "period_months": ["05.26"],
        "campaign_name": "BDAY", "order_sheet_url": ORDER_URL_FB}


class FakeUserReader:
    """Giả GoogleSheetsReader.from_access_token: chỉ token 'good' đọc được; 'expired' -> 401 của Google."""

    tokens: list[str] = []

    def __init__(self, token: str, master_tables: dict[str, list[list[Any]]]) -> None:
        self.token, self.master = token, master_tables
        self.user_mode = True
        FakeUserReader.tokens.append(token)

    def _guard(self) -> None:
        if self.token == "expired":
            raise SheetAuthError("Phiên Google hết hạn — đăng nhập lại")
        if self.token != "good":
            raise SheetAccessError("Tài khoản của bạn không mở được sheet")

    def titles(self, ref: Any) -> list[str]:
        self._guard()
        return list(self.master)

    def read_tabs(self, ref: Any, titles: list[str]) -> dict[str, list[list[Any]]]:
        return {t: self.master[t] for t in titles}

    def read(self, ref: Any, *, sheet_name: str | None = None) -> list[list[Any]]:
        self._guard()
        assert ref.spreadsheet_id == sheet_id(ORDER_URL_FB)
        return [list(r) for r in ORGANICA_MAY]

    def open(self, ref: Any) -> OrderWorkbook:
        self._guard()
        assert ref.spreadsheet_id == sheet_id(ORDER_URL_FB)
        return OrderWorkbook("google_sheet", ["ORDER"], lambda t: [list(r) for r in ORGANICA_MAY])


@pytest.fixture
def client(master_tables, monkeypatch):
    FakeUserReader.tokens = []
    monkeypatch.setattr(GoogleSheetsReader, "from_access_token",
                        classmethod(lambda cls, token, **kw: FakeUserReader(token, master_tables)))
    settings = Settings.from_env({"MASTER_SHEET_URL": MASTER_URL, "DEV_AUTH_HEADER": "true",
                                  "CORS_ALLOWED_ORIGINS": "http://localhost:3000"})
    app = create_app(settings)
    app.dependency_overrides[deps.get_generator] = lambda: BbntGenerator(deps.templates_for(settings),
                                                                          vn_clock(2026, 6, 3))
    return TestClient(app)


def test_requires_user_google_token(client):
    r = client.post("/api/v1/bbnt/preview", json=BODY, headers=USER)
    assert r.status_code == 401 and r.json()["code"] == "GOOGLE_TOKEN_MISSING"
    assert client.get("/readyz").json()["master"] == "chưa tải"         # chưa ai đăng nhập: không coi là lỗi


def test_reads_master_and_order_with_user_token(client):
    h = {**USER, "X-Google-Access-Token": "good"}
    r = client.post("/api/v1/bbnt/preview", json=BODY, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["bbnt_number"] == "06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26"
    assert FakeUserReader.tokens == ["good", "good"]                     # 1 lần cho master, 1 lần cho order
    assert client.get("/readyz").json()["master"] == "ok"                # master đã vào cache
    assert client.get("/api/v1/master", headers=h).json()["channels"] == ["FB", "TTS"]


def test_expired_and_forbidden_tokens(client):
    r = client.get("/api/v1/master?refresh=true", headers={**USER, "X-Google-Access-Token": "expired"})
    assert r.status_code == 401 and r.json()["code"] == "GOOGLE_TOKEN_EXPIRED"
    r = client.get("/api/v1/master?refresh=true", headers={**USER, "X-Google-Access-Token": "other"})
    assert r.status_code == 502 and "không mở được" in r.json()["message"]


def test_cors_allows_google_token_header(client):
    r = client.options("/api/v1/bbnt/preview", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-google-access-token"})
    assert r.status_code == 200


class _Resp:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status_code, self._payload = status, payload or {}

    def json(self) -> Any:
        return self._payload


@pytest.mark.parametrize("status, exc, text", [
    (401, SheetAuthError, "đăng nhập lại"),
    (403, SheetAccessError, "share quyền Người xem cho email của bạn"),
    (404, SheetAccessError, "kiểm tra link"),
])
def test_reader_error_messages_in_user_mode(status, exc, text):
    reader = sheets_mod.GoogleSheetsReader("t")
    assert reader._session.headers["Authorization"] == "Bearer t"
    reader._session.get = lambda *a, **k: _Resp(status)
    with pytest.raises(exc, match=text):
        reader.read(parse_sheet_url(ORDER_URL_FB))


def test_reader_reads_values_with_user_token():
    reader = sheets_mod.GoogleSheetsReader("t")
    calls: list[str] = []

    def get(url: str, **kw: Any) -> _Resp:
        calls.append(url)
        if url.endswith("/values/%27ORDER%27"):
            return _Resp(200, {"values": [["a"]]})
        return _Resp(200, {"sheets": [{"properties": {"sheetId": 0, "title": "ORDER"}}]})

    reader._session.get = get
    assert reader.read(parse_sheet_url(ORDER_URL_FB)) == [["a"]]
    assert len(calls) == 2


def test_production_without_service_account_is_valid():
    s = Settings.from_env({"APP_ENV": "production", "MASTER_SHEET_URL": MASTER_URL,
                           "GOOGLE_OAUTH_CLIENT_ID": "x.apps.googleusercontent.com",
                           "ALLOWED_EMAIL_DOMAINS": "hannaholala.com",
                           "CORS_ALLOWED_ORIGINS": "https://bbnt.hannaholala.com"})
    assert s.is_production


def test_master_file_fixture_exists():
    assert MASTER_XLSX.is_file()


# =========================================================================== kiểm token qua tokeninfo
CLIENT = "123-abc.apps.googleusercontent.com"


def _info(**over: Any) -> dict[str, Any]:
    base = {"aud": CLIENT, "azp": CLIENT, "email": "Hiep.Pham@hannaholala.com", "email_verified": "true",
            "scope": "openid https://www.googleapis.com/auth/userinfo.email " + SHEETS_SCOPE, "expires_in": "3599"}
    base.update(over)
    return base


def _verifier(responses: dict[str, tuple[int, dict[str, Any]]], now: list[float]) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def fetch(token: str) -> tuple[int, dict[str, Any]]:
        calls.append(token)
        return responses[token]
    return AccessTokenVerifier([CLIENT], fetch=fetch, clock=lambda: now[0]), calls


def test_access_token_verified_and_cached_until_expiry():
    now = [1000.0]
    v, calls = _verifier({"good": (200, _info())}, now)
    assert v.verify("good", "hiep.pham@hannaholala.com").email == "hiep.pham@hannaholala.com"
    v.verify("good", "hiep.pham@hannaholala.com")
    assert calls == ["good"]                                             # gọi Google 1 lần/token
    now[0] += 3600
    v.verify("good", "hiep.pham@hannaholala.com")
    assert calls == ["good", "good"]                                     # hết hạn -> hỏi lại


@pytest.mark.parametrize("status, info, code", [
    (400, {"error": "invalid_token"}, "GOOGLE_TOKEN_EXPIRED"),
    (200, _info(aud="other-app.apps.googleusercontent.com", azp="other"), "GOOGLE_TOKEN_WRONG_APP"),
    (200, _info(scope="openid email"), "GOOGLE_SCOPE_MISSING"),
    (200, _info(expires_in="0"), "GOOGLE_TOKEN_EXPIRED"),
    (200, _info(email_verified="false"), "GOOGLE_TOKEN_EXPIRED"),
])
def test_access_token_rejections(status, info, code):
    v, _ = _verifier({"t": (status, info)}, [0.0])
    with pytest.raises(BbntError) as e:
        v.verify("t", "hiep.pham@hannaholala.com")
    assert e.value.code == code and e.value.status_code == 401


def test_access_token_of_another_user_rejected():
    v, _ = _verifier({"t": (200, _info(email="someone@hannaholala.com"))}, [0.0])
    with pytest.raises(BbntError) as e:
        v.verify("t", "hiep.pham@hannaholala.com")
    assert e.value.code == "GOOGLE_TOKEN_WRONG_USER"


def test_api_checks_access_token_owner(client):
    """Qua API: token của người khác bị chặn trước khi gọi Google Sheets."""
    v, _ = _verifier({"good": (200, _info(email="someone@hannaholala.com"))}, [0.0])
    client.app.dependency_overrides[deps.get_access_token_verifier] = lambda: v
    r = client.post("/api/v1/bbnt/preview", json=BODY, headers={**USER, "X-Google-Access-Token": "good"})
    assert r.status_code == 401 and r.json()["code"] == "GOOGLE_TOKEN_WRONG_USER"
    assert FakeUserReader.tokens == []


def test_xlsx_on_drive_gives_clear_message():
    reader = sheets_mod.GoogleSheetsReader("t")
    resp = _Resp(400)
    resp.text = '{"error":{"code":400,"message":"This operation is not supported for this document"}}'
    reader._session.get = lambda *a, **k: resp
    with pytest.raises(SheetAccessError, match="Excel"):
        reader.read(parse_sheet_url(ORDER_URL_FB))
