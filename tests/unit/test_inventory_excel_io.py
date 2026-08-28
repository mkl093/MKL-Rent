"""Экспорт карточки модели в Excel и обратный импорт (параметры + единицы)."""

import io
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType, ItemStatus, PackingType
from app.inventory.schemas import (
    AccessoryQty,
    EquipmentItemInput,
    EquipmentModelCreate,
    EquipmentModelUpdate,
    PackingRuleInput,
)
from app.inventory.services import accessories as acc_service
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import excel_io
from app.inventory.services import items as item_service


@pytest.fixture
def category(db_session):
    return cat_service.create_category(db_session, "Свет")


def _serial_model(db_session, category):
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Прожектор Aura",
            accounting_type=AccountingType.SERIAL,
            weight_kg=Decimal("12.5"),
            base_price_eur=Decimal("80"),
            manufacturer="Ayrton",
            has_power=True,
            power_peak_w=800,
            power_nominal_w=650,
            packing=PackingRuleInput(packing_type=PackingType.CASE, capacity=4),
        ),
    )


def _qty_model(db_session, category, qty=10):
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=category.id,
            name="Кабель XLR 10м",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=qty,
        ),
    )


def _bytes(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _find_row(ws, label: str) -> int:
    for r in range(1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == label:
            return r
    raise AssertionError(f"label not found: {label}")


def _set_field(wb, label: str, value) -> None:
    ws = wb[excel_io.CARD_SHEET]
    row = _find_row(ws, label)
    ws.cell(row=row, column=2, value=value)


# --- Round-trip без изменений --------------------------------------------


def test_export_import_roundtrip_no_changes(db_session, category):
    model = _serial_model(db_session, category)
    item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="AURA-001"), user_id=None
    )
    wb = excel_io.build_workbook(db_session, model)
    ws = wb[excel_io.ITEMS_SHEET]
    assert ws.cell(row=1, column=1).value == "Модель"
    assert ws.cell(row=2, column=1).value == "Прожектор Aura"
    result = excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert result.items_created == 0
    assert result.items_updated == 0
    assert result.quantity_adjusted is False
    assert model.name == "Прожектор Aura"
    assert model.manufacturer == "Ayrton"
    assert model.packing.packing_type == PackingType.CASE


def test_export_includes_accessories_and_reimport_keeps_them(db_session, category):
    acc_cat = acc_service.create_category(db_session, "Такелаж")
    clamp = acc_service.create_accessory(db_session, acc_cat, "Клемп 50 мм")
    model = _serial_model(db_session, category)
    eq_service.update_model(
        db_session,
        model,
        EquipmentModelUpdate(
            category_id=category.id,
            name=model.name,
            accessories=[AccessoryQty(accessory_id=clamp.id, quantity=3)],
        ),
    )
    wb = excel_io.build_workbook(db_session, model)
    excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert len(model.accessories) == 1
    assert model.accessories[0].quantity == 3


# --- Изменение параметров модели ------------------------------------------


def test_import_updates_model_fields(db_session, category):
    model = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    _set_field(wb, excel_io.FIELD_LABELS["manufacturer"], "Robe")
    _set_field(wb, excel_io.FIELD_LABELS["weight_kg"], 13.2)
    excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert model.manufacturer == "Robe"
    assert model.weight_kg == Decimal("13.2")


def test_import_rejects_wrong_model_id(db_session, category):
    model = _serial_model(db_session, category)
    other = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    _set_field(wb, excel_io.FIELD_LABELS["id"], other.id)
    with pytest.raises(excel_io.ImportValidationError):
        excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert model.manufacturer == "Ayrton"  # ничего не применилось


def test_import_rejects_unknown_category(db_session, category):
    model = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    _set_field(wb, excel_io.FIELD_LABELS["category"], "Нет такой категории")
    with pytest.raises(excel_io.ImportValidationError) as exc:
        excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert "категория" in str(exc.value).lower() or "Категория" in exc.value.errors[0]
    assert model.name == "Прожектор Aura"


def test_import_rejects_accounting_type_change(db_session, category):
    model = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    _set_field(wb, excel_io.FIELD_LABELS["accounting_type"], "Количественный")
    with pytest.raises(excel_io.ImportValidationError):
        excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert model.accounting_type == AccountingType.SERIAL


# --- Единицы оборудования --------------------------------------------------


def test_import_adds_new_item_row(db_session, category):
    model = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    ws = wb[excel_io.ITEMS_SHEET]
    row = ws.max_row + 1
    ws.cell(row=row, column=3, value="AURA-NEW")
    ws.cell(row=row, column=6, value="Активно")
    result = excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert result.items_created == 1
    items = item_service.list_items(db_session, model.id)
    assert any(it.barcode == "AURA-NEW" for it in items)


def test_import_updates_existing_item_status_with_history(db_session, category):
    model = _serial_model(db_session, category)
    item = item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="AURA-002"), user_id=None
    )
    wb = excel_io.build_workbook(db_session, model)
    ws = wb[excel_io.ITEMS_SHEET]
    row = _row_for_item(ws, item.id)
    ws.cell(row=row, column=6, value="В ремонте")
    ws.cell(row=row, column=8, value="Сломался мотор")
    result = excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert result.items_updated == 1
    db_session.refresh(item)
    assert item.status == ItemStatus.REPAIR
    assert item.comment == "Сломался мотор"
    assert item.status_history[0].new_status == ItemStatus.REPAIR


def test_import_does_not_delete_missing_rows(db_session, category):
    model = _serial_model(db_session, category)
    item_service.create_item(
        db_session, model, EquipmentItemInput(barcode="AURA-003"), user_id=None
    )
    wb = excel_io.build_workbook(db_session, model)
    ws = wb[excel_io.ITEMS_SHEET]
    ws.delete_rows(2, ws.max_row)  # убираем все строки единиц из файла
    excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    items = item_service.list_items(db_session, model.id)
    assert len(items) == 1  # единица не удалена


def test_import_rejects_duplicate_barcode_in_file(db_session, category):
    model = _serial_model(db_session, category)
    wb = excel_io.build_workbook(db_session, model)
    ws = wb[excel_io.ITEMS_SHEET]
    ws.cell(row=2, column=3, value="DUP")
    ws.cell(row=2, column=6, value="Активно")
    ws.cell(row=3, column=3, value="DUP")
    ws.cell(row=3, column=6, value="Активно")
    with pytest.raises(excel_io.ImportValidationError):
        excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)


def test_import_adjusts_quantity_for_quantity_model(db_session, category):
    model = _qty_model(db_session, category, qty=10)
    wb = excel_io.build_workbook(db_session, model)
    _set_field(wb, excel_io.FIELD_LABELS["total_quantity"], 15)
    result = excel_io.import_workbook(db_session, model, _bytes(wb), user_id=None)
    assert result.quantity_adjusted is True
    assert eq_service.active_count(db_session, model.id) == 15


def _row_for_item(ws, item_id: int) -> int:
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=2).value == item_id:
            return r
    raise AssertionError(f"item row not found: {item_id}")
