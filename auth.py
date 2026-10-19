"""
Xác thực người dùng bằng Google ID token (OpenID Connect).

Frontend (Auth.js + Google provider) gửi ``Authorization: Bearer <id_token>``. Backend tự kiểm:

* chữ ký RSA của Google (cert công khai, cache theo ``Cache-Control: max-age``, tự tải lại khi Google xoay key)
* ``aud`` = OAuth Client ID của app, ``iss`` = accounts.google.com, ``exp`` còn hạn (lệch đồng hồ ≤ 60 giây)
* ``email_verified`` và email thuộc domain cho phép (``hd`` của Google Workspace hoặc đuôi email)

Backend KHÔNG cần OAuth client secret — secret chỉ nằm ở frontend để đổi code lấy token.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.auth import exceptions as gexc
from google.auth import jwt as gjwt

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
_MAX_AGE = re.compile(r"max-age=(\d+)")


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass(frozen=True)
class VerifiedUser:
    email: str
    name: str | None
    subject: str
    hosted_domain: str | None


CertFetcher = Callable[[], tuple[dict[str, str], int]]


def fetch_google_certs() -> tuple[dict[str, str], int]:
    import requests

    resp = requests.get(GOOGLE_CERTS_URL, timeout=10)
    resp.raise_for_status()
    m = _MAX_AGE.search(resp.headers.get("Cache-Control", ""))
    certs: dict[str, str] = resp.json()
    return certs, int(m.group(1)) if m else 3600


class GoogleIdTokenVerifier:
    """Thread-safe; 1 instance cho cả app."""

    def __init__(self, client_ids: tuple[str, ...], allowed_domains: tuple[str, ...] = (),
                 allowed_emails: tuple[str, ...] = (), *, fetch_certs: CertFetcher = fetch_google_certs,
                 clock: Callable[[], float] = time.time, clock_skew: int = 60) -> None:
        if not client_ids:
            raise ValueError("Cần ít nhất 1 GOOGLE_OAUTH_CLIENT_ID")
        self.client_ids = client_ids
        self.allowed_domains = tuple(d.lower() for d in allowed_domains)
        self.allowed_emails = tuple(e.lower() for e in allowed_emails)
        self._fetch, self._clock, self._skew = fetch_certs, clock, clock_skew
        self._certs: dict[str, str] = {}
        self._expires = 0.0
        self._last_forced = 0.0
        self._lock = threading.Lock()

    def _get_certs(self, kid: str | None) -> dict[str, str]:
        now = self._clock()
        with self._lock:
            stale = now >= self._expires
            unknown_kid = kid is not None and kid not in self._certs and now - self._last_forced > 60
            if stale or unknown_kid:
                certs, max_age = self._fetch()
                self._certs, self._expires = certs, now + max_age
                if unknown_kid and not stale:   # chỉ tính lần tải lại "ép" do gặp kid lạ (chống spam token rác)
                    self._last_forced = now
            return self._certs

    def verify(self, token: str) -> VerifiedUser:
        try:
            header = gjwt.decode_header(token)  # type: ignore[no-untyped-call]
        except (ValueError, gexc.GoogleAuthError) as exc:
            raise AuthError(401, "INVALID_TOKEN", "Token không đọc được") from exc
        certs = self._get_certs(header.get("kid"))
        try:
            claims: dict[str, Any] = gjwt.decode(  # type: ignore[no-untyped-call]
                token, certs=certs, audience=list(self.client_ids),
                                                clock_skew_in_seconds=self._skew)
        except (ValueError, gexc.GoogleAuthError) as exc:
            msg = str(exc)
            code = "TOKEN_EXPIRED" if "expired" in msg.lower() or "too late" in msg.lower() else "INVALID_TOKEN"
            raise AuthError(401, code, "Phiên đăng nhập hết hạn hoặc token không hợp lệ") from exc
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise AuthError(401, "INVALID_TOKEN", "Token không do Google cấp")
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is not True:
            raise AuthError(403, "EMAIL_NOT_VERIFIED", "Email Google chưa xác minh")
        hd = claims.get("hd")
        domain = email.rsplit("@", 1)[-1]
        allowed = email in self.allowed_emails or (
            domain in self.allowed_domains and (hd is None or str(hd).lower() == domain))
        if not allowed:
            raise AuthError(403, "DOMAIN_NOT_ALLOWED", f"Tài khoản {email} không được phép dùng hệ thống")
        return VerifiedUser(email=email, name=claims.get("name"), subject=str(claims.get("sub")),
                            hosted_domain=str(hd) if hd else None)
