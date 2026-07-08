"""Структура «Комплект»: исключение из свободного стока, бронь, документы."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, KitWeightMode
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate, KitInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.packing import service as pack_service
from app.projects.availability import compute_availability, compute_kit_availability
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


@pytest.fixture
def serial_model(db_session):
    cat = cat_service.create_category(db_session, "Свет")
    m = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Прожектор", accounting_type=AccountingType.SERIAL
        ),
    )
    for bc in ("L1", "L2", "L3"):
        item_service.create_item(db_session, m, EquipmentItemInput(barcode=bc), user_id=None)
    return m


def test_placing_item_in_kit_reduces_free_stock(db_session, serial_model):
    """Помещение единицы в комплект вычитает её из свободного остатка модели."""
    assert eq_service.stock_quantity(db_session, serial_model) == 3
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс света"))
    items = item_service.list_items(db_session, serial_model.id)

    moved = kit_service.add_items(db_session, kit, [items[0].id])
    assert moved == 1
    assert eq_service.stock_quantity(db_session, serial_model) == 2
    assert eq_service.kit_count(db_session, serial_model.id) == 1
    # Доступность учитывает исключение из свободного стока.
    a = compute_availability(db_session, serial_model, date(2026, 7, 1), date(2026, 7, 5))
    assert a.total == 2


def test_removing_item_returns_to_free_stock(db_session, serial_model):
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    items = item_service.list_items(db_session, serial_model.id)
    kit_service.add_items(db_session, kit, [items[0].id, items[1].id])
    assert eq_service.stock_quantity(db_session, serial_model) == 1

    kit_service.remove_item(db_session, kit, items[0].id)
    assert eq_service.stock_quantity(db_session, serial_model) == 2
    assert items[0].kit_id is None


def test_adjust_quantity_keeps_kit_units(db_session):
    """Быстрое уменьшение остатка не трогает единицы, помещённые в комплект."""
    cat = cat_service.create_category(db_session, "Кабели")
    m = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="XLR",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=5,
            base_price_eur=Decimal("0"),
        ),
    )
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс кабелей"))
    items = item_service.list_items(db_session, m.id)
    kit_service.add_items(db_session, kit, [items[0].id])
    assert eq_service.active_count(db_session, m.id) == 4

    # Понижаем свободный остаток до 1 — удаляются только свободные единицы.
    eq_service.adjust_quantity(db_session, m, 1, user_id=None)
    assert eq_service.stock_quantity(db_session, m) == 1
    assert eq_service.kit_count(db_session, m.id) == 1
    assert items[0].kit_id == kit.id


def test_kit_booking_and_availability(db_session, serial_model):
    """Бронь комплекта делает его занятым в пересекающемся периоде другого проекта."""
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    other = create_project(
        db_session,
        ProjectInput(name="Другой", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    other.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=other.id, kit_id=kit.id, quantity=1))
    db_session.commit()

    ka = compute_kit_availability(db_session, kit.id, date(2026, 7, 3), date(2026, 7, 8))
    assert ka.reserved_other == 1
    assert ka.available is False
    # Исключение текущего проекта — снова свободен.
    ka2 = compute_kit_availability(
        db_session, kit.id, date(2026, 7, 3), date(2026, 7, 8), exclude_project_id=other.id
    )
    assert ka2.available is True


def test_overdue_shipped_kit_stays_busy_after_rental_end(db_session):
    """Отгружённый и не возвращённый комплект занят и после конца аренды (просрочка)."""
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    proj = create_project(
        db_session,
        ProjectInput(name="Отгружен", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    proj.status = ProjectStatus.SHIPPED
    db_session.add(ProjectReservation(project_id=proj.id, kit_id=kit.id, quantity=1))
    db_session.commit()

    # Аренда закончилась 5-го, возврат не отмечен — комплект занят и 10-го.
    ka = compute_kit_availability(db_session, kit.id, date(2026, 7, 10), date(2026, 7, 12))
    assert ka.available is False

    # После возврата 6-го — на период с 10-го снова свободен.
    proj.returned_date = date(2026, 7, 6)
    db_session.commit()
    ka2 = compute_kit_availability(db_session, kit.id, date(2026, 7, 10), date(2026, 7, 12))
    assert ka2.available is True


def test_estimate_kit_line_creates_reservation(db_session):
    """Добавление комплекта в смету создаёт бронь-комплект (одна позиция)."""
    kit = kit_service.create_kit(db_session, KitInput(name="Комплект A"))
    project = create_project(
        db_session, ProjectInput(name="P", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db_session, project)

    line = est_service.add_kit_line(db_session, estimate, project, kit)
    assert line is not None and line.is_kit and line.quantity == 1
    # Повторно тот же комплект не добавляется.
    assert est_service.add_kit_line(db_session, estimate, project, kit) is None

    res = (
        db_session.query(ProjectReservation)
        .filter(ProjectReservation.project_id == project.id, ProjectReservation.kit_id == kit.id)
        .one()
    )
    assert res.quantity == 1


def test_packing_from_estimate_includes_kit_with_composition(db_session, serial_model):
    """Packing-лист из сметы содержит строку-комплект с перечнем комплектации."""
    kit = kit_service.create_kit(db_session, KitInput(name="Комплект B"))
    items = item_service.list_items(db_session, serial_model.id)
    kit_service.add_items(db_session, kit, [items[0].id, items[1].id])

    project = create_project(
        db_session, ProjectInput(name="P", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_kit_line(db_session, estimate, project, kit)

    packing = pack_service.create_from_estimate(db_session, project)
    kit_lines = [ln for ln in packing.lines if ln.is_kit]
    assert len(kit_lines) == 1
    assert kit_lines[0].name == "Комплект B"

    groups = kit_service.content_groups(kit)
    assert groups[0].model_name == "Прожектор"
    assert groups[0].count == 2


@pytest.fixture
def weighted_model(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    m = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.SERIAL,
            weight_kg=Decimal("10"),
        ),
    )
    for bc in ("W1", "W2"):
        item_service.create_item(db_session, m, EquipmentItemInput(barcode=bc), user_id=None)
    return m


def test_kit_weight_content_mode(db_session, weighted_model):
    """По умолчанию вес комплекта = сумма веса содержимого."""
    kit = kit_service.create_kit(db_session, KitInput(name="K"))
    items = item_service.list_items(db_session, weighted_model.id)
    kit_service.add_items(db_session, kit, [i.id for i in items])
    assert kit_service.content_weight(kit) == Decimal("20")
    assert kit_service.total_weight(kit) == Decimal("20")


def test_kit_weight_packaging_mode(db_session, weighted_model):
    """Режим «содержимое + упаковка»: к весу содержимого прибавляется вес упаковки."""
    kit = kit_service.create_kit(
        db_session,
        KitInput(name="K", weight_mode=KitWeightMode.PACKAGING, weight_value=Decimal("5.5")),
    )
    items = item_service.list_items(db_session, weighted_model.id)
    kit_service.add_items(db_session, kit, [i.id for i in items])
    assert kit_service.total_weight(kit) == Decimal("25.5")


def test_kit_weight_total_mode_ignores_content(db_session, weighted_model):
    """Режим «фиксированный общий вес»: содержимое не учитывается."""
    kit = kit_service.create_kit(
        db_session,
        KitInput(name="K", weight_mode=KitWeightMode.TOTAL, weight_value=Decimal("30")),
    )
    items = item_service.list_items(db_session, weighted_model.id)
    kit_service.add_items(db_session, kit, [i.id for i in items])
    assert kit_service.total_weight(kit) == Decimal("30")


def test_kit_weight_value_cleared_in_content_mode(db_session):
    """В режиме «по содержимому» числовое значение веса не сохраняется."""
    kit = kit_service.create_kit(
        db_session,
        KitInput(name="K", weight_mode=KitWeightMode.CONTENT, weight_value=Decimal("99")),
    )
    assert kit.weight_value is None


# --- Веб-поток -----------------------------------------------------------


@pytest.fixture
def auth_client(client, db_session):
    from app.auth import service as auth_service

    auth_service.create_user(db_session, "admin", "pass123")
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    return client


def _csrf(client, url):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_kit_web_create_add_remove(auth_client, db_session, serial_model):
    # Создание комплекта
    token = _csrf(auth_client, "/inventory/kits")
    resp = auth_client.post(
        "/inventory/kits",
        data={"name": "Кейс DJ", "description": "тест", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    kit_id = int(resp.headers["location"].rstrip("/").split("/")[-1])

    # Пикер свободных единиц показывает единицы модели
    picker = auth_client.get(f"/inventory/kits/{kit_id}/add").text
    item_ids = re.findall(r'name="select_(\d+)"', picker)
    assert len(item_ids) == 3

    # Добавляем две единицы
    token = _csrf(auth_client, f"/inventory/kits/{kit_id}/add")
    auth_client.post(
        f"/inventory/kits/{kit_id}/add",
        data={f"select_{item_ids[0]}": "1", f"select_{item_ids[1]}": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert eq_service.stock_quantity(db_session, serial_model) == 1

    detail = auth_client.get(f"/inventory/kits/{kit_id}").text
    assert "Прожектор" in detail

    # Извлекаем одну единицу
    token = _csrf(auth_client, f"/inventory/kits/{kit_id}")
    auth_client.post(
        f"/inventory/kits/{kit_id}/items/{item_ids[0]}/remove",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert eq_service.stock_quantity(db_session, serial_model) == 2
