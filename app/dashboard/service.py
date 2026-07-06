"""Данные для главной страницы (ТЗ §5)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.inventory.enums import ItemStatus
from app.inventory.models import EquipmentItem
from app.projects.enums import RESERVING_STATUSES
from app.projects.models import Project
from app.projects.service import project_deficits


def active_booked(db: Session) -> list[Project]:
    """Активные проекты в работе (забронированы/отгружены, дата окончания не прошла)."""
    today = utcnow().date()
    stmt = (
        select(Project)
        .where(
            Project.status.in_(RESERVING_STATUSES),
            (Project.end_date.is_(None)) | (Project.end_date >= today),
        )
        .order_by(Project.start_date)
    )
    return list(db.execute(stmt).scalars().all())


def overdue_booked(db: Session) -> list[Project]:
    """Забронированные/отгруженные с прошедшей датой окончания (ТЗ §5)."""
    today = utcnow().date()
    stmt = (
        select(Project)
        .where(
            Project.status.in_(RESERVING_STATUSES),
            Project.end_date.is_not(None),
            Project.end_date < today,
        )
        .order_by(Project.end_date)
    )
    return list(db.execute(stmt).scalars().all())


def projects_with_deficit(db: Session) -> list[Project]:
    """Резервирующие проекты, у которых есть дефицит (ТЗ §5, §15)."""
    booked = (
        db.execute(
            select(Project)
            .where(Project.status.in_(RESERVING_STATUSES))
            .order_by(Project.start_date)
        )
        .scalars()
        .all()
    )
    return [p for p in booked if project_deficits(db, p)]


def repair_items(db: Session) -> list[EquipmentItem]:
    """Оборудование в ремонте (ТЗ §5)."""
    stmt = (
        select(EquipmentItem)
        .where(EquipmentItem.status == ItemStatus.REPAIR)
        .order_by(EquipmentItem.barcode)
    )
    return list(db.execute(stmt).scalars().all())


def repair_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(EquipmentItem.status == ItemStatus.REPAIR)
        )
        or 0
    )
