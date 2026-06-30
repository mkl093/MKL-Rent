"""Общие зависимости FastAPI: текущий пользователь, доступ, CSRF, рендеринг."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.service import get_user_by_id
from app.database import get_db
from app.settings.service import get_company_settings
from app.templating import CSRF_SESSION_KEY, get_csrf_token, pop_flashes, templates

SESSION_USER_KEY = "user_id"


class LoginRequired(Exception):
    """Сигнал, что требуется вход — обрабатывается редиректом на /login."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Текущий пользователь из сессии или None."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    user = get_user_by_id(db, user_id)
    if user is None or not user.can_login:
        request.session.pop(SESSION_USER_KEY, None)
        return None
    return user


def require_login(
    user: User | None = Depends(get_current_user),
) -> User:
    """Зависимость защищённых страниц: требует авторизованного пользователя."""
    if user is None:
        raise LoginRequired
    return user


def verify_csrf(request: Request, csrf_token: str | None = Form(None)) -> None:
    """Проверить CSRF-токен формы против значения в сессии (ТЗ §41.2)."""
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not csrf_token or csrf_token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Недействительный CSRF-токен"
        )


def render(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
    *,
    db: Session | None = None,
    user: User | None = None,
    status_code: int = status.HTTP_200_OK,
):
    """Отрендерить шаблон с общим контекстом (пользователь, настройки, CSRF, flash)."""
    ctx: dict[str, Any] = {
        "request": request,
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "flashes": pop_flashes(request),
        "company": get_company_settings(db) if db is not None else None,
        "current_project": request.session.get("current_project"),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)


def redirect(url: str, status_code: int = status.HTTP_303_SEE_OTHER) -> RedirectResponse:
    """Редирект после POST (PRG-паттерн)."""
    return RedirectResponse(url=url, status_code=status_code)
