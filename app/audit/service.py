"""Запись и чтение журнала действий (ТЗ §29)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.models import AuditLog
from app.auth.models import User
from app.database import utcnow


def log(
    db: Session,
    user: User | None,
    event_type: EventType,
    description: str,
    *,
    object_type: str | None = None,
    object_id: int | None = None,
    old_value: object | None = None,
    new_value: object | None = None,
) -> None:
    """Записать событие в журнал.

    Логирование — вспомогательная операция: его сбой не должен ломать основное
    действие (оно уже зафиксировано вызывающим кодом).
    """
    entry = AuditLog(
        created_at=utcnow(),
        user_id=user.id if user else None,
        user_name=user.username if user else "система",
        event_type=event_type.value,
        object_type=object_type,
        object_id=object_id,
        description=description,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — журнал не должен ронять основное действие
        db.rollback()


def list_entries(
    db: Session,
    *,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    return list(db.execute(stmt.limit(limit).offset(offset)).scalars().all())
