"""Фактические даты отгрузки/возврата: авто-заполнение, валидация, влияние на
доступность (3 состояния + просрочка)."""

from datetime import date
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.availability import compute_availability
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import (
    ValidationError,
    _today,
    create_project,
    set_status,
    update_project,
)


@pytest.fixture
def model(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=10,
            base_price_eur=Decimal("0"),
        ),
    )


def _reserving(db_session, model_id, qty, start, end, status, returned=None):
    project = create_project(db_session, ProjectInput(name="P", start_date=start, end_date=end))
    project.status = status
    project.returned_date = returned
    db_session.add(ProjectReservation(project_id=project.id, model_id=model_id, quantity=qty))
    db_session.commit()
    return project


# --- 3 состояния: бронь vs в работе -------------------------------------


def test_booked_counts_as_reserved(db_session, model):
    _reserving(db_session, model.id, 3, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.BOOKED)
    a = compute_availability(db_session, model, date(2026, 7, 2), date(2026, 7, 4))
    assert a.reserved == 3
    assert a.in_work == 0
    assert a.available == 7


def test_shipped_counts_as_in_work(db_session, model):
    _reserving(db_session, model.id, 4, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    a = compute_availability(db_session, model, date(2026, 7, 2), date(2026, 7, 4))
    assert a.reserved == 0
    assert a.in_work == 4
    assert a.available == 6
    # обратная совместимость: суммарная занятость
    assert a.reserved_other == 4


# --- Просрочка: отгружено и не возвращено держит сток после конца аренды --


def test_overdue_shipped_blocks_after_rental_end(db_session, model):
    # Аренда закончилась 5 июля, сегодня 8-е, проект не завершён — держит сток и 10-го.
    _reserving(db_session, model.id, 6, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    a = compute_availability(
        db_session, model, date(2026, 7, 10), date(2026, 7, 12), today=date(2026, 7, 8)
    )
    assert a.in_work == 6
    assert a.available == 4


def test_shipped_not_overdue_frees_after_rental_end(db_session, model):
    # Отгружено 1–5, сегодня 3-е (просрочки нет) — на период с 10-го свободно.
    _reserving(db_session, model.id, 6, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    a = compute_availability(
        db_session, model, date(2026, 7, 10), date(2026, 7, 12), today=date(2026, 7, 3)
    )
    assert a.in_work == 0
    assert a.available == 10


def test_planned_return_before_period_frees_stock(db_session, model):
    # Плановый возврат 6 июля, сегодня 3-е — на период с 10-го свободно.
    _reserving(
        db_session,
        model.id,
        6,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 6),
    )
    a = compute_availability(
        db_session, model, date(2026, 7, 10), date(2026, 7, 12), today=date(2026, 7, 3)
    )
    assert a.in_work == 0
    assert a.available == 10


def test_planned_return_extends_shipped_occupancy(db_session, model):
    # Плановый возврат 11 июля позже конца аренды — занято и на 10–12.
    _reserving(
        db_session,
        model.id,
        6,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 11),
    )
    a = compute_availability(
        db_session, model, date(2026, 7, 10), date(2026, 7, 12), today=date(2026, 7, 3)
    )
    assert a.in_work == 6


def test_overdue_planned_return_still_blocks(db_session, model):
    # Плановый возврат 6-го прошёл (сегодня 8-е), проект не завершён — занято.
    project = _reserving(
        db_session,
        model.id,
        6,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 6),
    )
    period = (date(2026, 7, 10), date(2026, 7, 12))
    a = compute_availability(db_session, model, *period, today=date(2026, 7, 8))
    assert a.in_work == 6
    # Завершение освобождает сток.
    project.status = ProjectStatus.COMPLETED
    db_session.commit()
    a2 = compute_availability(db_session, model, *period, today=date(2026, 7, 8))
    assert a2.in_work == 0


# --- Окно занятости: плановые отгрузка/возврат расширяют даты аренды ------


def _booked_with_window(db_session, model_id, qty, shipped, returned):
    project = create_project(
        db_session,
        ProjectInput(
            name="Окно",
            start_date=date(2026, 7, 10),
            end_date=date(2026, 7, 12),
            shipped_date=shipped,
            returned_date=returned,
        ),
    )
    project.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=project.id, model_id=model_id, quantity=qty))
    db_session.commit()
    return project


def test_occupancy_window_properties(db_session):
    p = create_project(
        db_session,
        ProjectInput(name="P", start_date=date(2026, 7, 10), end_date=date(2026, 7, 12)),
    )
    assert (p.occupancy_start, p.occupancy_end) == (date(2026, 7, 10), date(2026, 7, 12))
    assert p.occupancy_differs is False
    p.shipped_date, p.returned_date = date(2026, 7, 9), date(2026, 7, 13)
    assert (p.occupancy_start, p.occupancy_end) == (date(2026, 7, 9), date(2026, 7, 13))
    assert p.occupancy_differs is True
    # Отгрузка позже начала / возврат раньше конца окно не сужают.
    p.shipped_date, p.returned_date = date(2026, 7, 11), date(2026, 7, 11)
    assert (p.occupancy_start, p.occupancy_end) == (date(2026, 7, 10), date(2026, 7, 12))


def test_booked_blocks_planned_shipping_and_return_days(db_session, model):
    _booked_with_window(db_session, model.id, 4, date(2026, 7, 9), date(2026, 7, 13))
    on_ship = compute_availability(db_session, model, date(2026, 7, 9), date(2026, 7, 9))
    on_return = compute_availability(db_session, model, date(2026, 7, 13), date(2026, 7, 13))
    before = compute_availability(db_session, model, date(2026, 7, 8), date(2026, 7, 8))
    after = compute_availability(db_session, model, date(2026, 7, 14), date(2026, 7, 14))
    assert on_ship.reserved == 4
    assert on_return.reserved == 4
    assert before.reserved == 0
    assert after.reserved == 0


def test_deficit_uses_occupancy_window(db_session, model):
    from app.projects.service import project_deficits

    # A: аренда 10–12 (8 шт.). B: аренда 13–15 (5 шт.). Дефицита нет.
    a = _booked_with_window(db_session, model.id, 8, None, None)
    b = create_project(
        db_session,
        ProjectInput(name="B", start_date=date(2026, 7, 13), end_date=date(2026, 7, 15)),
    )
    b.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=b.id, model_id=model.id, quantity=5))
    db_session.commit()
    assert project_deficits(db_session, b) == []

    # Плановый возврат A 13-го — пересекается с B: 8 + 5 > 10.
    a.returned_date = date(2026, 7, 13)
    db_session.commit()
    lines = project_deficits(db_session, b)
    assert len(lines) == 1 and lines[0].deficit == 3

    # Плановая отгрузка B 12-го — дефицит видит и сам A.
    a.returned_date = None
    b.shipped_date = date(2026, 7, 12)
    db_session.commit()
    assert project_deficits(db_session, a)[0].deficit == 3


def test_booked_does_not_block_after_rental_end(db_session, model):
    # Забронированный (не отгружённый) не держит сток после конца аренды.
    _reserving(db_session, model.id, 6, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.BOOKED)
    a = compute_availability(db_session, model, date(2026, 7, 10), date(2026, 7, 12))
    assert a.reserved == 0
    assert a.available == 10


# --- Авто-заполнение при смене статуса ----------------------------------


def test_set_status_shipped_autofills_shipped_date(db_session):
    project = create_project(
        db_session, ProjectInput(name="P", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    set_status(db_session, project, ProjectStatus.SHIPPED)
    assert project.shipped_date == _today()
    assert project.returned_date is None


def test_set_status_completed_autofills_returned_date(db_session):
    project = create_project(
        db_session, ProjectInput(name="P", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    set_status(db_session, project, ProjectStatus.COMPLETED)
    assert project.returned_date == _today()


def test_manual_dates_preserved_on_status_change(db_session):
    manual = date(2026, 6, 30)
    project = create_project(
        db_session,
        ProjectInput(
            name="P",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            shipped_date=manual,
        ),
    )
    set_status(db_session, project, ProjectStatus.SHIPPED)
    assert project.shipped_date == manual  # авто не перезаписывает вручную заданное


# --- Валидация: возврат не раньше отгрузки ------------------------------


def test_returned_before_shipped_rejected_on_update(db_session):
    project = create_project(db_session, ProjectInput(name="P"))
    with pytest.raises(ValidationError):
        update_project(
            db_session,
            project,
            ProjectInput(
                name="P",
                shipped_date=date(2026, 7, 10),
                returned_date=date(2026, 7, 5),
            ),
        )


def test_dates_persisted(db_session):
    project = create_project(
        db_session,
        ProjectInput(
            name="P",
            shipped_date=date(2026, 7, 2),
            returned_date=date(2026, 7, 9),
        ),
    )
    db_session.refresh(project)
    assert project.shipped_date == date(2026, 7, 2)
    assert project.returned_date == date(2026, 7, 9)
