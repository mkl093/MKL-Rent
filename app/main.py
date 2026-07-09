"""Точка входа FastAPI: сборка приложения, middleware, маршруты (ТЗ §32)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Импорт моделей регистрирует таблицы в Base.metadata.
from app.audit import models as _audit_models  # noqa: F401
from app.auth import models as _auth_models  # noqa: F401
from app.auth.models import User
from app.config import get_settings
from app.database import get_db
from app.dependencies import LoginRequired, require_login
from app.documents import models as _documents_models  # noqa: F401
from app.estimates import models as _estimates_models  # noqa: F401
from app.inventory import models as _inventory_models  # noqa: F401
from app.numbering import models as _numbering_models  # noqa: F401
from app.packing import models as _packing_models  # noqa: F401
from app.projects import models as _projects_models  # noqa: F401
from app.settings import models as _settings_models  # noqa: F401

STATIC_DIR = Path(__file__).parent / "static"


logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Предупреждение о секрете по умолчанию в production (ТЗ §41.2).
    if settings.is_production and settings.app_secret_key == "change-me-in-production":
        logger.warning("APP_SECRET_KEY не задан в production — задайте безопасный секрет!")
    # Запуск планировщика авто-backup (если BACKUP_AUTO=true) — ТЗ §36.
    from app.backup.scheduler import start as start_backup_scheduler

    start_backup_scheduler()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="rental-inventory", docs_url=None, redoc_url=None, lifespan=lifespan)

    # Сессия в подписанном cookie (ТЗ §41.2): httponly, secure в production.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        session_cookie="rental_session",
        same_site="lax",
        https_only=settings.is_production,
    )

    # Защита от подмены Host-заголовка за обратным прокси (ТЗ §41.2).
    # Включается только если задан APP_ALLOWED_HOSTS; localhost всегда разрешён
    # для healthcheck контейнера (curl http://localhost:8000/healthz).
    if settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[*settings.allowed_hosts, "localhost", "127.0.0.1"],
        )

    # Security-заголовки на все ответы (ТЗ §41.2).
    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Пользовательские файлы (фото, логотип) отдаются только авторизованным,
    # с защитой от обхода пути (ТЗ §41.2). Публичного доступа нет.
    media_dir = Path(settings.storage_path)
    media_dir.mkdir(parents=True, exist_ok=True)
    media_root = media_dir.resolve()

    @app.get("/media/{file_path:path}", include_in_schema=False)
    def media(
        file_path: str,
        db: Session = Depends(get_db),
        user: User = Depends(require_login),
    ) -> FileResponse:
        target = (media_root / file_path).resolve()
        if not target.is_relative_to(media_root) or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(str(target))

    # Перехват «требуется вход» → редирект на /login.
    @app.exception_handler(LoginRequired)
    async def _login_required_handler(request: Request, exc: LoginRequired) -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Маршруты модулей.
    from app.audit.router import router as audit_router
    from app.auth.router import router as auth_router
    from app.backup.router import router as backup_router
    from app.dashboard.router import router as dashboard_router
    from app.documents.router import router as documents_router
    from app.estimates.router import router as estimates_router
    from app.inventory.router import router as inventory_router
    from app.packing.router import router as packing_router
    from app.projects.router import router as projects_router
    from app.settings.router import router as settings_router
    from app.users.router import router as users_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(projects_router)
    app.include_router(estimates_router)
    app.include_router(packing_router)
    app.include_router(documents_router)
    app.include_router(inventory_router)
    app.include_router(settings_router)
    app.include_router(users_router)
    app.include_router(audit_router)
    app.include_router(backup_router)

    return app


app = create_app()
