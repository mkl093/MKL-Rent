"""Работа с часовым поясом (ТЗ §28, §45.4).

Все timestamp хранятся в UTC, а отображаются в настраиваемом часовом поясе
(по умолчанию Europe/Berlin).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings

DEFAULT_TIMEZONE = "Europe/Berlin"


def get_app_timezone() -> ZoneInfo:
    """Часовой пояс приложения из настроек."""
    name = get_settings().app_timezone or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_local(value: datetime, tz: ZoneInfo | None = None) -> datetime:
    """Преобразовать UTC-время в локальное для отображения.

    Naive-время считается UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz or get_app_timezone())


def format_datetime(value: datetime, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Отформатировать время в локальном часовом поясе."""
    return to_local(value).strftime(fmt)
