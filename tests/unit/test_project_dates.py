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
    # Аренда закончилась 5 июля, возврат не отмечен — держит сток и 10-го.
    _reserving(db_session, model.id, 6, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    a = compute_availability(db_session, model, date(2026, 7, 10), date(2026, 7, 12))
    assert a.in_work == 6
    assert a.available == 4


def test_returned_before_period_frees_stock(db_session, model):
    # Возвращено 6 июля — на период с 10-го уже свободно.
    _reserving(
        db_session,
        model.id,
        6,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 6),
    )
    a = compute_availability(db_session, model, date(2026, 7, 10), date(2026, 7, 12))
    assert a.in_work == 0
    assert a.available == 10


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
