"""Модель гостевого аккаунта клиента (просмотр склада без прав персонала).

Отдельная таблица и отдельная сессия от app.auth.models.User — гостевой контур
изолирован от персонала (своя аутентификация, свои права). Ограничение попыток
входа по IP переиспользует общую таблицу app.auth.models.IpLoginLock: перебор
пароля с одного IP должен блокироваться независимо от того, в какую форму
(персонала или гостя) его вводят.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin

# Уровни доступа кумулятивны: у гостя с уровнем N доступны и все возможности
# уровня N-1. Клиенту уровень нигде не показывается — это внутренняя настройка.
GUEST_LEVEL_VIEW = 1  # какие модели есть на складе и их количество
GUEST_LEVEL_AVAILABILITY = 2  # + поиск свободного оборудования на даты


class GuestUser(Base, TimestampMixin):
    __tablename__ = "guest_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Название клиента/компании — для админки и приветствия в шапке гостя.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    access_level: Mapped[int] = mapped_column(Integer, default=GUEST_LEVEL_VIEW, nullable=False)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def can_login(self) -> bool:
        return not self.is_blocked
