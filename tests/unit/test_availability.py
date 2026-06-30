"""Доступность, пересечения дат и дефицит (ТЗ §13.3, §15)."""

from datetime import date
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.projects.availability import compute_availability, ranges_overlap
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


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


def _booked(db_session, model_id, qty, start, end, status=ProjectStatus.BOOKED):
    project = create_project(
        db_session, ProjectInput(name="Другой", start_date=start, end_date=end)
    )
    project.status = status
    db_session.add(ProjectReservation(project_id=project.id, model_id=model_id, quantity=qty))
    db_session.commit()
    return project


# --- Пересечение дат (ТЗ §13.3) -----------------------------------------


def test_ranges_overlap_inclusive():
    # окончание 10-го и начало 10-го = пересечение
    assert ranges_overlap(date(2026, 7, 1), date(2026, 7, 10), date(2026, 7, 10), date(2026, 7, 12))
    # смежные без общего дня — не пересекаются
    assert not ranges_overlap(
        date(2026, 7, 1), date(2026, 7, 9), date(2026, 7, 10), date(2026, 7, 12)
    )


# --- Доступность (ТЗ §15) -----------------------------------------------


def test_availability_basic(db_session, model):
    a = compute_availability(db_session, model, date(2026, 7, 1), date(2026, 7, 5), required=4)
    assert a.total == 10
    assert a.reserved_other == 0
    assert a.available == 10
    assert a.deficit == 0


def test_reserved_by_overlapping_booked(db_session, model):
    _booked(db_session, model.id, 7, date(2026, 7, 3), date(2026, 7, 8))
    a = compute_availability(db_session, model, date(2026, 7, 1), date(2026, 7, 5), required=5)
    assert a.reserved_other == 7
    assert a.available == 3
    assert a.deficit == 2


def test_non_overlapping_not_counted(db_session, model):
    _booked(db_session, model.id, 7, date(2026, 8, 1), date(2026, 8, 5))
    a = compute_availability(db_session, model, date(2026, 7, 1), date(2026, 7, 5))
    assert a.reserved_other == 0


def test_draft_does_not_reserve(db_session, model):
    _booked(db_session, model.id, 7, date(2026, 7, 1), date(2026, 7, 5), status=ProjectStatus.DRAFT)
    a = compute_availability(db_session, model, date(2026, 7, 1), date(2026, 7, 5))
    assert a.reserved_other == 0


def test_exclude_current_project(db_session, model):
    project = _booked(db_session, model.id, 4, date(2026, 7, 1), date(2026, 7, 5))
    a = compute_availability(
        db_session, model, date(2026, 7, 1), date(2026, 7, 5), exclude_project_id=project.id
    )
    assert a.reserved_other == 0


def test_serial_unavailable_by_status(db_session):
    cat = cat_service.create_category(db_session, "Свет")
    m = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Прожектор", accounting_type=AccountingType.SERIAL
        ),
    )
    for bc in ("A", "B", "C"):
        item_service.create_item(db_session, m, EquipmentItemInput(barcode=bc), user_id=None)
    # один в ремонт
    items = item_service.list_items(db_session, m.id)
    item_service.change_status(db_session, items[0], ItemStatus.REPAIR, user_id=None)
    a = compute_availability(db_session, m, date(2026, 7, 1), date(2026, 7, 5), required=3)
    assert a.total == 3
    assert a.unavailable_by_status == 1
    assert a.available == 2
    assert a.deficit == 1
