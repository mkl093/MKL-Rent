"""Packing-лист: расчёты, создание, упаковка, серийные, статусы (ТЗ §17–§20)."""

from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, ItemStatus, PackingType
from app.inventory.schemas import (
    EquipmentItemInput,
    EquipmentModelCreate,
    PackingRuleInput,
)
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.packing import service
from app.packing.calc import compute_line, packages_count, unit_volume_m3
from app.packing.enums import PackingStatus
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput


@pytest.fixture
def env(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    qty_model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("2.0"),
            length_mm=100,
            width_mm=100,
            height_mm=100,
            packing=PackingRuleInput(
                packing_type=PackingType.CASE,
                capacity=4,
                empty_weight_kg=Decimal("1.0"),
                length_mm=500,
                width_mm=400,
                height_mm=300,
            ),
        ),
    )
    serial_model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Микшер", accounting_type=AccountingType.SERIAL
        ),
    )
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, qty_model, 10)
    est_service.add_model(db_session, estimate, project, serial_model, 2)
    return db_session, project, qty_model, serial_model


# --- Расчёты (ТЗ §12, §18–§20) ------------------------------------------


def test_packages_ceil():
    assert packages_count(10, True, 4) == 3
    assert packages_count(4, True, 4) == 1
    assert packages_count(0, True, 4) == 0
    assert packages_count(10, False, 4) == 0


def test_unit_volume():
    assert unit_volume_m3(100, 100, 100) == Decimal("0.001")
    assert unit_volume_m3(500, 400, 300) == Decimal("0.06")


# --- Создание из сметы (ТЗ §17.1) ---------------------------------------


def test_create_from_estimate(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    assert packing.number.startswith("PL-")
    assert len(packing.lines) == 2

    qty_line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert qty_line.planned_quantity == 10
    assert qty_line.quantity == 10  # факт = план для количественных
    assert qty_line.packed_quantity == 10  # всё упаковано по умолчанию (ТЗ §18)

    serial_line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    assert serial_line.planned_quantity == 2
    assert serial_line.fact_quantity == 0  # экземпляры назначаются позже


def test_cannot_create_twice(env):
    db, project, *_ = env
    service.create_from_estimate(db, project)
    with pytest.raises(service.AlreadyExists):
        service.create_from_estimate(db, project)


# --- Вес и объём (ТЗ §19, §20) ------------------------------------------


def test_weight_and_volume_all_packed(env):
    db, project, qty_model, _ = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    c = compute_line(line)
    assert c.packages == 3  # ceil(10/4)
    assert c.equipment_weight == Decimal("20.0")  # 10 × 2.0
    assert c.packaging_weight == Decimal("3.0")  # 3 × 1.0
    assert c.total_weight == Decimal("23.0")
    # всё упаковано → объём оборудования не считается, только упаковка
    assert c.equipment_volume == Decimal("0.000")
    assert c.package_volume == Decimal("0.180")  # 3 × 0.06
    assert c.total_volume == Decimal("0.180")


def test_distribution_moves_to_unpacked(env):
    db, project, qty_model, _ = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    service.set_distribution(db, line, packed_quantity=4)  # 4 упак, 6 без
    c = compute_line(line)
    assert c.packed == 4 and c.unpacked == 6
    assert c.packages == 1  # ceil(4/4)
    assert c.equipment_weight == Decimal("20.0")  # вес всех единиц
    assert c.packaging_weight == Decimal("1.0")
    assert c.equipment_volume == Decimal("0.006")  # 6 × 0.001
    assert c.package_volume == Decimal("0.060")  # 1 × 0.06
    assert c.total_volume == Decimal("0.066")


# --- Серийные экземпляры (ТЗ §17.7, §17.8, §22) -------------------------


def test_serial_add_flow(env):
    db, project, qty_model, serial_model = env
    for bc in ("S1", "S2", "S3"):
        item_service.create_item(db, serial_model, EquipmentItemInput(barcode=bc), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)

    assert service.add_serial_item(db, line, "S1") == service.SerialResult.OK
    assert service.add_serial_item(db, line, "S1") == service.SerialResult.DUPLICATE
    assert service.add_serial_item(db, line, "NOPE") == service.SerialResult.NOT_FOUND
    # другая модель
    assert service.add_serial_item(db, line, "WRONGBC") == service.SerialResult.NOT_FOUND
    # второй — план 2 достигнут
    assert service.add_serial_item(db, line, "S2") == service.SerialResult.OK
    # третий — сверх плана
    assert service.add_serial_item(db, line, "S3") == service.SerialResult.OVER_PLAN
    assert (
        service.add_serial_item(db, line, "S3", allow_over=True) == service.SerialResult.OVER_PLAN
    )
    assert line.fact_quantity == 3


def test_serial_blocked_status(env):
    db, project, qty_model, serial_model = env
    item = item_service.create_item(
        db, serial_model, EquipmentItemInput(barcode="R1"), user_id=None
    )
    item_service.change_status(db, item, ItemStatus.REPAIR, user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    assert service.add_serial_item(db, line, "R1") == service.SerialResult.BLOCKED


def test_serial_wrong_model(env):
    db, project, qty_model, serial_model = env
    # экземпляр посерийной модели №2
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id, name="Другой", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db, other, EquipmentItemInput(barcode="OTHER1"), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    assert service.add_serial_item(db, line, "OTHER1") == service.SerialResult.WRONG_MODEL


# --- Статусы (ТЗ §17.4) -------------------------------------------------


def test_picked_requires_confirmation_when_undercomplete(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    # серийная строка факт 0 < план 2 → недокомплект
    assert service.is_undercomplete(packing)
    with pytest.raises(service.UndercompleteError):
        service.set_status(db, packing, PackingStatus.PICKED)
    service.set_status(
        db,
        packing,
        PackingStatus.PICKED,
        shortage_comment="нет микшера",
        confirm_undercomplete=True,
    )
    assert packing.status == PackingStatus.PICKED
    assert packing.shortage_comment == "нет микшера"


# --- Синхронизация со сметой (ТЗ §17.2) ---------------------------------


def test_sync_with_estimate(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    estimate = est_service.get_estimate(db, project)
    # увеличиваем количество в смете
    qty_line = next(ln for ln in estimate.lines if ln.model_id == qty_model.id)
    from app.estimates.schemas import LineUpdate

    est_service.update_line(
        db, qty_line, LineUpdate(quantity=15, unit_price=Decimal("0"), coefficient=Decimal("1"))
    )
    disc = service.discrepancies(db, project, packing)
    assert any(d.model_id == qty_model.id and d.estimate_quantity == 15 for d in disc)
    service.apply_sync(db, project, packing)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert line.planned_quantity == 15


# --- Интеграция с удалением проекта (ТЗ §13.7) --------------------------


def test_project_delete_blocked_with_packing(env):
    db, project, *_ = env
    service.create_from_estimate(db, project)
    with pytest.raises(proj_service.ValidationError):
        proj_service.delete_project(db, project)
