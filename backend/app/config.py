"""
Cấu hình runtime, đọc từ biến môi trường. Thiếu biến bắt buộc ở production -> app không khởi động.

Không có database, không có storage: dữ liệu duy nhất là Google Sheet master (``MASTER_SHEET_URL``).
Không có service account: backend đọc Google Sheet bằng OAuth2 access token của người dùng (frontend gửi kèm).
Xem ``.env.example`` cho danh sách đầy đủ.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.sheets import parse_sheet_url

DEFAULT_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")


class ConfigError(RuntimeError):
    pass


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(x.strip().rstrip("/") for x in (value or "").split(",") if x.strip())


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
    # Master data
    master_sheet_url: str | None = None                # Google Sheet master (hợp đồng, brand, pháp nhân, mapping)
    master_file: str | None = None                     # CHỈ dev/test: file .xlsx thay cho Sheet master
    master_cache_seconds: int = 60
    templates_dir: str = DEFAULT_TEMPLATES_DIR
    timezone: str = "Asia/Ho_Chi_Minh"
    # Bật lại gộp nhiều link / tách camp / tách tab (ARCHITECTURE 14.1). Mặc định tắt: 1 link order = 1 BBNT.
    allow_merged_bbnt: bool = False
    database_url: str | None = None                    # Postgres đối soát (trang /reconcile); trống = tắt
    recon_approvers: tuple[str, ...] = ()              # email được Đổi/Giữ ô, Giữ/Tiếp tục case

    @property
    def is_production(self) -> bool:
        return self.app_env in ("production", "staging")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
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
            master_sheet_url=e.get("MASTER_SHEET_URL") or None,
            master_file=e.get("MASTER_FILE") or None,
            master_cache_seconds=cache,
            templates_dir=e.get("TEMPLATES_DIR") or DEFAULT_TEMPLATES_DIR,
            timezone=e.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh"),
            allow_merged_bbnt=e.get("ALLOW_MERGED_BBNT", "false").lower() in ("1", "true", "yes"),
            database_url=e.get("DATABASE_URL") or None,
            recon_approvers=tuple(x.lower() for x in _csv(e.get("RECON_APPROVERS"))),
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
        if self.google_oauth_client_ids and any(not c.endswith(".apps.googleusercontent.com")
                                                for c in self.google_oauth_client_ids):
            problems.append("GOOGLE_OAUTH_CLIENT_ID phải có dạng ….apps.googleusercontent.com")
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
        if problems:
            raise ConfigError("Cấu hình thiếu/sai: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
