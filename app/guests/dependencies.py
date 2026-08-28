"""Гостевая сессия.

Отдельный ключ сессии (GUEST_SESSION_KEY) не пересекается с SESSION_USER_KEY
персонала (app.dependencies) — в одном браузере вход как гость и как сотрудник
не мешают друг другу. Проверка уровня доступа (guest.access_level) делается
там, где она реально нужна — в app.guests.router, инлайн, а не отдельной
зависимостью: уровень 2 сейчас лишь добавляет функцию на ту же страницу
каталога, а не отдельный маршрут.
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.guests.models import GuestUser
from app.guests.service import get_guest_by_id

GUEST_SESSION_KEY = "guest_id"


class GuestLoginRequired(Exception):
    """Сигнал, что требуется гостевой вход — обрабатывается редиректом на /guest/login."""


def get_current_guest(request: Request, db: Session = Depends(get_db)) -> GuestUser | None:
    """Текущий гость из сессии или None."""
    guest_id = request.session.get(GUEST_SESSION_KEY)
    if not guest_id:
        return None
    guest = get_guest_by_id(db, guest_id)
    if guest is None or not guest.can_login:
        request.session.pop(GUEST_SESSION_KEY, None)
        return None
    return guest


def require_guest_login(guest: GuestUser | None = Depends(get_current_guest)) -> GuestUser:
    """Зависимость гостевых страниц: требует авторизованного гостя."""
    if guest is None:
        raise GuestLoginRequired
    return guest
