"""Расчёт доступности и дефицита оборудования (ТЗ §15).

Обе даты включаются в период. Пересечение: проекты, чьи интервалы [start,end]
пересекаются по правилу a_start <= b_end AND b_start <= a_end (ТЗ §13.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.inventory.enums import UNAVAILABLE_STATUSES
from app.inventory.models import EquipmentItem, EquipmentModel
from app.projects.enums import ProjectStatus
from app.projects.models import Project, ProjectReservation


def ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Пересекаются ли два включительных интервала дат (ТЗ §13.3)."""
    return a_start <= b_end and b_start <= a_end


@dataclass
class ModelAvailability:
    """Сводка доступности модели на период (ТЗ §15).

    Занятость делится на два состояния (для 3-цветной индикации на складе):
    - «Зарезервировано» (жёлтый) — брони проектов в статусе «Забронирован»;
    - «В работе» (красный) — брони отгружённых проектов. Отгружённый и ещё не
      возвращённый проект держит оборудование и после конца аренды (просрочка).
    Свободное (зелёное) = total − unavailable_by_status − reserved − in_work.
    """

    total: int
    unavailable_by_status: int
    reserved: int  # «зарезервировано» — статус «Забронирован» (жёлтый)
    in_work: int  # «в работе» — отгружено/не возвращено (красный)
    required: int

    @property
    def reserved_other(self) -> int:
        """Суммарная занятость в других проектах (бронь + в работе)."""
        return self.reserved + self.in_work

    @property
    def available(self) -> int:
        return self.total - self.unavailable_by_status - self.reserved - self.in_work

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
    """Свободный сток модели: единицы, не помещённые в комплект (пункт 3, «Комплект»)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(EquipmentItem.model_id == model.id, EquipmentItem.kit_id.is_(None))
        )
        or 0
    )


def _unavailable_by_status(db: Session, model: EquipmentModel) -> int:
    """Свободные единицы, недоступные по статусу (в ремонте/с дефектом/списаны)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(
                EquipmentItem.model_id == model.id,
                EquipmentItem.kit_id.is_(None),
                EquipmentItem.status.in_(UNAVAILABLE_STATUSES),
            )
        )
        or 0
    )


def _booked_overlap(start: date, end: date):
    """Пересечение по датам аренды для проектов в статусе «Забронирован» (ТЗ §13.3)."""
    return and_(
        Project.status == ProjectStatus.BOOKED,
        Project.start_date.is_not(None),
        Project.end_date.is_not(None),
        Project.start_date <= end,
        Project.end_date >= start,
    )


def _in_work_overlap(start: date, end: date):
    """Пересечение для отгружённых проектов.

    Окно занятости — от даты начала аренды и до returned_date; пока проект не
    возвращён (returned_date пуст), он держит сток бессрочно, в т.ч. после конца
    аренды (просрочка). Возвращённый до начала периода — уже свободен.
    """
    return and_(
        Project.status == ProjectStatus.SHIPPED,
        Project.start_date.is_not(None),
        Project.start_date <= end,
        or_(Project.returned_date.is_(None), Project.returned_date >= start),
    )


def _sum_reservations(
    db: Session,
    model_id: int,
    overlap,
    exclude_project_id: int | None,
) -> int:
    stmt = (
        select(func.coalesce(func.sum(ProjectReservation.quantity), 0))
        .join(Project, Project.id == ProjectReservation.project_id)
        .where(ProjectReservation.model_id == model_id, overlap)
    )
    if exclude_project_id is not None:
        stmt = stmt.where(Project.id != exclude_project_id)
    return db.scalar(stmt) or 0


def reserved_in_other_projects(
    db: Session,
    model_id: int,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
) -> int:
    """Суммарная занятость модели (бронь + в работе) в других проектах (ТЗ §15)."""
    return _sum_reservations(
        db,
        model_id,
        or_(_booked_overlap(start, end), _in_work_overlap(start, end)),
        exclude_project_id,
    )


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
        reserved=_sum_reservations(db, model.id, _booked_overlap(start, end), exclude_project_id),
        in_work=_sum_reservations(db, model.id, _in_work_overlap(start, end), exclude_project_id),
        required=required,
    )


@dataclass
class KitAvailability:
    """Доступность комплекта на период: комплект существует в 1 экземпляре («Комплект»)."""

    reserved_other: int  # брони комплекта в других пересекающихся забронированных проектах

    @property
    def available(self) -> bool:
        return self.reserved_other <= 0


def reserved_kit_in_other_projects(
    db: Session,
    kit_id: int,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
) -> int:
    """Сколько раз комплект занят в других пересекающихся проектах (ТЗ §15).

    Комплект — сформированная единица (его содержимое уходит с общего склада),
    поэтому занятость считается как у модели: бронь — по датам аренды, отгрузка —
    до возврата (просрочка держит комплект и после конца аренды).
    """
    stmt = (
        select(func.coalesce(func.sum(ProjectReservation.quantity), 0))
        .join(Project, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.kit_id == kit_id,
            or_(_booked_overlap(start, end), _in_work_overlap(start, end)),
        )
    )
    if exclude_project_id is not None:
        stmt = stmt.where(Project.id != exclude_project_id)
    return db.scalar(stmt) or 0


def compute_kit_availability(
    db: Session,
    kit_id: int,
    start: date,
    end: date,
    *,
    exclude_project_id: int | None = None,
) -> KitAvailability:
    """Доступность комплекта на период (свободен, если не забронирован другим проектом)."""
    return KitAvailability(
        reserved_other=reserved_kit_in_other_projects(db, kit_id, start, end, exclude_project_id)
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
            or_(_booked_overlap(start, end), _in_work_overlap(start, end)),
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
