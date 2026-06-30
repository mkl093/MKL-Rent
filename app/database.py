"""Подключение к БД и базовый класс моделей.

Синхронный SQLAlchemy 2 — для обычного CRUD это проще и предпочтительнее (ТЗ §32).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings

_settings = get_settings()

# SQLite требует особого флага для использования в нескольких потоках (тесты/проверки).
_connect_args = (
    {"check_same_thread": False} if _settings.sqlalchemy_url.startswith("sqlite") else {}
)

engine = create_engine(
    _settings.sqlalchemy_url,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def utcnow() -> datetime:
    """Текущее время в UTC (timestamp всегда храним в UTC — ТЗ §45.4)."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Привести значение к aware-UTC.

    SQLite не хранит tzinfo и возвращает naive-время; считаем его UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""


class TimestampMixin:
    """Поля created_at/updated_at в UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
