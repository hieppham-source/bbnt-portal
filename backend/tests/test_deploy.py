"""Tests cho phần triển khai: cấu hình env, xác thực Google ID token, CORS, health, S3 storage, Alembic."""
from __future__ import annotations

import base64
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.auth import AuthError, GoogleIdTokenVerifier
from app.config import ConfigError, Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ID = "1234567890-abc.apps.googleusercontent.com"
FRONTEND = "https://bbnt.hannaholala.com"
MASTER_URL = "https://docs.google.com/spreadsheets/d/1MasterSheetIdAbCdEfGhIjKlMnOp/edit#gid=0"


def prod_env(**over: str) -> dict[str, str]:
    env = {
        "APP_ENV": "production",
        "MASTER_SHEET_URL": MASTER_URL,
        "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID,
        "ALLOWED_EMAIL_DOMAINS": "hannaholala.com",
        "CORS_ALLOWED_ORIGINS": f"{FRONTEND}/, https://bbnt-portal.vercel.app",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


# =========================================================================== config
class TestSettings:
    def test_valid_production(self):
        s = Settings.from_env(prod_env())
        assert s.is_production and s.master_sheet_url == MASTER_URL and s.master_cache_seconds == 60
        assert s.cors_allowed_origins == (FRONTEND, "https://bbnt-portal.vercel.app")   # bỏ '/' cuối

    def test_secrets_not_in_repr(self):
        r = repr(Settings.from_env(prod_env()))
        assert "PRIVATE KEY" not in r

    @pytest.mark.parametrize("over, problem", [
        ({"MASTER_SHEET_URL": ""}, "MASTER_SHEET_URL"),
        ({"MASTER_SHEET_URL": "https://example.com/x"}, "MASTER_SHEET_URL"),
        ({"MASTER_SHEET_URL": "", "MASTER_FILE": "master.xlsx"}, "MASTER_SHEET_URL bắt buộc ở production"),
        ({"MASTER_CACHE_SECONDS": "abc"}, "MASTER_CACHE_SECONDS"),
        ({"TEMPLATES_DIR": "/nope"}, "TEMPLATES_DIR"),
        ({"GOOGLE_OAUTH_CLIENT_ID": ""}, "GOOGLE_OAUTH_CLIENT_ID"),
        ({"ALLOWED_EMAIL_DOMAINS": ""}, "ALLOWED_EMAIL_DOMAINS"),
        ({"CORS_ALLOWED_ORIGINS": "*"}, "CORS_ALLOWED_ORIGINS"),
        ({"CORS_ALLOWED_ORIGINS": "http://bbnt.hannaholala.com"}, "CORS_ALLOWED_ORIGINS"),
        ({"DEV_AUTH_HEADER": "true"}, "DEV_AUTH_HEADER"),
        ({"APP_TIMEZONE": "UTC"}, "APP_TIMEZONE"),
    ])
    def test_production_rejects_bad_config(self, over, problem):
        with pytest.raises(ConfigError, match=problem):
            Settings.from_env(prod_env(**over))

    def test_development_is_lenient(self):
        s = Settings.from_env({"MASTER_FILE": "deploy/MASTER_BBNT_TEMPLATE.xlsx", "DEV_AUTH_HEADER": "true"})
        assert not s.is_production and s.dev_auth_header and s.master_sheet_url is None
        with pytest.raises(ConfigError, match="MASTER_SHEET_URL"):
            Settings.from_env({})

    def test_service_account_variables_are_ignored(self):
        """Không còn service account: biến cũ còn sót trên Render không ảnh hưởng gì."""
        s = Settings.from_env(prod_env(GOOGLE_SERVICE_ACCOUNT_JSON="anything"))
        assert not hasattr(s, "google_service_account")

    def test_client_id_format(self):
        with pytest.raises(ConfigError, match="apps.googleusercontent.com"):
            Settings.from_env(prod_env(GOOGLE_OAUTH_CLIENT_ID="GOCSPX-secret-pasted-by-mistake"))


# =========================================================================== Google ID token
@pytest.fixture(scope="module")
def keypair():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    def make():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = dt.datetime.now(dt.UTC)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        return pem, cert.public_bytes(serialization.Encoding.PEM).decode()
    return {"k1": make(), "k2": make()}


def sign(keypair, kid: str = "k1", **claims: Any) -> str:
    from google.auth import crypt, jwt

    now = int(time.time())
    payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1001", "iat": now, "exp": now + 3600,
               "email": "hiep.pham@hannaholala.com", "email_verified": True, "hd": "hannaholala.com",
               "name": "Hiệp Phạm"}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    signer = crypt.RSASigner.from_string(keypair[kid][0], key_id=kid)
    return jwt.encode(signer, payload).decode()


class FakeCerts:
    def __init__(self, keypair, kids=("k1",)) -> None:
        self.keypair, self.kids, self.calls = keypair, list(kids), 0

    def __call__(self) -> tuple[dict[str, str], int]:
        self.calls += 1
        return {k: self.keypair[k][1] for k in self.kids}, 3600


def verifier(keypair, **kw: Any) -> GoogleIdTokenVerifier:
    return GoogleIdTokenVerifier((CLIENT_ID,), ("hannaholala.com",), kw.pop("emails", ()),
                                 fetch_certs=kw.pop("fetch", FakeCerts(keypair)), **kw)


class TestGoogleIdToken:
    def test_valid(self, keypair):
        u = verifier(keypair).verify(sign(keypair))
        assert u.email == "hiep.pham@hannaholala.com" and u.hosted_domain == "hannaholala.com"

    @pytest.mark.parametrize("claims, code", [
        ({"aud": "other-client.apps.googleusercontent.com"}, "INVALID_TOKEN"),
        ({"iss": "https://evil.example.com"}, "INVALID_TOKEN"),
        ({"exp": int(time.time()) - 600, "iat": int(time.time()) - 4200}, "TOKEN_EXPIRED"),
        ({"email": "someone@gmail.com", "hd": None}, "DOMAIN_NOT_ALLOWED"),
        ({"email": "x@hannaholala.com.evil.com", "hd": None}, "DOMAIN_NOT_ALLOWED"),
        ({"hd": "other.com"}, "DOMAIN_NOT_ALLOWED"),
        ({"email_verified": False}, "EMAIL_NOT_VERIFIED"),
    ])
    def test_rejections(self, keypair, claims, code):
        with pytest.raises(AuthError) as e:
            verifier(keypair).verify(sign(keypair, **claims))
        assert e.value.code == code

    def test_tampered_signature(self, keypair):
        token = sign(keypair)
        head, body, sig = token.split(".")
        forged = base64.urlsafe_b64encode(json.dumps({"email": "boss@hannaholala.com"}).encode()).decode().rstrip("=")
        with pytest.raises(AuthError):
            verifier(keypair).verify(f"{head}.{forged}.{sig}")

    def test_garbage(self, keypair):
        with pytest.raises(AuthError):
            verifier(keypair).verify("abc")

    def test_allowed_email_exception(self, keypair):
        v = verifier(keypair, emails=("ketoan.ngoai@gmail.com",))
        assert v.verify(sign(keypair, email="ketoan.ngoai@gmail.com", hd=None)).email == "ketoan.ngoai@gmail.com"

    def test_certs_cached_and_refreshed_on_key_rotation(self, keypair):
        fetch = FakeCerts(keypair, kids=("k1",))
        v = verifier(keypair, fetch=fetch)
        for _ in range(5):
            v.verify(sign(keypair, "k1"))
        assert fetch.calls == 1                       # cache theo max-age
        fetch.kids = ["k1", "k2"]                     # Google xoay key
        assert v.verify(sign(keypair, "k2")).email
        assert fetch.calls == 2


# =========================================================================== app: CORS, health, auth wiring
@pytest.fixture
def prod_client(keypair):
    settings = Settings.from_env(prod_env())
    app = create_app(settings)

    class EmptyRepo:
        has_default_source = False

        def peek(self) -> Any:
            return None

        def get(self, refresh: bool = False, source: Any = None) -> Any:
            from app.master.loader import load_master
            return load_master(lambda _: None)

    app.dependency_overrides[deps.get_token_verifier] = lambda: verifier(keypair)
    app.dependency_overrides[deps.get_master_repo] = lambda: EmptyRepo()

    class TrustingVerifier:
        def verify(self, token: str, email: str) -> None:
            assert token == "tok"

    app.dependency_overrides[deps.get_access_token_verifier] = lambda: TrustingVerifier()
    with TestClient(app) as c:
        yield c


def test_healthz_and_no_public_docs(prod_client):
    assert prod_client.get("/healthz").json() == {"status": "ok"}
    assert prod_client.get("/docs").status_code == 404
    assert prod_client.get("/openapi.json").status_code == 404


def test_cors_preflight_allowed_origin(prod_client):
    r = prod_client.options("/api/v1/bbnt/generate", headers={
        "Origin": FRONTEND, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == FRONTEND
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_unknown_origin(prod_client):
    r = prod_client.options("/api/v1/bbnt/generate", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers


def test_cors_exposes_download_headers(prod_client, keypair):
    r = prod_client.get("/api/v1/master/check",
                        headers={"Origin": FRONTEND, "Authorization": f"Bearer {sign(keypair)}"})
    assert r.headers["access-control-allow-origin"] == FRONTEND
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "content-disposition" in exposed and "x-bbnt-number" in exposed


def test_auth_required_and_dev_header_ignored_in_production(prod_client, keypair):
    url = "/api/v1/master/check"
    r = prod_client.get(url)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert prod_client.get(url, headers={"X-User-Email": "hiep.pham@hannaholala.com"}).status_code == 401
    assert prod_client.get(url, headers={"Authorization": "Bearer junk"}).status_code == 401
    bad = sign(keypair, email="x@gmail.com", hd=None)
    r = prod_client.get(url, headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "DOMAIN_NOT_ALLOWED"
    r = prod_client.get(url, headers={"Authorization": f"Bearer {sign(keypair)}"})
    assert r.status_code == 401 and r.json()["code"] == "GOOGLE_TOKEN_MISSING"   # qua ID token, thiếu access token
    r = prod_client.get(url, headers={"Authorization": f"Bearer {sign(keypair)}", "X-Google-Access-Token": "tok"})
    assert r.status_code == 200 and r.json()["ok"] is False             # master rỗng -> lỗi tab
