"""Бизнес-логика проектов: нумерация, статусы, бронирование, копирование (ТЗ §13–§15)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import utcnow
from app.inventory.models import EquipmentModel
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.projects.availability import compute_availability, ranges_overlap
from app.projects.enums import RESERVING_STATUSES, ProjectStatus
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


def _today() -> date:
    from app.utils.timezone import to_local

    return to_local(utcnow()).date()


def _validate_actual_dates(shipped: date | None, returned: date | None) -> None:
    """Дата возврата не может быть раньше даты отгрузки."""
    if shipped is not None and returned is not None and returned < shipped:
        raise ValidationError("Дата возврата не может быть раньше даты отгрузки")


def list_projects(
    db: Session, archived: bool = False, status_filter: str | None = None
) -> list[Project]:
    """Активные (черновик/забронирован) или архивные (завершён/отменён) проекты (ТЗ §13.6).

    status_filter (карточки главной страницы, ТЗ §5): "active" — резервирующие
    с непрошедшей датой окончания; "overdue" — резервирующие с прошедшей датой
    окончания; "deficit" — резервирующие проекты с нехваткой оборудования.
    """
    if status_filter in ("active", "overdue"):
        today = utcnow().date()
        stmt = select(Project).where(Project.status.in_(RESERVING_STATUSES))
        if status_filter == "active":
            stmt = stmt.where(
                (Project.end_date.is_(None)) | (Project.end_date >= today)
            ).order_by(Project.start_date)
        else:
            stmt = stmt.where(
                Project.end_date.is_not(None), Project.end_date < today
            ).order_by(Project.end_date)
        return list(db.execute(stmt).scalars().all())

    if status_filter == "deficit":
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

    archived_statuses = [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED]
    stmt = select(Project)
    if archived:
        stmt = stmt.where(Project.status.in_(archived_statuses))
    else:
        stmt = stmt.where(Project.status.notin_(archived_statuses))
    stmt = stmt.order_by(Project.created_at.desc())
    return list(db.execute(stmt).scalars().all())


@dataclass
class TimelineRow:
    """Строка диаграммы Ганта календаря проектов (ТЗ §13.9)."""

    project: Project
    offset: int  # индекс первого дня полосы в окне [start; end]
    length: int  # длина полосы в днях
    cont_before: bool  # срок начинается раньше видимого окна
    cont_after: bool  # срок заканчивается позже видимого окна
    overlaps: list[Project]  # другие видимые проекты, пересекающиеся по датам


def compute_project_timeline(
    db: Session, start: date, end: date, *, q: str | None = None, archived: bool = False
) -> tuple[list[date], list[TimelineRow], list[int]]:
    """Проекты, пересекающие [start; end], разложенные для календаря-Ганта (ТЗ §13.9).

    Наложения (`overlaps`) считаются только среди проектов, попавших в окно —
    иначе бейдж «×N» ссылался бы на проекты, которых нет на экране. Правило
    пересечения — `ranges_overlap()` (ТЗ §13.3), то же, что для брони оборудования.
    """
    archived_statuses = [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED]
    stmt = select(Project).where(
        Project.start_date.is_not(None),
        Project.end_date.is_not(None),
        Project.start_date <= end,
        Project.end_date >= start,
    )
    stmt = stmt.where(
        Project.status.in_(archived_statuses)
        if archived
        else Project.status.notin_(archived_statuses)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Project.number.ilike(like), Project.name.ilike(like), Project.customer.ilike(like))
        )
    stmt = stmt.order_by(Project.start_date, Project.end_date)
    projects = list(db.execute(stmt).scalars().all())

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows: list[TimelineRow] = []
    for p in projects:
        overlaps = [
            other
            for other in projects
            if other.id != p.id
            and ranges_overlap(p.start_date, p.end_date, other.start_date, other.end_date)
        ]
        bar_start = max(p.start_date, start)
        bar_end = min(p.end_date, end)
        rows.append(
            TimelineRow(
                project=p,
                offset=(bar_start - start).days,
                length=(bar_end - bar_start).days + 1,
                cont_before=p.start_date < start,
                cont_after=p.end_date > end,
                overlaps=overlaps,
            )
        )

    load = [0] * len(days)
    for row in rows:
        for i in range(row.offset, row.offset + row.length):
            load[i] += 1

    return days, rows, load


@dataclass
class GridBar:
    """Полоса проекта в ячейке сетки месяца (ТЗ §13.9)."""

    project: Project
    is_start: bool  # день == project.start_date (не граница окна/недели)
    is_end: bool  # день == project.end_date


@dataclass
class GridDay:
    """Ячейка сетки месяца календаря проектов (ТЗ §13.9)."""

    date: date
    in_month: bool
    bars: list[GridBar]


def _group_rows_by_day(days: list[date], rows: list[TimelineRow]) -> list[list[TimelineRow]]:
    """Проекты, активные на каждый день диапазона (по offset/length из TimelineRow)."""
    by_day: list[list[TimelineRow]] = [[] for _ in days]
    for row in rows:
        for i in range(row.offset, row.offset + row.length):
            by_day[i].append(row)
    return by_day


def build_project_grid(
    days: list[date], rows: list[TimelineRow], month_start: date
) -> list[list[GridDay]]:
    """Недели Пн–Вс для сетки месяца календаря проектов (ТЗ §13.9).

    Принимает уже посчитанные `compute_project_timeline()` days/rows (окно —
    полные недели, захватывающие месяц целиком) без обращения к БД. Скругление
    торцов полосы (`is_start`/`is_end`) — по настоящим датам проекта, а не по
    границе окна/недели, иначе полоса, продолжающаяся на новую неделю, выглядела
    бы законченной.
    """
    by_day = _group_rows_by_day(days, rows)
    weeks: list[list[GridDay]] = []
    week: list[GridDay] = []
    for d, day_rows in zip(days, by_day):
        bars = [
            GridBar(
                project=r.project,
                is_start=d == r.project.start_date,
                is_end=d == r.project.end_date,
            )
            for r in day_rows
        ]
        week.append(GridDay(date=d, in_month=d.month == month_start.month, bars=bars))
        if len(week) == 7:
            weeks.append(week)
            week = []
    return weeks


def list_projects_without_dates(db: Session, archived: bool = False) -> list[Project]:
    """Проекты без обеих дат аренды — не попадают в календарь (ТЗ §13.9)."""
    archived_statuses = [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED]
    stmt = select(Project).where(
        or_(Project.start_date.is_(None), Project.end_date.is_(None))
    )
    stmt = stmt.where(
        Project.status.in_(archived_statuses)
        if archived
        else Project.status.notin_(archived_statuses)
    )
    stmt = stmt.order_by(Project.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_project(db: Session, project_id: int) -> Project | None:
    stmt = (
        select(Project).options(selectinload(Project.reservations)).where(Project.id == project_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_project(db: Session, data: ProjectInput) -> Project:
    """Создать проект-черновик с автоматическим номером PRJ-YYYY-NNN (ТЗ §14)."""
    _validate_actual_dates(data.shipped_date, data.returned_date)
    year = _current_year()
    number = next_number(db, DocType.PROJECT, year)
    project = Project(
        number=number,
        name=data.name.strip(),
        start_date=data.start_date,
        end_date=data.end_date,
        shipped_date=data.shipped_date,
        returned_date=data.returned_date,
        rental_coefficient=data.rental_coefficient,
        vat=data.vat,
        customer=(data.customer or None),
        address=(data.address or None),
        comment=(data.comment or None),
        color=data.color,
        calendar_bar=data.calendar_bar,
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project: Project, data: ProjectInput) -> Project:
    """Обновить поля проекта. Номер и статус не меняются здесь."""
    _validate_actual_dates(data.shipped_date, data.returned_date)
    project.name = data.name.strip()
    project.start_date = data.start_date
    project.end_date = data.end_date
    project.shipped_date = data.shipped_date
    project.returned_date = data.returned_date
    project.rental_coefficient = data.rental_coefficient
    project.vat = data.vat
    project.customer = data.customer or None
    project.address = data.address or None
    project.comment = data.comment or None
    project.color = data.color
    project.calendar_bar = data.calendar_bar
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
        color=project.color,
        calendar_bar=project.calendar_bar,
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
        # Бронь комплекта (model_id пуст) в дефицит по моделям не входит.
        if res.model_id is None:
            continue
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
    при смене статуса (ТЗ §13.4). Фактические даты проставляются автоматически
    (если ещё пусты), при этом ранее введённые вручную значения сохраняются:
    «Отгружено» → shipped_date = сегодня, «Завершён» → returned_date = сегодня.
    """
    if status == ProjectStatus.SHIPPED and project.shipped_date is None:
        project.shipped_date = _today()
    if status == ProjectStatus.COMPLETED and project.returned_date is None:
        project.returned_date = _today()
    project.status = status
    db.commit()
    db.refresh(project)
    return project
