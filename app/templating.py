"""Настройка Jinja2 и помощники для шаблонов."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.utils.timezone import format_datetime

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["format_datetime"] = format_datetime


def static_url(path: str) -> str:
    """URL статики с cache-busting-версией по mtime файла.

    Браузеры (особенно iOS Safari) кешируют JS/CSS без Cache-Control и могут
    отдавать устаревшую версию. Суффикс ?v=<mtime> меняет URL при каждом
    изменении файла и заставляет забрать свежую копию.
    """
    rel = path.lstrip("/")
    try:
        version = int((STATIC_DIR / rel).stat().st_mtime)
    except OSError:
        return f"/static/{rel}"
    return f"/static/{rel}?v={version}"


templates.env.globals["static_url"] = static_url


# --- CSRF ---------------------------------------------------------------

CSRF_SESSION_KEY = "csrf_token"


def get_csrf_token(request: Request) -> str:
    """Вернуть CSRF-токен из сессии, создав при отсутствии."""
    from app.utils.security import generate_token

    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = generate_token()
        request.session[CSRF_SESSION_KEY] = token
    return token


# --- Flash-сообщения ----------------------------------------------------

FLASH_SESSION_KEY = "_flashes"


def flash(request: Request, message: str, category: str = "info") -> None:
    """Добавить flash-сообщение (показывается один раз)."""
    request.session.setdefault(FLASH_SESSION_KEY, []).append(
        {"message": message, "category": category}
    )


def pop_flashes(request: Request) -> list[dict[str, str]]:
    """Забрать и очистить flash-сообщения."""
    return request.session.pop(FLASH_SESSION_KEY, [])
