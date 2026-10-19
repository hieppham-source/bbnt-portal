"""
Kiểm tra Google OAuth2 access token mà frontend gửi kèm (header ``X-Google-Access-Token``).

Backend dùng token này để gọi Google Sheets API thay cho service account. Trước khi dùng, kiểm 3 điều qua
``https://oauth2.googleapis.com/tokeninfo``:

* token do **chính app của mình** cấp (``aud``/``azp`` thuộc ``GOOGLE_OAUTH_CLIENT_ID``) — không nhận token
  của app khác;
* token thuộc **đúng người** đang đăng nhập (email trùng email trong ID token);
* token có scope ``spreadsheets.readonly``.

Kết quả được cache theo hash của token tới khi token hết hạn (thường 1 giờ) -> mỗi người chỉ tốn 1 lần gọi
tokeninfo/giờ. Token không bao giờ được ghi log hay lưu xuống đĩa.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.errors import BbntError, fail

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

#: (access_token) -> (http_status, json) — inject để test, mặc định gọi Google.
TokenInfoFetcher = Callable[[str], tuple[int, dict[str, Any]]]


def _fetch_tokeninfo(token: str) -> tuple[int, dict[str, Any]]:
    import requests

    try:
        resp = requests.post(TOKENINFO_URL, data={"access_token": token}, timeout=10)
    except requests.RequestException as exc:
        raise fail("GOOGLE_UNREACHABLE", "Không kết nối được Google để kiểm tra phiên đăng nhập", 502) from exc
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


@dataclass(frozen=True)
class AccessTokenInfo:
    email: str
    expires_at: float


class AccessTokenVerifier:
    def __init__(self, client_ids: Sequence[str], fetch: TokenInfoFetcher = _fetch_tokeninfo,
                 clock: Callable[[], float] = time.time, max_cache: int = 1000) -> None:
        self.client_ids = frozenset(client_ids)
        self._fetch, self._clock, self._max = fetch, clock, max_cache
        self._cache: dict[str, AccessTokenInfo] = {}
        self._lock = threading.Lock()

    def verify(self, token: str, email: str) -> AccessTokenInfo:
        """Raise :class:`BbntError` 401 nếu token hết hạn / sai app / sai người / thiếu scope Sheets."""
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
        if hit is None or hit.expires_at <= now:
            hit = self._lookup(token, now)
            with self._lock:
                if len(self._cache) >= self._max:     # dọn token hết hạn, vẫn đầy thì xoá hết
                    self._cache = {k: v for k, v in self._cache.items() if v.expires_at > now}
                    if len(self._cache) >= self._max:
                        self._cache.clear()
                self._cache[key] = hit
        if hit.email != email.lower():
            raise fail("GOOGLE_TOKEN_WRONG_USER", "Phiên Google không khớp người đang đăng nhập — đăng nhập lại", 401)
        return hit

    def _lookup(self, token: str, now: float) -> AccessTokenInfo:
        status, info = self._fetch(token)
        if status != 200 or "error" in info or "error_description" in info:
            raise fail("GOOGLE_TOKEN_EXPIRED", "Phiên Google hết hạn — đăng nhập lại", 401)
        audience = {str(info.get("aud", "")), str(info.get("azp", ""))}
        if self.client_ids and not (audience & self.client_ids):
            raise fail("GOOGLE_TOKEN_WRONG_APP", "Token Google không do BBNT Portal cấp", 401)
        if SHEETS_SCOPE not in str(info.get("scope", "")).split():
            raise fail("GOOGLE_SCOPE_MISSING", "Chưa cấp quyền đọc Google Sheet — đăng xuất rồi đăng nhập lại "
                                               "và bấm Cho phép", 401)
        email = str(info.get("email", "")).lower()
        if not email or str(info.get("email_verified", "true")).lower() != "true":
            raise fail("GOOGLE_TOKEN_EXPIRED", "Token Google không có email đã xác minh — đăng nhập lại", 401)
        try:
            ttl = int(info.get("expires_in", 0))
        except (TypeError, ValueError):
            ttl = 0
        if ttl <= 0:
            raise fail("GOOGLE_TOKEN_EXPIRED", "Phiên Google hết hạn — đăng nhập lại", 401)
        return AccessTokenInfo(email, now + min(ttl, 3600) - 30)


__all__ = ["SHEETS_SCOPE", "AccessTokenInfo", "AccessTokenVerifier", "BbntError"]
