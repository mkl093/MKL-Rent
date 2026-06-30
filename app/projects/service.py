"""Бизнес-логика проектов: нумерация, статусы, бронирование, копирование (ТЗ §13–§15)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import utcnow
from app.inventory.models import EquipmentModel
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.projects.availability import compute_availability
from app.projects.enums import ProjectStatus
from app.projects.models import Project
from app.projects.schemas import ProjectInput


class ProjectError(Exception):
    """Базовая ошибка домена проектов."""


class ValidationError(ProjectError):
    """Нарушение бизнес-правил проекта."""


@dataclass
class DeficitLine:
    model_id: int
    model_name: str
    required: int
    available: int

    @property
    def deficit(self) -> int:
        return max(0, self.required - self.available)


class DeficitError(ProjectError):
    """Бронирование с дефицитом без подтверждения (ТЗ §15)."""

    def __init__(self, lines: list[DeficitLine]):
        self.lines = lines
        super().__init__("Дефицит оборудования")


def _current_year() -> int:
    from app.utils.timezone import to_local

    return to_local(utcnow()).year


def list_projects(db: Session, archived: bool = False) -> list[Project]:
    """Активные (черновик/забронирован) или архивные (завершён/отменён) проекты (ТЗ §13.6)."""
    archived_statuses = [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED]
    stmt = select(Project)
    if archived:
        stmt = stmt.where(Project.status.in_(archived_statuses))
    else:
        stmt = stmt.where(Project.status.notin_(archived_statuses))
    stmt = stmt.order_by(Project.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_project(db: Session, project_id: int) -> Project | None:
    stmt = (
        select(Project).options(selectinload(Project.reservations)).where(Project.id == project_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_project(db: Session, data: ProjectInput) -> Project:
    """Создать проект-черновик с автоматическим номером PRJ-YYYY-NNN (ТЗ §14)."""
    year = _current_year()
    number = next_number(db, DocType.PROJECT, year)
    project = Project(
        number=number,
        name=data.name.strip(),
        start_date=data.start_date,
        end_date=data.end_date,
        rental_coefficient=data.rental_coefficient,
        vat=data.vat,
        customer=(data.customer or None),
        address=(data.address or None),
        comment=(data.comment or None),
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project: Project, data: ProjectInput) -> Project:
    """Обновить поля проекта. Номер и статус не меняются здесь."""
    project.name = data.name.strip()
    project.start_date = data.start_date
    project.end_date = data.end_date
    project.rental_coefficient = data.rental_coefficient
    project.vat = data.vat
    project.customer = data.customer or None
    project.address = data.address or None
    project.comment = data.comment or None
    db.commit()
    db.refresh(project)
    return project


def copy_project(db: Session, project: Project) -> Project:
    """Копия проекта: общие данные, без дат, packing и статуса брони (ТЗ §13.8).

    Смета копируется на Этапе 4. Новый проект — черновик с новым номером.
    """
    year = _current_year()
    number = next_number(db, DocType.PROJECT, year)
    copy = Project(
        number=number,
        name=f"{project.name} (копия)",
        start_date=None,
        end_date=None,
        rental_coefficient=project.rental_coefficient,
        vat=project.vat,
        customer=project.customer,
        address=project.address,
        comment=project.comment,
        status=ProjectStatus.DRAFT,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


def has_packing_list(db: Session, project: Project) -> bool:
    """Создан ли packing-лист (ТЗ §13.7)."""
    from app.packing.service import project_has_packing

    return project_has_packing(db, project.id)


def delete_project(db: Session, project: Project) -> None:
    """Удалить можно только черновик без packing-листа (ТЗ §13.7)."""
    if project.status != ProjectStatus.DRAFT:
        raise ValidationError("Удалить можно только проект в статусе «Черновик»")
    if has_packing_list(db, project):
        raise ValidationError("Нельзя удалить проект с packing-листом")
    db.delete(project)
    db.commit()


def _validate_bookable(project: Project) -> None:
    if project.start_date is None or project.end_date is None:
        raise ValidationError("Укажите даты начала и окончания аренды")
    if project.start_date > project.end_date:
        raise ValidationError("Дата начала позже даты окончания")


def project_deficits(db: Session, project: Project) -> list[DeficitLine]:
    """Дефицит по всем броням проекта на его даты (ТЗ §15)."""
    if project.start_date is None or project.end_date is None:
        return []
    lines: list[DeficitLine] = []
    for res in project.reservations:
        model = db.get(EquipmentModel, res.model_id)
        if model is None:
            continue
        avail = compute_availability(
            db,
            model,
            project.start_date,
            project.end_date,
            required=res.quantity,
            exclude_project_id=project.id,
        )
        if avail.deficit > 0:
            lines.append(
                DeficitLine(
                    model_id=model.id,
                    model_name=model.name,
                    required=res.quantity,
                    available=avail.available,
                )
            )
    return lines


def book_project(db: Session, project: Project, allow_deficit: bool = False) -> Project:
    """Перевести проект в «Забронирован» (ТЗ §13.4, §15).

    Требует корректных дат. При дефиците — отдельное подтверждение.
    """
    _validate_bookable(project)
    deficits = project_deficits(db, project)
    if deficits and not allow_deficit:
        raise DeficitError(deficits)
    project.status = ProjectStatus.BOOKED
    db.commit()
    db.refresh(project)
    return project


def set_status(db: Session, project: Project, status: ProjectStatus) -> Project:
    """Прямой перевод статуса (завершить/отменить/вернуть в черновик).

    Дата окончания сама бронь не освобождает — освобождение происходит только
    при смене статуса (ТЗ §13.4).
    """
    project.status = status
    db.commit()
    db.refresh(project)
    return project
