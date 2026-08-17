"""Проекты: статусы, бронирование, копирование, удаление (ТЗ §13–§15)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects import service
from app.projects.enums import ProjectStatus
from app.projects.models import Project, ProjectReservation
from app.projects.schemas import ProjectInput


def _model(db_session, qty=5):
    cat = cat_service.create_category(db_session, "Звук")
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=qty,
        ),
    )


def test_deficits_skips_kit_reservation_without_warning(db_session):
    """Бронь комплекта (model_id пуст) не должна вызывать SAWarning о NULL-PK."""
    import warnings

    from sqlalchemy.exc import SAWarning

    from app.inventory.schemas import KitInput
    from app.inventory.services import kits as kit_service

    project = service.create_project(
        db_session,
        ProjectInput(
            name="С комплектом", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)
        ),
    )
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    db_session.add(ProjectReservation(project_id=project.id, kit_id=kit.id, quantity=1))
    db_session.commit()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        assert service.project_deficits(db_session, project) == []


def test_create_assigns_number_and_draft(db_session):
    p = service.create_project(db_session, ProjectInput(name="Концерт"))
    assert p.number.startswith("PRJ-")
    assert p.status == ProjectStatus.DRAFT


def test_book_requires_dates(db_session):
    p = service.create_project(db_session, ProjectInput(name="Без дат"))
    with pytest.raises(service.ValidationError):
        service.book_project(db_session, p)


def test_book_rejects_inverted_dates(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 10), end_date=date(2026, 7, 1)),
    )
    with pytest.raises(service.ValidationError):
        service.book_project(db_session, p)


def test_book_success_without_reservations(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    service.book_project(db_session, p)
    assert p.status == ProjectStatus.BOOKED


def test_book_deficit_requires_confirmation(db_session):
    model = _model(db_session, qty=3)
    # сосед бронирует 3 на пересекающийся период
    neighbor = service.create_project(
        db_session,
        ProjectInput(name="Сосед", start_date=date(2026, 7, 1), end_date=date(2026, 7, 9)),
    )
    neighbor.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=neighbor.id, model_id=model.id, quantity=3))
    db_session.commit()

    p = service.create_project(
        db_session,
        ProjectInput(name="Наш", start_date=date(2026, 7, 5), end_date=date(2026, 7, 12)),
    )
    db_session.add(ProjectReservation(project_id=p.id, model_id=model.id, quantity=2))
    db_session.commit()
    db_session.refresh(p)

    with pytest.raises(service.DeficitError) as exc:
        service.book_project(db_session, p)
    assert exc.value.lines[0].deficit == 2

    service.book_project(db_session, p, allow_deficit=True)
    assert p.status == ProjectStatus.BOOKED


def test_copy_resets_dates_status_number(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(
            name="Оригинал",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            customer="ООО Ромашка",
            vat=Decimal("19"),
        ),
    )
    p.status = ProjectStatus.BOOKED
    db_session.commit()

    copy = service.copy_project(db_session, p)
    assert copy.number != p.number
    assert copy.status == ProjectStatus.DRAFT
    assert copy.start_date is None and copy.end_date is None
    assert copy.name == "Оригинал (копия)"
    assert copy.customer == "ООО Ромашка"
    assert copy.vat == Decimal("19")


def test_delete_only_draft(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    service.book_project(db_session, p)
    with pytest.raises(service.ValidationError):
        service.delete_project(db_session, p)
    service.set_status(db_session, p, ProjectStatus.DRAFT)
    service.delete_project(db_session, p)
    assert db_session.get(Project, p.id) is None


def test_list_active_vs_archived(db_session):
    a = service.create_project(db_session, ProjectInput(name="Активный"))
    b = service.create_project(db_session, ProjectInput(name="Архивный"))
    service.set_status(db_session, b, ProjectStatus.CANCELLED)
    active_ids = {p.id for p in service.list_projects(db_session, archived=False)}
    archived_ids = {p.id for p in service.list_projects(db_session, archived=True)}
    assert a.id in active_ids and b.id not in active_ids
    assert b.id in archived_ids and a.id not in archived_ids


# --- Календарь проектов: диаграмма Ганта и наложения (ТЗ §13.9) ---------------


def test_compute_project_timeline_offsets_and_continuation(db_session):
    """Проект, торчащий за оба края окна, обрезается по видимому диапазону,
    но сохраняет флаги cont_before/cont_after."""
    service.create_project(
        db_session,
        ProjectInput(name="Долгий", start_date=date(2026, 6, 25), end_date=date(2026, 7, 15)),
    )
    days, rows, load = service.compute_project_timeline(
        db_session, date(2026, 7, 1), date(2026, 7, 10)
    )
    assert len(days) == 10
    assert len(rows) == 1
    row = rows[0]
    assert row.offset == 0
    assert row.length == 10
    assert row.cont_before is True
    assert row.cont_after is True
    assert row.overlaps == []
    assert load == [1] * 10


def test_compute_project_timeline_detects_overlap_and_load(db_session):
    service.create_project(
        db_session,
        ProjectInput(name="A", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    service.create_project(
        db_session,
        ProjectInput(name="B", start_date=date(2026, 7, 4), end_date=date(2026, 7, 8)),
    )
    days, rows, load = service.compute_project_timeline(
        db_session, date(2026, 7, 1), date(2026, 7, 10)
    )
    by_name = {r.project.name: r for r in rows}
    assert [p.name for p in by_name["A"].overlaps] == ["B"]
    assert [p.name for p in by_name["B"].overlaps] == ["A"]
    # 4 и 5 июля — оба проекта активны одновременно (индексы 3, 4 в окне с 1 июля).
    assert load[3] == 2 and load[4] == 2
    assert load[0] == 1 and load[7] == 1


def test_compute_project_timeline_excludes_undated_and_out_of_range(db_session):
    service.create_project(db_session, ProjectInput(name="Без дат"))
    service.create_project(
        db_session,
        ProjectInput(name="Вне окна", start_date=date(2026, 9, 1), end_date=date(2026, 9, 5)),
    )
    days, rows, load = service.compute_project_timeline(
        db_session, date(2026, 7, 1), date(2026, 7, 10)
    )
    assert rows == []
    assert load == [0] * 10


def test_list_projects_without_dates(db_session):
    undated = service.create_project(db_session, ProjectInput(name="Без дат"))
    service.create_project(
        db_session,
        ProjectInput(name="С датами", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    result = service.list_projects_without_dates(db_session, archived=False)
    assert [p.id for p in result] == [undated.id]


# --- Сетка месяца календаря проектов (ТЗ §13.9) -------------------------------


def _month_grid_range(month_start: date) -> tuple[date, date]:
    """Тот же расчёт полных недель Пн–Вс, что _month_grid_range() в app/projects/router.py."""
    import calendar as pycalendar

    days_in_month = pycalendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start + timedelta(days=days_in_month - 1)
    grid_start = month_start - timedelta(days=month_start.weekday())
    grid_last_week_start = month_end - timedelta(days=month_end.weekday())
    grid_end = grid_last_week_start + timedelta(days=6)
    return grid_start, grid_end


def test_build_project_grid_true_boundaries_not_window_edges(db_session):
    """Скругление торцов полосы — по настоящим start_date/end_date проекта, а не
    по краю подложки соседнего месяца (грид августа: 27.07–06.09.2026)."""
    service.create_project(
        db_session,
        ProjectInput(name="Долгий", start_date=date(2026, 7, 29), end_date=date(2026, 9, 5)),
    )
    month_start = date(2026, 8, 1)
    grid_start, grid_end = _month_grid_range(month_start)
    assert (grid_start, grid_end) == (date(2026, 7, 27), date(2026, 9, 6))

    days, rows, _load = service.compute_project_timeline(db_session, grid_start, grid_end)
    weeks = service.build_project_grid(days, rows, month_start)
    by_date = {d.date: d for week in weeks for d in week}

    # До настоящего начала — бара нет вовсе.
    assert by_date[date(2026, 7, 27)].bars == []
    assert by_date[date(2026, 7, 27)].in_month is False

    # День настоящего начала — is_start=True, даже в подложке июля (in_month=False).
    start_day = by_date[date(2026, 7, 29)]
    assert start_day.in_month is False
    assert start_day.bars[0].is_start is True

    # Обычный день внутри августа — продолжение, без скруглений.
    mid_day = by_date[date(2026, 8, 15)]
    assert mid_day.in_month is True
    assert mid_day.bars[0].is_start is False
    assert mid_day.bars[0].is_end is False

    # День настоящего конца — is_end=True, в подложке сентября.
    end_day = by_date[date(2026, 9, 5)]
    assert end_day.in_month is False
    assert end_day.bars[0].is_end is True

    # После конца — бара нет.
    assert by_date[date(2026, 9, 6)].bars == []


def test_build_project_grid_stable_order_for_overlapping_projects(db_session):
    service.create_project(
        db_session, ProjectInput(name="A", start_date=date(2026, 8, 10), end_date=date(2026, 8, 15))
    )
    service.create_project(
        db_session, ProjectInput(name="B", start_date=date(2026, 8, 12), end_date=date(2026, 8, 20))
    )
    month_start = date(2026, 8, 1)
    grid_start, grid_end = _month_grid_range(month_start)
    days, rows, _load = service.compute_project_timeline(db_session, grid_start, grid_end)
    weeks = service.build_project_grid(days, rows, month_start)
    by_date = {d.date: d for week in weeks for d in week}

    overlap_day = by_date[date(2026, 8, 12)]
    assert [b.project.name for b in overlap_day.bars] == ["A", "B"]
