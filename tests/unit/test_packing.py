"""Packing-лист: расчёты, создание, упаковка, серийные, статусы (ТЗ §17–§20)."""

from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, ItemStatus, PackingType
from app.inventory.schemas import (
    AccessoryQty,
    EquipmentItemInput,
    EquipmentModelCreate,
    KitInput,
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


def test_accessory_totals_live(env):
    db, project, qty_model, serial_model = env
    from app.inventory.models import EquipmentModelAccessory
    from app.inventory.services import accessories as acc_service

    power = acc_service.create_category(db, "Питание")
    cable = acc_service.create_accessory(db, power, "PowerCon")
    rig = acc_service.create_category(db, "Такелаж")
    clamp = acc_service.create_accessory(db, rig, "Клемп")
    # qty_model: факт = план = 10 (см. фикстуру env)
    qty_model.accessories.append(EquipmentModelAccessory(accessory_id=cable.id, quantity=2))
    qty_model.accessories.append(EquipmentModelAccessory(accessory_id=clamp.id, quantity=1))
    db.commit()

    packing = service.create_from_estimate(db, project)
    groups = service.accessory_totals(db, packing)
    # Группировка по категориям + суммирование факт × кол-во в комплектации.
    flat = {name: qty for g in groups for name, qty in g.items}
    assert flat == {"PowerCon": 20, "Клемп": 10}
    cats = [g.category_name for g in groups]
    assert cats == ["Питание", "Такелаж"]  # категории по алфавиту
    assert next(g for g in groups if g.category_name == "Питание").total == 20


def test_accessory_totals_includes_kit_contents(env):
    db, project, _, serial_model = env
    from app.inventory.services import accessories as acc_service
    from app.inventory.services import kits as kit_service

    power = acc_service.create_category(db, "Питание")
    cable = acc_service.create_accessory(db, power, "PowerCon")
    lamp = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=serial_model.category_id,
            name="Прожектор",
            accounting_type=AccountingType.SERIAL,
            accessories=[AccessoryQty(accessory_id=cable.id, quantity=2)],
        ),
    )
    i1 = item_service.create_item(db, lamp, EquipmentItemInput(barcode="L1"), user_id=None)
    i2 = item_service.create_item(db, lamp, EquipmentItemInput(barcode="L2"), user_id=None)
    kit = kit_service.create_kit(db, KitInput(name="Кейс"))
    kit_service.add_items(db, kit, [i1.id, i2.id])

    packing = service.create_from_estimate(db, project)
    service.add_kit(db, packing, kit)

    groups = service.accessory_totals(db, packing)
    flat = {name: qty for g in groups for name, qty in g.items}
    # 2 единицы в комплекте × 2 шт аксессуара на модель = 4
    assert flat.get("PowerCon") == 4


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


# --- Добавление оборудования со склада вручную --------------------------


def test_add_model_new_line(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id,
            name="Кабель",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=5,
            weight_kg=Decimal("0.5"),
        ),
    )
    line = service.add_model(db, packing, other, 3)
    assert line.model_id == other.id
    assert line.planned_quantity == 3
    assert line.quantity == 3  # факт = план для количественных
    assert len([ln for ln in packing.lines if ln.model_id == other.id]) == 1


def test_add_model_merges_existing(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    before = line.planned_quantity  # 10
    same = service.add_model(db, packing, qty_model, 4)
    assert same.id == line.id
    assert same.planned_quantity == before + 4
    assert same.quantity == before + 4  # факт тоже увеличен
    # строка не задвоилась
    assert len([ln for ln in packing.lines if ln.model_id == qty_model.id]) == 1


def test_add_serial_model_new_line(env):
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id, name="Радио", accounting_type=AccountingType.SERIAL
        ),
    )
    line = service.add_model(db, packing, other, 2)
    assert line.is_serial
    assert line.planned_quantity == 2
    assert line.fact_quantity == 0  # серийные назначаются экземплярами


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


def test_sync_deletes_line_removed_from_estimate(env):
    """Удалённая из сметы модель без факта пропадает из packing-листа при синхронизации."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    # Обнуляем факт — иначе автоматически проставленный «факт = план» (ТЗ §18)
    # потребует подтверждения удаления (см. test_sync_requires_confirmation_when_fact_collected).
    service.update_quantity_line(db, line, fact_quantity=0, packed_quantity=0, comment=None)

    estimate = est_service.get_estimate(db, project)
    qty_line = next(ln for ln in estimate.lines if ln.model_id == qty_model.id)
    est_service.delete_line(db, qty_line)
    db.expire_all()  # как в роутере: коллекция estimate.lines закэширована в сессии

    disc = service.discrepancies(db, project, packing)
    assert any(d.model_id == qty_model.id and d.planned_quantity == 10 for d in disc)

    service.apply_sync(db, project, packing)
    db.refresh(packing)
    assert not any(ln.model_id == qty_model.id for ln in packing.lines)


def test_sync_requires_confirmation_when_fact_collected(env):
    """Если по удаляемой позиции уже есть факт — синхронизация без подтверждения не удаляет."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    service.update_quantity_line(db, line, fact_quantity=5, packed_quantity=0, comment=None)

    estimate = est_service.get_estimate(db, project)
    qty_line = next(ln for ln in estimate.lines if ln.model_id == qty_model.id)
    est_service.delete_line(db, qty_line)
    db.expire_all()

    with pytest.raises(service.SyncConfirmRequired):
        service.apply_sync(db, project, packing)
    db.refresh(packing)
    assert any(ln.model_id == qty_model.id for ln in packing.lines)

    service.apply_sync(db, project, packing, confirm_delete=True)
    db.refresh(packing)
    assert not any(ln.model_id == qty_model.id for ln in packing.lines)


def test_sync_does_not_touch_manual_line(env):
    """Модель, добавленная в packing вручную и отсутствующая в смете, синхронизацию переживает."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id,
            name="Ручная модель",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=5,
        ),
    )
    service.add_model(db, packing, other, 2)
    line = next(ln for ln in packing.lines if ln.model_id == other.id)
    assert line.is_manual

    service.apply_sync(db, project, packing)
    db.refresh(packing)
    assert any(ln.model_id == other.id for ln in packing.lines)


def test_custom_estimate_line_synced_to_packing(env):
    """Произвольная позиция сметы переносится в packing-лист и обновляется/удаляется синком."""
    db, project, qty_model, serial_model = env
    from app.estimates.schemas import CustomLineInput

    estimate = est_service.get_estimate(db, project)
    est_service.add_custom_line(
        db, estimate, project, CustomLineInput(name="Суб-аренда пульта", quantity=1)
    )
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.name == "Суб-аренда пульта")
    assert line.is_custom
    assert line.planned_quantity == 1

    est_line = next(ln for ln in estimate.lines if ln.name == "Суб-аренда пульта")
    from app.estimates.schemas import LineUpdate

    est_service.update_line(
        db, est_line, LineUpdate(quantity=3, unit_price=Decimal("0"), coefficient=Decimal("1"))
    )
    disc = service.discrepancies(db, project, packing)
    assert any(d.is_custom and d.estimate_quantity == 3 for d in disc)
    service.apply_sync(db, project, packing)
    db.refresh(packing)
    line = next(ln for ln in packing.lines if ln.name == "Суб-аренда пульта")
    assert line.planned_quantity == 3

    est_service.delete_line(db, est_line)
    db.expire_all()
    # Факт произвольной позиции по умолчанию = план (как и у складских), поэтому удаление
    # требует подтверждения — здесь проверяется сам перенос/удаление, а не запрос подтверждения.
    service.apply_sync(db, project, packing, confirm_delete=True)
    db.refresh(packing)
    assert not any(ln.name == "Суб-аренда пульта" for ln in packing.lines)


def test_custom_estimate_line_skip_packing_checkbox(env):
    """Чекбокс «Не добавлять в паккинг лист» исключает произвольную строку из переноса."""
    db, project, qty_model, serial_model = env
    from app.estimates.schemas import CustomLineInput

    estimate = est_service.get_estimate(db, project)
    est_service.add_custom_line(
        db,
        estimate,
        project,
        CustomLineInput(name="Доставка", quantity=1, add_to_packing=False),
    )
    packing = service.create_from_estimate(db, project)
    assert not any(ln.name == "Доставка" for ln in packing.lines)


# --- Интеграция с удалением проекта (ТЗ §13.7) --------------------------


def test_project_delete_blocked_with_packing(env):
    db, project, *_ = env
    service.create_from_estimate(db, project)
    with pytest.raises(proj_service.ValidationError):
        proj_service.delete_project(db, project)
