"""Расчёт доступности и дефицита оборудования (ТЗ §15).

Обе даты включаются в период. Пересечение: проекты, чьи интервалы [start,end]
пересекаются по правилу a_start <= b_end AND b_start <= a_end (ТЗ §13.3).

Окно занятости проекта (ТЗ §15.1) шире дат аренды, если заданы плановые даты
отгрузки/возврата: начало — более ранняя из «начало аренды»/«дата отгрузки»,
конец — более поздняя из «окончание аренды»/«дата возврата». Отгружённый проект
держит оборудование до конца окна, а если конец прошёл, а проект не завершён
(просрочка), — бессрочно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.inventory.enums import ItemStatus
from app.inventory.models import EquipmentItem, EquipmentModel
from app.projects.enums import ProjectStatus
from app.projects.models import Project, ProjectReservation


def ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Пересекаются ли два включительных интервала дат (ТЗ §13.3)."""
    return a_start <= b_end and b_start <= a_end


def local_today() -> date:
    """Сегодня в часовом поясе приложения — граница просрочки отгрузки."""
    from app.utils.timezone import to_local

    return to_local(utcnow()).date()


def occupancy_start_expr():
    """SQL: начало окна занятости — min(start_date, shipped_date).

    Через CASE, а не LEAST(): в SQLite (dev/тесты) LEAST/GREATEST нет.
    """
    return case(
        (
            and_(Project.shipped_date.is_not(None), Project.shipped_date < Project.start_date),
            Project.shipped_date,
        ),
        else_=Project.start_date,
    )


def occupancy_end_expr():
    """SQL: конец окна занятости — max(end_date, returned_date)."""
    return case(
        (
            and_(Project.returned_date.is_not(None), Project.returned_date > Project.end_date),
            Project.returned_date,
        ),
        else_=Project.end_date,
    )


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
    # Окно занятости (аренда, расширенная плановыми отгрузкой/возвратом).
    occupancy_start: date | None = None
    occupancy_end: date | None = None

    @property
    def window_differs(self) -> bool:
        """Окно занятости шире дат аренды — показываем его отдельно."""
        return (self.occupancy_start, self.occupancy_end) != (self.start_date, self.end_date)


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


def _unavailable_clause():
    """Единица недоступна по статусу: в ремонте/списана, либо с дефектом без
    отметки «использовать несмотря на дефект» (см. EquipmentItem.is_usable)."""
    return or_(
        EquipmentItem.status.in_([ItemStatus.REPAIR, ItemStatus.RETIRED]),
        and_(
            EquipmentItem.status == ItemStatus.DEFECT,
            EquipmentItem.usable_despite_defect.is_(False),
        ),
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
                _unavailable_clause(),
            )
        )
        or 0
    )


def _booked_overlap(start: date, end: date):
    """Пересечение окна занятости «Забронирован»-проектов с периодом (ТЗ §13.3)."""
    return and_(
        Project.status == ProjectStatus.BOOKED,
        Project.start_date.is_not(None),
        Project.end_date.is_not(None),
        occupancy_start_expr() <= end,
        occupancy_end_expr() >= start,
    )


def _in_work_overlap(start: date, end: date, today: date):
    """Пересечение для отгружённых проектов.

    Окно занятости — от начала окна до его конца (плановый возврат или окончание
    аренды). Если конец окна прошёл (< today), а проект всё ещё «Отгружено» —
    просрочка: сток занят бессрочно, пока проект не завершат.
    """
    occ_end = occupancy_end_expr()
    return and_(
        Project.status == ProjectStatus.SHIPPED,
        Project.start_date.is_not(None),
        occupancy_start_expr() <= end,
        or_(occ_end.is_(None), occ_end < today, occ_end >= start),
    )


def _reserving_overlap(start: date, end: date, today: date | None):
    """Бронь или «в работе», пересекающие период."""
    return or_(
        _booked_overlap(start, end),
        _in_work_overlap(start, end, today or local_today()),
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
    *,
    today: date | None = None,
) -> int:
    """Суммарная занятость модели (бронь + в работе) в других проектах (ТЗ §15)."""
    overlap = _reserving_overlap(start, end, today)
    return _sum_reservations(db, model_id, overlap, exclude_project_id)


def compute_availability(
    db: Session,
    model: EquipmentModel,
    start: date,
    end: date,
    *,
    required: int = 0,
    exclude_project_id: int | None = None,
    today: date | None = None,
) -> ModelAvailability:
    """Полная сводка доступности модели на период (ТЗ §15)."""
    today = today or local_today()
    return ModelAvailability(
        total=_total_stock(db, model),
        unavailable_by_status=_unavailable_by_status(db, model),
        reserved=_sum_reservations(db, model.id, _booked_overlap(start, end), exclude_project_id),
        in_work=_sum_reservations(
            db, model.id, _in_work_overlap(start, end, today), exclude_project_id
        ),
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
    *,
    today: date | None = None,
) -> int:
    """Сколько раз комплект занят в других пересекающихся проектах (ТЗ §15).

    Комплект — сформированная единица (его содержимое уходит с общего склада),
    поэтому занятость считается как у модели: по окну занятости проекта, а
    просроченная отгрузка держит комплект бессрочно.
    """
    stmt = (
        select(func.coalesce(func.sum(ProjectReservation.quantity), 0))
        .join(Project, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.kit_id == kit_id,
            _reserving_overlap(start, end, today),
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
    today: date | None = None,
) -> KitAvailability:
    """Доступность комплекта на период (свободен, если не забронирован другим проектом)."""
    return KitAvailability(
        reserved_other=reserved_kit_in_other_projects(
            db, kit_id, start, end, exclude_project_id, today=today
        )
    )


def occupancy_detail(
    db: Session,
    model_id: int,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
    *,
    today: date | None = None,
) -> list[OccupancyEntry]:
    """Детализация занятости модели по пересекающимся забронированным проектам (ТЗ §15)."""
    stmt = (
        select(Project, ProjectReservation.quantity)
        .join(ProjectReservation, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.model_id == model_id,
            _reserving_overlap(start, end, today),
        )
        .order_by(Project.start_date, Project.id)
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
                occupancy_start=project.occupancy_start,
                occupancy_end=project.occupancy_end,
            )
        )
    return entries


# --- Пакетный расчёт для списков (склад, подбор в смету/packing) -----------
#
# Те же правила, что у compute_availability/occupancy_detail, но одним набором
# GROUP BY-запросов на весь список вместо нескольких запросов на каждую модель.
# Результат обязан совпадать с поштучным расчётом (см. tests/unit/test_availability_bulk.py).

_IN_CHUNK = 500  # запас до лимита параметров в одном IN (SQLite/PostgreSQL)


def _chunks(ids: list[int]) -> list[list[int]]:
    return [ids[i : i + _IN_CHUNK] for i in range(0, len(ids), _IN_CHUNK)]


def _unique_ids(ids) -> list[int]:
    return list(dict.fromkeys(ids))


def _sum_reservations_by_model(
    db: Session, ids: list[int], overlap, exclude_project_id: int | None
) -> dict[int, int]:
    result: dict[int, int] = {}
    for chunk in _chunks(ids):
        stmt = (
            select(ProjectReservation.model_id, func.sum(ProjectReservation.quantity))
            .join(Project, Project.id == ProjectReservation.project_id)
            .where(ProjectReservation.model_id.in_(chunk), overlap)
            .group_by(ProjectReservation.model_id)
        )
        if exclude_project_id is not None:
            stmt = stmt.where(Project.id != exclude_project_id)
        result.update({mid: int(qty or 0) for mid, qty in db.execute(stmt).all()})
    return result


def compute_availability_bulk(
    db: Session,
    model_ids,
    start: date,
    end: date,
    *,
    exclude_project_id: int | None = None,
    today: date | None = None,
) -> dict[int, ModelAvailability]:
    """Доступность списка моделей на период: {model_id: ModelAvailability}.

    Эквивалент {id: compute_availability(...)} без запросов на каждую модель.
    """
    ids = _unique_ids(model_ids)
    if not ids:
        return {}
    today = today or local_today()

    stock: dict[int, tuple[int, int]] = {}
    for chunk in _chunks(ids):
        stmt = (
            select(
                EquipmentItem.model_id,
                func.count(),
                func.sum(case((_unavailable_clause(), 1), else_=0)),
            )
            .where(EquipmentItem.model_id.in_(chunk), EquipmentItem.kit_id.is_(None))
            .group_by(EquipmentItem.model_id)
        )
        stock.update(
            {mid: (int(total), int(bad or 0)) for mid, total, bad in db.execute(stmt).all()}
        )

    reserved = _sum_reservations_by_model(db, ids, _booked_overlap(start, end), exclude_project_id)
    in_work = _sum_reservations_by_model(
        db, ids, _in_work_overlap(start, end, today), exclude_project_id
    )
    return {
        mid: ModelAvailability(
            total=stock.get(mid, (0, 0))[0],
            unavailable_by_status=stock.get(mid, (0, 0))[1],
            reserved=reserved.get(mid, 0),
            in_work=in_work.get(mid, 0),
            required=0,
        )
        for mid in ids
    }


def occupancy_detail_bulk(
    db: Session,
    model_ids,
    start: date,
    end: date,
    exclude_project_id: int | None = None,
    *,
    today: date | None = None,
) -> dict[int, list[OccupancyEntry]]:
    """Детализация занятости для списка моделей: {model_id: [OccupancyEntry]}.

    Модели без занятости в результат не попадают.
    """
    ids = _unique_ids(model_ids)
    result: dict[int, list[OccupancyEntry]] = {}
    for chunk in _chunks(ids):
        stmt = (
            select(Project, ProjectReservation.model_id, ProjectReservation.quantity)
            .join(ProjectReservation, Project.id == ProjectReservation.project_id)
            .where(
                ProjectReservation.model_id.in_(chunk),
                _reserving_overlap(start, end, today),
            )
            .order_by(Project.start_date, Project.id)
        )
        if exclude_project_id is not None:
            stmt = stmt.where(Project.id != exclude_project_id)
        for project, model_id, quantity in db.execute(stmt).all():
            result.setdefault(model_id, []).append(
                OccupancyEntry(
                    project_id=project.id,
                    number=project.number,
                    name=project.name,
                    start_date=project.start_date,
                    end_date=project.end_date,
                    status=project.status,
                    quantity=quantity,
                    occupancy_start=project.occupancy_start,
                    occupancy_end=project.occupancy_end,
                )
            )
    return result


def compute_kit_availability_bulk(
    db: Session,
    kit_ids,
    start: date,
    end: date,
    *,
    exclude_project_id: int | None = None,
    today: date | None = None,
) -> dict[int, KitAvailability]:
    """Доступность списка комплектов на период: {kit_id: KitAvailability}."""
    ids = _unique_ids(kit_ids)
    if not ids:
        return {}
    booked: dict[int, int] = {}
    for chunk in _chunks(ids):
        stmt = (
            select(ProjectReservation.kit_id, func.sum(ProjectReservation.quantity))
            .join(Project, Project.id == ProjectReservation.project_id)
            .where(
                ProjectReservation.kit_id.in_(chunk),
                _reserving_overlap(start, end, today),
            )
            .group_by(ProjectReservation.kit_id)
        )
        if exclude_project_id is not None:
            stmt = stmt.where(Project.id != exclude_project_id)
        booked.update({kid: int(qty or 0) for kid, qty in db.execute(stmt).all()})
    return {kid: KitAvailability(reserved_other=booked.get(kid, 0)) for kid in ids}


# --- Planboard: доступность по дням на диапазон (календарь склада, §15.2) ----


@dataclass
class DayCell:
    """Состояние модели на конкретный день диапазона planboard."""

    day: date
    total: int
    reserved: int  # занято бронью (статус «Забронирован»)
    in_work: int  # занято отгружёнными (в работе)
    # Занятость этой модели продолжается до/после видимого окна (только для
    # крайних дней): показываем стрелку «‹»/«›» на границе периода.
    cont_before: bool = False
    cont_after: bool = False

    @property
    def available(self) -> int:
        return self.total - self.reserved - self.in_work

    @property
    def state(self) -> str:
        """Цвет ячейки: free (зелёный) / reserved (жёлтый) / busy (красный)."""
        if self.in_work > 0 or self.available <= 0:
            return "busy"
        if self.reserved > 0:
            return "reserved"
        return "free"


def _daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _count_by_model(db: Session, model_ids: list[int], *, only_unavailable: bool) -> dict[int, int]:
    """Свободный сток (или недоступный по статусу) по каждой модели одним запросом."""
    stmt = (
        select(EquipmentItem.model_id, func.count())
        .where(EquipmentItem.model_id.in_(model_ids), EquipmentItem.kit_id.is_(None))
        .group_by(EquipmentItem.model_id)
    )
    if only_unavailable:
        stmt = stmt.where(_unavailable_clause())
    return {mid: cnt for mid, cnt in db.execute(stmt).all()}


def compute_planboard(
    db: Session,
    model_ids: list[int],
    start: date,
    end: date,
    *,
    today: date | None = None,
) -> tuple[list[date], dict[int, list[DayCell]]]:
    """Доступность каждой модели по дням диапазона [start; end] включительно.

    Один оконный запрос по броням (не N×дней): пересекающие диапазон брони
    раскладываются по дням в Python. Бронь и отгрузка занимают окно занятости
    проекта (аренда, расширенная плановыми отгрузкой/возвратом); просроченная
    отгрузка — до конца видимого диапазона и дальше (§15.1).
    """
    days = _daterange(start, end)
    if not model_ids:
        return days, {}
    today = today or local_today()

    total = _count_by_model(db, model_ids, only_unavailable=False)
    unavail = _count_by_model(db, model_ids, only_unavailable=True)

    # (model_id, day) -> [reserved, in_work]
    busy: dict[tuple[int, date], list[int]] = {}
    # Модели, чья занятость выходит за левую/правую границу окна.
    cont_before: set[int] = set()
    cont_after: set[int] = set()
    stmt = (
        select(
            ProjectReservation.model_id,
            ProjectReservation.quantity,
            Project.status,
            occupancy_start_expr(),
            occupancy_end_expr(),
        )
        .join(Project, Project.id == ProjectReservation.project_id)
        .where(
            ProjectReservation.model_id.in_(model_ids),
            _reserving_overlap(start, end, today),
        )
    )
    for model_id, qty, status, w_start, w_end in db.execute(stmt).all():
        shipped = status == ProjectStatus.SHIPPED
        # Просроченная отгрузка (конец окна прошёл, проект не завершён) — бессрочно.
        open_ended = shipped and (w_end is None or w_end < today)
        occ_start = max(w_start, start)
        occ_end = end if open_ended else min(w_end, end)
        day = occ_start
        while day <= occ_end:
            bucket = busy.setdefault((model_id, day), [0, 0])
            bucket[1 if shipped else 0] += qty
            day += timedelta(days=1)
        # Занятость начинается раньше окна (крайняя левая ячейка модели занята).
        if w_start < start:
            cont_before.add(model_id)
        # Продолжается за правым краем: просрочка — бессрочно; иначе конец окна
        # занятости позже конца видимого диапазона.
        if open_ended or w_end > end:
            cont_after.add(model_id)

    rows: dict[int, list[DayCell]] = {}
    for mid in model_ids:
        base = (total.get(mid, 0)) - (unavail.get(mid, 0))
        cells = []
        for d in days:
            reserved, in_work = busy.get((mid, d), (0, 0))
            cells.append(DayCell(day=d, total=base, reserved=reserved, in_work=in_work))
        if cells and mid in cont_before:
            cells[0].cont_before = True
        if cells and mid in cont_after:
            cells[-1].cont_after = True
        rows[mid] = cells
    return days, rows
