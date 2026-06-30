"""Бизнес-правила склада (ТЗ §7, §9, §10, §12, §21, §25)."""

from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType, ItemStatus, PackingType
from app.inventory.schemas import (
    EquipmentItemInput,
    EquipmentModelCreate,
    EquipmentModelUpdate,
    PackingRuleInput,
)
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service


@pytest.fixture
def category(db_session):
    return cat_service.create_category(db_session, "Звук")


def _qty_model(db_session, category, qty=10):
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Кабель XLR",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=qty,
            base_price_eur=Decimal("0"),
        ),
    )


def _serial_model(db_session, category):
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Микшер X32",
            accounting_type=AccountingType.SERIAL,
            weight_kg=Decimal("21.5"),
            base_price_eur=Decimal("150"),
        ),
    )


# --- Тип учёта неизменяем (ТЗ §7.3, §40.1) ------------------------------


def test_accounting_type_immutable_on_update(db_session, category):
    model = _serial_model(db_session, category)
    eq_service.update_model(
        db_session,
        model,
        EquipmentModelUpdate(category_id=category.id, name="Микшер X32 ред."),
    )
    assert model.accounting_type == AccountingType.SERIAL


def test_quantity_model_ignores_quantity_for_serial(db_session, category):
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Колонка",
            accounting_type=AccountingType.SERIAL,
            total_quantity=99,  # должно игнорироваться
        ),
    )
    assert model.total_quantity == 0


# --- Количественные остатки и история (ТЗ §10) --------------------------


def test_adjust_quantity_records_history(db_session, category):
    model = _qty_model(db_session, category, qty=10)
    adj = eq_service.adjust_quantity(db_session, model, 15, user_id=None, comment="приход")
    assert model.total_quantity == 15
    assert adj.old_quantity == 10
    assert adj.new_quantity == 15
    assert adj.delta == 5
    assert len(eq_service.quantity_history(db_session, model)) == 1


def test_adjust_quantity_rejects_serial(db_session, category):
    model = _serial_model(db_session, category)
    with pytest.raises(eq_service.InventoryError):
        eq_service.adjust_quantity(db_session, model, 5, user_id=None)


def test_adjust_quantity_rejects_negative(db_session, category):
    model = _qty_model(db_session, category)
    with pytest.raises(eq_service.InventoryError):
        eq_service.adjust_quantity(db_session, model, -1, user_id=None)


# --- Кейсы/рэки (ТЗ §12) ------------------------------------------------


def test_packing_packages_ceil():
    from app.inventory.models import PackingRule

    pr = PackingRule(packing_type=PackingType.CASE, capacity=4)
    assert pr.packages_for(0) == 0
    assert pr.packages_for(4) == 1
    assert pr.packages_for(5) == 2
    assert pr.packages_for(8) == 2
    assert pr.name_for("X32") == "Кейс для X32"


def test_model_with_packing(db_session, category):
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Прожектор",
            accounting_type=AccountingType.SERIAL,
            packing=PackingRuleInput(packing_type=PackingType.RACK, capacity=6),
        ),
    )
    assert model.has_packing
    assert model.packing.packages_for(7) == 2


# --- Экземпляры и штрих-коды (ТЗ §8.2, §9, §21) -------------------------


def test_create_item_sets_active_and_history(db_session, category):
    model = _serial_model(db_session, category)
    item = item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="BC-001"), user_id=None
    )
    assert item.status == ItemStatus.ACTIVE
    assert len(item.status_history) == 1


def test_duplicate_barcode_rejected(db_session, category):
    model = _serial_model(db_session, category)
    item_service.create_item(db_session, model, EquipmentItemInput(barcode="DUP"), user_id=None)
    with pytest.raises(item_service.DuplicateBarcode):
        item_service.create_item(db_session, model, EquipmentItemInput(barcode="DUP"), user_id=None)


def test_item_only_for_serial(db_session, category):
    model = _qty_model(db_session, category)
    with pytest.raises(eq_service.InventoryError):
        item_service.create_item(db_session, model, EquipmentItemInput(barcode="X"), user_id=None)


def test_change_status_records_history(db_session, category):
    model = _serial_model(db_session, category)
    item = item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="BC-2"), user_id=None
    )
    item_service.change_status(db_session, item, ItemStatus.REPAIR, user_id=None, comment="ремонт")
    assert item.status == ItemStatus.REPAIR
    # Создание + смена статуса.
    assert len(item.status_history) == 2


def test_change_status_noop_same(db_session, category):
    model = _serial_model(db_session, category)
    item = item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="BC-3"), user_id=None
    )
    item_service.change_status(db_session, item, ItemStatus.ACTIVE, user_id=None)
    assert len(item.status_history) == 1


def test_global_barcode_search(db_session, category):
    model = _serial_model(db_session, category)
    item_service.create_item(db_session, model, EquipmentItemInput(barcode="FIND-ME"), user_id=None)
    found = item_service.find_by_barcode(db_session, "FIND-ME")
    assert found is not None
    assert item_service.find_by_barcode(db_session, "NOPE") is None


def test_stock_quantity(db_session, category):
    qty = _qty_model(db_session, category, qty=7)
    assert eq_service.stock_quantity(db_session, qty) == 7
    serial = _serial_model(db_session, category)
    item_service.create_item(db_session, serial, EquipmentItemInput(barcode="S1"), user_id=None)
    item_service.create_item(db_session, serial, EquipmentItemInput(barcode="S2"), user_id=None)
    assert eq_service.stock_quantity(db_session, serial) == 2


# --- Категории (ТЗ §6.1) ------------------------------------------------


def test_delete_category_blocked_when_used(db_session, category):
    _qty_model(db_session, category)
    with pytest.raises(cat_service.InUse):
        cat_service.delete_category(db_session, category)


# --- Список и фильтры (ТЗ §25) ------------------------------------------


def test_list_models_filters_and_archive(db_session, category):
    _qty_model(db_session, category)
    serial = _serial_model(db_session, category)
    # По умолчанию — активные.
    active = eq_service.list_models(db_session, eq_service.ModelFilters())
    assert len(active) == 2
    # Поиск по названию.
    found = eq_service.list_models(db_session, eq_service.ModelFilters(query="Микшер"))
    assert len(found) == 1 and found[0].id == serial.id
    # Архивирование убирает из активного списка.
    eq_service.archive_model(db_session, serial, True)
    assert len(eq_service.list_models(db_session, eq_service.ModelFilters())) == 1
    archived = eq_service.list_models(db_session, eq_service.ModelFilters(archived=True))
    assert len(archived) == 1 and archived[0].id == serial.id
