"""Модель журнала действий (ТЗ §29).

Запись содержит дату/время, пользователя, тип события, объект и его ID, описание,
а при необходимости — старое и новое значение. Удаление журнала через UI не требуется.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Пользователь и его имя-снимок (на случай удаления учётной записи).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    user_name: Mapped[str] = mapped_column(String(150), nullable=False)

    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    object_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
