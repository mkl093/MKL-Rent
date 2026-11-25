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


def _reserve(db_session, model_id, qty, start, end, status, returned=None, shipped=None):
    project = create_project(db_session, ProjectInput(name="P", start_date=start, end_date=end))
    project.status = status
    project.shipped_date = shipped
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
    # Отгружено 1–5, сегодня 7-е, проект не завершён: держит сток и после конца аренды.
    _reserve(db_session, model.id, 3, date(2026, 7, 1), date(2026, 7, 5), ProjectStatus.SHIPPED)
    days, rows = compute_planboard(
        db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10), today=date(2026, 7, 7)
    )
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 4)).in_work == 3
    assert _cell(cells, date(2026, 7, 4)).state == "busy"
    # 8-го аренда уже кончилась, но не возвращено — всё ещё занято.
    assert _cell(cells, date(2026, 7, 8)).in_work == 3


def test_planned_return_extends_shipped_until_return_date(db_session, model):
    _reserve(
        db_session,
        model.id,
        3,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 7),
    )
    # Сегодня 2-е: просрочки нет, занято до планового возврата включительно.
    days, rows = compute_planboard(
        db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10), today=date(2026, 7, 2)
    )
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 7)).in_work == 3  # день возврата ещё занят
    assert _cell(cells, date(2026, 7, 8)).in_work == 0  # со следующего дня свободно
    assert cells[-1].cont_after is False


def test_return_before_rental_end_does_not_shorten_window(db_session, model):
    # Возврат раньше окончания аренды окно не сокращает: занято до конца аренды.
    _reserve(
        db_session,
        model.id,
        3,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 4),
    )
    days, rows = compute_planboard(
        db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10), today=date(2026, 7, 2)
    )
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 5)).in_work == 3
    assert _cell(cells, date(2026, 7, 6)).in_work == 0


def test_overdue_planned_return_holds_until_completed(db_session, model):
    # Плановый возврат 7-го прошёл (сегодня 9-е), проект не завершён — занято дальше.
    _reserve(
        db_session,
        model.id,
        3,
        date(2026, 7, 1),
        date(2026, 7, 5),
        ProjectStatus.SHIPPED,
        returned=date(2026, 7, 7),
    )
    days, rows = compute_planboard(
        db_session, [model.id], date(2026, 7, 1), date(2026, 7, 10), today=date(2026, 7, 9)
    )
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 10)).in_work == 3
    assert cells[-1].cont_after is True


def test_booked_window_includes_shipping_and_return_days(db_session, model):
    # Аренда 10–12, отгрузка 9-го, возврат 13-го — бронь занимает 9–13.
    _reserve(
        db_session,
        model.id,
        4,
        date(2026, 7, 10),
        date(2026, 7, 12),
        ProjectStatus.BOOKED,
        shipped=date(2026, 7, 9),
        returned=date(2026, 7, 13),
    )
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 8), date(2026, 7, 14))
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 8)).state == "free"
    assert _cell(cells, date(2026, 7, 9)).reserved == 4
    assert _cell(cells, date(2026, 7, 13)).reserved == 4
    assert _cell(cells, date(2026, 7, 14)).state == "free"


def test_window_arrows_use_shipping_and_return_dates(db_session, model):
    # Аренда целиком внутри окна, но отгрузка/возврат выходят за его края.
    _reserve(
        db_session,
        model.id,
        2,
        date(2026, 7, 10),
        date(2026, 7, 12),
        ProjectStatus.BOOKED,
        shipped=date(2026, 7, 7),
        returned=date(2026, 7, 16),
    )
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 8), date(2026, 7, 14))
    cells = rows[model.id]
    assert cells[0].cont_before is True
    assert cells[-1].cont_after is True


def test_deficit_day_is_busy(db_session, model):
    _reserve(db_session, model.id, 12, date(2026, 7, 3), date(2026, 7, 3), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 5))
    cell = _cell(rows[model.id], date(2026, 7, 3))
    assert cell.available == -2
    assert cell.state == "busy"


def test_booking_across_month_boundary(db_session, model):
    """Бронь с конца июля по начало августа — непрерывна на стыке месяцев."""
    _reserve(db_session, model.id, 4, date(2026, 7, 28), date(2026, 8, 3), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 25), date(2026, 8, 5))
    cells = rows[model.id]
    assert _cell(cells, date(2026, 7, 27)).state == "free"
    for d in (
        date(2026, 7, 28),
        date(2026, 7, 31),
        date(2026, 8, 1),
        date(2026, 8, 3),
    ):
        c = _cell(cells, d)
        assert c.reserved == 4 and c.available == 6 and c.state == "reserved"
    assert _cell(cells, date(2026, 8, 4)).state == "free"


def test_booking_clamped_to_window_edges(db_session, model):
    """Бронь длиннее окна: видимые дни заняты, край окна не «теряется»."""
    # Длинная бронь, выходящая за оба края окна.
    _reserve(db_session, model.id, 2, date(2026, 7, 20), date(2026, 8, 31), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 25), date(2026, 8, 5))
    cells = rows[model.id]
    assert len(cells) == 12
    # Все дни окна, включая первый и последний, заняты.
    assert all(c.reserved == 2 for c in cells)
    assert cells[0].day == date(2026, 7, 25) and cells[-1].day == date(2026, 8, 5)


def test_edge_continuation_flags(db_session, model):
    """Бронь выходит за оба края окна — на крайних ячейках стоят флаги продолжения."""
    _reserve(db_session, model.id, 2, date(2026, 7, 20), date(2026, 8, 10), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 25), date(2026, 8, 5))
    cells = rows[model.id]
    assert cells[0].cont_before is True
    assert cells[-1].cont_after is True


def test_no_continuation_when_inside_window(db_session, model):
    """Бронь целиком внутри окна — флагов продолжения нет."""
    _reserve(db_session, model.id, 2, date(2026, 7, 26), date(2026, 8, 4), ProjectStatus.BOOKED)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 25), date(2026, 8, 5))
    cells = rows[model.id]
    assert cells[0].cont_before is False
    assert cells[-1].cont_after is False


def test_shipped_unreturned_continues_after_window(db_session, model):
    """Отгружено и не возвращено — тянется за правый край окна (просрочка)."""
    _reserve(db_session, model.id, 2, date(2026, 7, 28), date(2026, 7, 30), ProjectStatus.SHIPPED)
    days, rows = compute_planboard(
        db_session, [model.id], date(2026, 7, 25), date(2026, 8, 5), today=date(2026, 8, 1)
    )
    cells = rows[model.id]
    assert cells[-1].cont_after is True
    assert cells[0].cont_before is False


def test_draft_not_counted(db_session, model):
    _reserve(db_session, model.id, 4, date(2026, 7, 3), date(2026, 7, 5), ProjectStatus.DRAFT)
    days, rows = compute_planboard(db_session, [model.id], date(2026, 7, 1), date(2026, 7, 7))
    assert all(c.state == "free" for c in rows[model.id])
