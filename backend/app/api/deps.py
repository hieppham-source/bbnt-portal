"""FastAPI dependencies — đọc cấu hình từ ``app.config``; override được trong test (``app.dependency_overrides``)."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException

from app.auth import AuthError, GoogleIdTokenVerifier
from app.config import Settings, get_settings
from app.errors import fail
from app.master.repository import MasterRepository, google_sheet_source, xlsx_source
from app.services.generator import BbntGenerator
from app.services.sheets import GoogleSheetsReader, SheetReader, parse_sheet_url
from app.services.templates import TemplateRegistry


@dataclass(frozen=True)
class User:
    email: str


_SINGLETONS: dict[tuple[str, int], tuple[Settings, Any]] = {}
_LOCK = threading.Lock()


def _per_settings(name: str, settings: Settings, build: Callable[[], Any]) -> Any:
    """1 instance cho mỗi (loại, Settings) — giữ cache master / cert / template suốt vòng đời app."""
    key = (name, id(settings))
    with _LOCK:
        hit = _SINGLETONS.get(key)
        if hit is None or hit[0] is not settings:
            hit = (settings, build())
            _SINGLETONS[key] = hit
        return hit[1]


def google_reader_for(settings: Settings) -> GoogleSheetsReader | None:
    def build() -> GoogleSheetsReader | None:
        if settings.google_service_account:
            return GoogleSheetsReader.from_info(dict(settings.google_service_account))
        if settings.google_service_account_file:
            return GoogleSheetsReader.from_file(settings.google_service_account_file)
        return None
    reader: GoogleSheetsReader | None = _per_settings("google", settings, build)
    return reader


def get_sheet_reader(settings: Annotated[Settings, Depends(get_settings)]) -> SheetReader:
    reader = google_reader_for(settings)
    if reader is None:
        raise fail("SHEETS_NOT_CONFIGURED", "Chưa cấu hình GOOGLE_SERVICE_ACCOUNT_JSON", status=500)
    return reader


def master_repo_for(settings: Settings) -> MasterRepository:
    def build() -> MasterRepository:
        if settings.master_sheet_url:
            reader = google_reader_for(settings)
            if reader is None:
                raise fail("SHEETS_NOT_CONFIGURED", "Chưa cấu hình GOOGLE_SERVICE_ACCOUNT_JSON", status=500)
            source = google_sheet_source(reader, parse_sheet_url(settings.master_sheet_url))
        else:
            assert settings.master_file
            source = xlsx_source(settings.master_file)
        return MasterRepository(source, ttl_seconds=settings.master_cache_seconds)
    repo: MasterRepository = _per_settings("master", settings, build)
    return repo


def get_master_repo(settings: Annotated[Settings, Depends(get_settings)]) -> MasterRepository:
    return master_repo_for(settings)


def templates_for(settings: Settings) -> TemplateRegistry:
    reg: TemplateRegistry = _per_settings("templates", settings, lambda: TemplateRegistry(settings.templates_dir))
    return reg


def get_templates(settings: Annotated[Settings, Depends(get_settings)]) -> TemplateRegistry:
    return templates_for(settings)


def get_clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


def get_generator(templates: Annotated[TemplateRegistry, Depends(get_templates)],
                  clock: Annotated[Callable[[], datetime], Depends(get_clock)]) -> BbntGenerator:
    return BbntGenerator(templates, clock)


@lru_cache
def _verifier(client_ids: tuple[str, ...], domains: tuple[str, ...], emails: tuple[str, ...]
              ) -> GoogleIdTokenVerifier:
    return GoogleIdTokenVerifier(client_ids, domains, emails)   # 1 instance -> cache cert Google dùng chung


def get_token_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> GoogleIdTokenVerifier | None:
    if not settings.google_oauth_client_ids:
        return None
    return _verifier(settings.google_oauth_client_ids, settings.allowed_email_domains, settings.allowed_emails)


def get_user(
    settings: Annotated[Settings, Depends(get_settings)],
    verifier: Annotated[GoogleIdTokenVerifier | None, Depends(get_token_verifier)],
    authorization: Annotated[str | None, Header()] = None,
    x_user_email: Annotated[str | None, Header()] = None,
) -> User:
    """``Authorization: Bearer <Google ID token>``. ``X-User-Email`` chỉ nhận khi DEV_AUTH_HEADER=true (local)."""
    if authorization and authorization.lower().startswith("bearer "):
        if verifier is None:
            raise HTTPException(500, detail={"code": "AUTH_NOT_CONFIGURED",
                                             "message": "Thiếu GOOGLE_OAUTH_CLIENT_ID"})
        try:
            user = verifier.verify(authorization[7:].strip())
        except AuthError as exc:
            raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message},
                                headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
                                ) from exc
        return User(user.email)
    if settings.dev_auth_header and not settings.is_production and x_user_email and "@" in x_user_email:
        return User(x_user_email.strip().lower())
    raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Cần đăng nhập"},
                        headers={"WWW-Authenticate": "Bearer"})
