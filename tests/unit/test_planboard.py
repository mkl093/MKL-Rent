"""Planboard: раскладка занятости по дням диапазона (календарь склада, §15.2)."""

from datetime import date
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.availability import compute_planboard
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


def _reserve(db_session, model_id, qty, start, end, status, returned=None):
    project = create_project(db_session, ProjectInput(name="P", start_date=start, end_date=end))
    project.status = status
    project.returned_date = returned
    db_session.add(ProjectReservation(project_id=project.id, model_id=model_id, quantity=qty))
    db_session.commit()
    return project


def _cell(cells, d):
    return next(c for c in cells if c.day == d)


def test_range_length_and_free(db_session, model):
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 7))
    assert len(days) == 7
    assert days[0] == date(2026, 7, 1) and days[-1] == date(2026, 7, 7)
    cells = rows[model.id]
    assert all(c.state == "free" and c.available == 10 for c in cells)


def test_booked_days_only_within_rental(db_session, model):
    _reserve(db_session, model.id, 4, date(2026, 7, 3), date(2026, 7, 5), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 7))
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 2)).state == "free"
    mid = _cell(cells, date(2026, 7, 4))
    assert mid.reserved == 4 and mid.available == 6 and mid.state == "reserved"
    assert _cell(cells, date(2026, 7, 6)).state == "free"


def test_shipped_is_busy_and_overdue_holds(db_session, model):
    # Отгружено 1–5, возврат не отмечен: держит сток и после конца аренды.
    _reserve(db_session, model.id, 3, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10))
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 4)).in_work == 3
    assert _cell(cells, date(2026, 7, 4)).state == "busy"
    # 8-го аренда уже кончилась, но не возвращено — всё ещё занято.
    assert _cell(cells, date(2026, 7, 8)).in_work == 3


def test_returned_frees_from_return_date(db_session, model):
    _reserve(
        db_session,
        model.id,
        3,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 4),
    )
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10))
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 4)).in_work == 3  # день возврата ещё занят
    assert _cell(cells, date(2026, 7, 5)).in_work == 0  # со следующего дня свободно


def test_deficit_day_is_busy(db_session, model):
    _reserve(db_session, model.id, 12, date(2026, 7, 3), date(2026, 7, 3), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 5))
    cell = _cell(rows[model.id], date(2026, 7, 3))
    assert cell.available == -2
    assert cell.state == "busy"


def test_draft_not_counted(db_session, model):
    _reserve(db_session, model.id, 4, date(2026, 7, 3), date(2026, 7, 5), ProjectStatus.DRAFT)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 7))
    assert all(c.state == "free" for c in rows[model.id])
