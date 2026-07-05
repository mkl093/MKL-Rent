"""Точка входа FastAPI: сборка приложения, middleware, маршруты (ТЗ §32)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# Импорт моделей регистрирует таблицы в Base.metadata.
from app.audit import models as _audit_models  # noqa: F401
from app.auth import models as _auth_models  # noqa: F401
from app.config import get_settings
from app.dependencies import LoginRequired
from app.documents import models as _documents_models  # noqa: F401
from app.estimates import models as _estimates_models  # noqa: F401
from app.inventory import models as _inventory_models  # noqa: F401
from app.numbering import models as _numbering_models  # noqa: F401
from app.packing import models as _packing_models  # noqa: F401
from app.projects import models as _projects_models  # noqa: F401
from app.settings import models as _settings_models  # noqa: F401

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="rental-inventory", docs_url=None, redoc_url=None)

    # Сессия в подписанном cookie (ТЗ §41.2): httponly, secure в production.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        session_cookie="rental_session",
        same_site="lax",
        https_only=settings.is_production,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Отдача пользовательских файлов (фото моделей и т. п.) из STORAGE_PATH.
    media_dir = Path(settings.storage_path)
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

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

    return app


app = create_app()
