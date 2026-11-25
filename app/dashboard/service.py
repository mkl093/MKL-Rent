"""Данные для главной страницы (ТЗ §5)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory.enums import ItemStatus
from app.inventory.models import EquipmentItem
from app.projects.availability import local_today, occupancy_end_expr
from app.projects.enums import RESERVING_STATUSES
from app.projects.models import Project
from app.projects.service import project_deficits


def active_booked(db: Session) -> list[Project]:
    """Активные проекты в работе (забронированы/отгружены, окно занятости не прошло)."""
    today = local_today()
    occ_end = occupancy_end_expr()
    stmt = (
        select(Project)
        .where(
            Project.status.in_(RESERVING_STATUSES),
            (Project.end_date.is_(None)) | (occ_end >= today),
        )
        .order_by(Project.start_date)
    )
    return list(db.execute(stmt).scalars().all())


def overdue_booked(db: Session) -> list[Project]:
    """Забронированные/отгруженные с прошедшим концом окна занятости (ТЗ §5).

    Конец окна — более поздняя из дат окончания аренды и планового возврата.
    """
    today = local_today()
    occ_end = occupancy_end_expr()
    stmt = (
        select(Project)
        .where(
            Project.status.in_(RESERVING_STATUSES),
            Project.end_date.is_not(None),
            occ_end < today,
        )
        .order_by(occ_end)
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


def repair_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(EquipmentItem.status == ItemStatus.REPAIR)
        )
        or 0
    )
