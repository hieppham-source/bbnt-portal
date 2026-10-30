"""
FastAPI app factory (stateless — không database, không lưu file).

Chạy production (Dockerfile làm sẵn)::

    uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import deps
from app.api.v1.bbnt import issue_out
from app.api.v1.bbnt import router as bbnt_router
from app.api.v1.master import router as master_router
from app.api.v1.recon import router as recon_router
from app.config import Settings, get_settings
from app.core.calculation import RowIssue
from app.errors import BbntError

log = logging.getLogger("bbnt")

#: Header tuỳ biến JS phía trình duyệt cần đọc được (tên file tải về, số BBNT...).
EXPOSED_HEADERS = ["Content-Disposition", "X-BBNT-Number", "X-BBNT-Warnings", "X-BBNT-Total-Payable"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()   # sai cấu hình -> ConfigError ngay khi khởi động, không chờ request
    app = FastAPI(
        title="BBNT Generator — Hannah Olala",
        version="2.0.0",
        docs_url=None if settings.is_production else "/docs",      # tắt Swagger công khai ở production
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_origin_regex=settings.cors_allowed_origin_regex,
        allow_credentials=False,                 # xác thực bằng Bearer token, không dùng cookie cross-site
        allow_methods=["GET", "POST", "OPTIONS"],
        # X-User-Email chỉ mở khi chạy local với DEV_AUTH_HEADER=true (frontend dev không cần Google)
        allow_headers=["Authorization", "Content-Type", "X-Google-Access-Token"]
        + (["X-User-Email"] if settings.dev_auth_header and not settings.is_production else []),
        expose_headers=EXPOSED_HEADERS,
        max_age=600,
    )
    app.include_router(bbnt_router)
    app.include_router(master_router)
    app.include_router(recon_router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        """Mở địa chỉ backend trên trình duyệt -> biết ngay app đang chạy (thay vì {"detail":"Not Found"})."""
        return {"service": "bbnt-api", "status": "ok", "health": "/healthz", "ready": "/readyz"}

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness — UptimeRobot ping endpoint này để Render Free không ngủ."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        """Readiness: template parse được + Sheet master (nếu đọc được không cần người dùng). Không lộ chi tiết lỗi."""
        checks: dict[str, str] = {}
        try:
            override = app.dependency_overrides.get(deps.get_master_repo)
            repo = override() if override else deps.master_repo_for(settings)
            if repo.has_default_source:
                master = repo.get()
                checks["master"] = "ok" if not master.errors else f"{len(master.errors)} dòng lỗi"
            else:   # chế độ OAuth: master chỉ đọc được khi có người dùng đăng nhập
                cached = repo.peek()
                checks["master"] = "chưa tải" if cached is None else (
                    "ok" if not cached.errors else f"{len(cached.errors)} dòng lỗi")
        except Exception:  # noqa: BLE001
            log.exception("readyz: master check failed")
            checks["master"] = "error"
        try:
            t_override = app.dependency_overrides.get(deps.get_templates)
            templates = (t_override() if t_override else deps.templates_for(settings)).check()
            checks["templates"] = "ok" if all(v == "ok" for v in templates.values()) else "error"
        except Exception:  # noqa: BLE001
            log.exception("readyz: template check failed")
            checks["templates"] = "error"
        ok = all(v != "error" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)

    @app.exception_handler(BbntError)
    async def _bbnt_error(_: Request, exc: BbntError) -> JSONResponse:
        details = exc.details
        if isinstance(details, list) and details and isinstance(details[0], RowIssue):
            details = [issue_out(i) for i in details]
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(
            {"code": exc.code, "message": exc.message, "details": details}))

    return app
