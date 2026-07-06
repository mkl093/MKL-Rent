"""Расчёт доступности и дефицита оборудования (ТЗ §15).

Обе даты включаются в период. Пересечение: проекты, чьи интервалы [start,end]
пересекаются по правилу a_start <= b_end AND b_start <= a_end (ТЗ §13.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory.enums import UNAVAILABLE_STATUSES, AccountingType
from app.inventory.models import EquipmentItem, EquipmentModel
from app.projects.enums import RESERVING_STATUSES, ProjectStatus
from app.projects.models import Project, ProjectReservation


def ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Пересекаются ли два включительных интервала дат (ТЗ §13.3)."""
    return a_start <= b_end and b_start <= a_end


@dataclass
class ModelAvailability:
    """Сводка доступности модели на период (ТЗ §15)."""

    total: int
    unavailable_by_status: int
    reserved_other: int
    required: int

    @property
    def available(self) -> int:
        return self.total - self.unavailable_by_status - self.reserved_other

    @property
    def deficit(self) -> int:
        return max(0, self.required - self.available)


@dataclass
class OccupancyEntry:
    """Строка детализации занятости (ТЗ §15)."""

    project_id: int
    number: str
    name: str
    start_date: date | None
    end_date: date | None
    status: ProjectStatus
    quantity: int


def _total_stock(db: Session, model: EquipmentModel) -> int:
    if model.accounting_type == AccountingType.QUANTITY:
        return model.total_quantity
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(EquipmentItem.model_id == model.id)
        )
        or 0
    )


def _unavailable_by_status(db: Session, model: EquipmentModel) -> int:
    """Экземпляры, недоступные по статусу (в ремонте/списаны). Для количественных — 0."""
    if model.accounting_type == AccountingType.QUANTITY:
        return 0
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(
                EquipmentItem.model_id == model.id,
                EquipmentItem.status.in_(UNAVAILABLE_STATUSES),
            )
        )
        or 0
    )


def reserved_in_other_projects(
    db: Session,
    model_id: int,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
) -> int:
    """Сумма броней модели в других забронированных пересекающихся проектах (ТЗ §15)."""
    stmt = (
        select(func.coalesce(func.sum(ProjectReservation.quantity), 0))
        .join(Project, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.model_id == model_id,
            Project.status.in_(RESERVING_STATUSES),
            Project.start_date.is_not(None),
            Project.end_date.is_not(None),
            Project.start_date <= end,
            Project.end_date >= start,
        )
    )
    if exclude_project_id is not None:
        stmt = stmt.where(Project.id != exclude_project_id)
    return db.scalar(stmt) or 0


def compute_availability(
    db: Session,
    model: EquipmentModel,
    start: date,
    end: date,
    *,
    required: int = 0,
    exclude_project_id: int | None = None,
) -> ModelAvailability:
    """Полная сводка доступности модели на период (ТЗ §15)."""
    return ModelAvailability(
        total=_total_stock(db, model),
        unavailable_by_status=_unavailable_by_status(db, model),
        reserved_other=reserved_in_other_projects(db, model.id, start, end, exclude_project_id),
        required=required,
    )


def occupancy_detail(
    db: Session,
    model_id: int,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
) -> list[OccupancyEntry]:
    """Детализация занятости модели по пересекающимся забронированным проектам (ТЗ §15)."""
    stmt = (
        select(Project, ProjectReservation.quantity)
        .join(ProjectReservation, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.model_id == model_id,
            Project.status.in_(RESERVING_STATUSES),
            Project.start_date.is_not(None),
            Project.end_date.is_not(None),
            Project.start_date <= end,
            Project.end_date >= start,
        )
        .order_by(Project.start_date)
    )
    if exclude_project_id is not None:
        stmt = stmt.where(Project.id != exclude_project_id)

    entries: list[OccupancyEntry] = []
    for project, quantity in db.execute(stmt).all():
        entries.append(
            OccupancyEntry(
                project_id=project.id,
                number=project.number,
                name=project.name,
                start_date=project.start_date,
                end_date=project.end_date,
                status=project.status,
                quantity=quantity,
            )
        )
    return entries
