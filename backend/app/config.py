"""
Cấu hình runtime, đọc từ biến môi trường. Thiếu biến bắt buộc ở production -> app không khởi động.

Không có database, không có storage: dữ liệu duy nhất là Google Sheet master (``MASTER_SHEET_URL``).
Xem ``.env.example`` cho danh sách đầy đủ.
"""
from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.sheets import parse_sheet_url

DEFAULT_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")


class ConfigError(RuntimeError):
    pass


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(x.strip().rstrip("/") for x in (value or "").split(",") if x.strip())


def parse_service_account(raw: str) -> dict[str, Any]:
    """Nhận JSON thô hoặc base64 của JSON (dán vào biến môi trường an toàn hơn, không vỡ xuống dòng)."""
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except ValueError as exc:
            raise ConfigError("GOOGLE_SERVICE_ACCOUNT_JSON không phải JSON hay base64 hợp lệ") from exc
    try:
        info: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError("GOOGLE_SERVICE_ACCOUNT_JSON không phải JSON hợp lệ") from exc
    missing = {"type", "client_email", "private_key", "token_uri"} - info.keys()
    if info.get("type") != "service_account" or missing:
        raise ConfigError(f"Service account JSON thiếu trường: {sorted(missing) or ['type=service_account']}")
    try:  # private key hỏng (copy thiếu, mất \n) -> báo ngay lúc khởi động thay vì lỗi 500 khi tạo BBNT
        from google.oauth2 import service_account

        service_account.Credentials.from_service_account_info(info)  # type: ignore[no-untyped-call]
    except ValueError as exc:
        raise ConfigError("GOOGLE_SERVICE_ACCOUNT_JSON có private_key không hợp lệ") from exc
    return info


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"                       # production | staging | development | test
    cors_allowed_origins: tuple[str, ...] = ()
    cors_allowed_origin_regex: str | None = None
    # Auth
    google_oauth_client_ids: tuple[str, ...] = ()      # audience hợp lệ của Google ID token
    allowed_email_domains: tuple[str, ...] = ()
    allowed_emails: tuple[str, ...] = ()               # ngoại lệ ngoài domain (vd kế toán thuê ngoài)
    dev_auth_header: bool = False                      # CHỈ dev: cho phép X-User-Email không token
    # Google Sheets
    google_service_account: Mapping[str, Any] | None = field(default=None, repr=False)
    google_service_account_file: str | None = None
    # Master data
    master_sheet_url: str | None = None                # Google Sheet master (hợp đồng, brand, pháp nhân, mapping)
    master_file: str | None = None                     # CHỈ dev/test: file .xlsx thay cho Sheet master
    master_cache_seconds: int = 60
    templates_dir: str = DEFAULT_TEMPLATES_DIR
    timezone: str = "Asia/Ho_Chi_Minh"

    @property
    def is_production(self) -> bool:
        return self.app_env in ("production", "staging")

    @property
    def service_account_email(self) -> str | None:
        if self.google_service_account:
            return str(self.google_service_account.get("client_email"))
        if self.google_service_account_file and Path(self.google_service_account_file).is_file():
            try:
                return str(json.loads(Path(self.google_service_account_file).read_text()).get("client_email"))
            except (OSError, ValueError):
                return None
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        sa_raw = e.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        try:
            cache = int(e.get("MASTER_CACHE_SECONDS", "60"))
        except ValueError as exc:
            raise ConfigError("MASTER_CACHE_SECONDS phải là số giây") from exc
        s = cls(
            app_env=e.get("APP_ENV", "development").lower(),
            cors_allowed_origins=_csv(e.get("CORS_ALLOWED_ORIGINS")),
            cors_allowed_origin_regex=e.get("CORS_ALLOWED_ORIGIN_REGEX") or None,
            google_oauth_client_ids=_csv(e.get("GOOGLE_OAUTH_CLIENT_ID")),
            allowed_email_domains=tuple(d.lower().lstrip("@") for d in _csv(e.get("ALLOWED_EMAIL_DOMAINS"))),
            allowed_emails=tuple(x.lower() for x in _csv(e.get("ALLOWED_EMAILS"))),
            dev_auth_header=e.get("DEV_AUTH_HEADER", "false").lower() in ("1", "true", "yes"),
            google_service_account=parse_service_account(sa_raw) if sa_raw else None,
            google_service_account_file=e.get("GOOGLE_SERVICE_ACCOUNT_FILE") or None,
            master_sheet_url=e.get("MASTER_SHEET_URL") or None,
            master_file=e.get("MASTER_FILE") or None,
            master_cache_seconds=cache,
            templates_dir=e.get("TEMPLATES_DIR") or DEFAULT_TEMPLATES_DIR,
            timezone=e.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh"),
        )
        s.validate()
        return s

    def validate(self) -> None:
        problems: list[str] = []
        if self.timezone != "Asia/Ho_Chi_Minh":
            problems.append("APP_TIMEZONE phải là Asia/Ho_Chi_Minh (số BBNT đánh theo giờ VN)")
        if not 0 <= self.master_cache_seconds <= 3600:
            problems.append("MASTER_CACHE_SECONDS trong khoảng 0–3600")
        if self.master_sheet_url:
            try:
                parse_sheet_url(self.master_sheet_url)
            except ValueError as exc:
                problems.append(f"MASTER_SHEET_URL: {exc}")
        elif not self.master_file:
            problems.append("MASTER_SHEET_URL")
        if not Path(self.templates_dir).is_dir():
            problems.append(f"TEMPLATES_DIR không tồn tại: {self.templates_dir}")
        if self.is_production:
            if not self.master_sheet_url:
                problems.append("MASTER_SHEET_URL bắt buộc ở production (MASTER_FILE chỉ dùng local)")
            if not self.google_oauth_client_ids:
                problems.append("GOOGLE_OAUTH_CLIENT_ID")
            if not (self.allowed_email_domains or self.allowed_emails):
                problems.append("ALLOWED_EMAIL_DOMAINS")
            if not self.cors_allowed_origins:
                problems.append("CORS_ALLOWED_ORIGINS")
            if any(o == "*" or o.startswith("http://") for o in self.cors_allowed_origins):
                problems.append("CORS_ALLOWED_ORIGINS không được chứa '*' hay http:// ở production")
            if self.dev_auth_header:
                problems.append("DEV_AUTH_HEADER phải tắt ở production")
            if not (self.google_service_account or self.google_service_account_file):
                problems.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        if problems:
            raise ConfigError("Cấu hình thiếu/sai: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
