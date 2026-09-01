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


# --- Обновление сметы по факту packing-листа (обратная синхронизация) ---


def test_estimate_sync_updates_existing_lines(env):
    """Факт packing-листа (меньше/больше плана) переносится в количество строки сметы."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)

    qty_line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    service.update_quantity_line(db, qty_line, fact_quantity=7, packed_quantity=0, comment=None)

    serial_line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="ES1"), user_id=None)
    service.add_serial_item(db, serial_line, "ES1")

    items = service.estimate_discrepancies(db, project, packing)
    assert any(
        it.model_id == qty_model.id and it.estimate_quantity == 10 and it.fact_quantity == 7
        for it in items
    )
    assert any(
        it.model_id == serial_model.id and it.estimate_quantity == 2 and it.fact_quantity == 1
        for it in items
    )

    service.apply_estimate_sync(db, project, packing, {it.key for it in items})
    estimate = est_service.get_estimate(db, project)
    qty_est_line = next(ln for ln in estimate.lines if ln.model_id == qty_model.id)
    serial_est_line = next(ln for ln in estimate.lines if ln.model_id == serial_model.id)
    assert qty_est_line.quantity == 7
    assert serial_est_line.quantity == 1
    assert not service.estimate_discrepancies(db, project, packing)


def test_estimate_sync_applies_only_selected_items(env):
    """Не отмеченные пользователем позиции синхронизация не трогает (экспорт не всегда весь)."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)

    qty_line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    service.update_quantity_line(db, qty_line, fact_quantity=7, packed_quantity=0, comment=None)

    serial_line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="ES2"), user_id=None)
    service.add_serial_item(db, serial_line, "ES2")

    items = service.estimate_discrepancies(db, project, packing)
    qty_item = next(it for it in items if it.model_id == qty_model.id)

    applied = service.apply_estimate_sync(db, project, packing, {qty_item.key})
    assert len(applied) == 1
    assert applied[0].model_id == qty_model.id

    estimate = est_service.get_estimate(db, project)
    qty_est_line = next(ln for ln in estimate.lines if ln.model_id == qty_model.id)
    serial_est_line = next(ln for ln in estimate.lines if ln.model_id == serial_model.id)
    assert qty_est_line.quantity == 7
    assert serial_est_line.quantity == 2  # не отмечена — осталась прежней

    remaining = service.estimate_discrepancies(db, project, packing)
    assert len(remaining) == 1
    assert remaining[0].model_id == serial_model.id


def test_estimate_sync_creates_new_line_for_manual_addition(env):
    """Модель, добавленная в packing вручную (сверх сметы), создаёт новую строку сметы."""
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
    service.add_model(db, packing, other, 3)

    items = service.estimate_discrepancies(db, project, packing)
    new_item = next(it for it in items if it.model_id == other.id)
    assert new_item.is_new
    assert new_item.fact_quantity == 3

    service.apply_estimate_sync(db, project, packing, {it.key for it in items})
    estimate = est_service.get_estimate(db, project)
    est_line = next(ln for ln in estimate.lines if ln.model_id == other.id)
    assert est_line.quantity == 3


def test_estimate_sync_ignores_lines_without_packing_counterpart(env):
    """Строка сметы без соответствующей позиции в packing-листе синхронизацией не трогается."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    estimate = est_service.get_estimate(db, project)
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id,
            name="Не в packing",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=5,
        ),
    )
    # other добавлена в смету уже после создания packing-листа — в packing её нет.
    est_service.add_model(db, estimate, project, other, 4)
    assert not any(ln.model_id == other.id for ln in packing.lines)

    items = service.estimate_discrepancies(db, project, packing)
    assert not any(it.model_id == other.id for it in items)


# --- Интеграция с удалением проекта (ТЗ §13.7) --------------------------


def test_project_delete_blocked_with_packing(env):
    db, project, *_ = env
    service.create_from_estimate(db, project)
    with pytest.raises(proj_service.ValidationError):
        proj_service.delete_project(db, project)


# --- Штрих-коды количественных строк: заготовки и замена сканом (ТЗ §22) ----


def test_backfill_quantity_barcodes(env):
    """backfill_quantity_barcodes довязывает заготовки под уже выставленный факт,
    даже если в момент выставления факта на складе ещё не было штрих-кодов —
    вызывается перед пересборкой документов (PDF packing-листа, carnet)."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert not line.serial_items  # на складе ещё нет штрих-кодов (см. env)

    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="B1"), user_id=None)
    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="B2"), user_id=None)

    attached = service.backfill_quantity_barcodes(db, packing)
    assert attached == 2
    assert {si.barcode for si in line.serial_items} == {"B1", "B2"}
    assert all(not si.confirmed_by_scan for si in line.serial_items)

    # Повторный вызов идемпотентен — не плодит дубликаты по уже занятому факту.
    again = service.backfill_quantity_barcodes(db, packing)
    assert again == 0
    assert len(line.serial_items) == 2


def test_quantity_auto_assigns_on_manual_fact_increase(env):
    """Увеличение «Факт» вручную (без сканирования, поле на основной странице)
    тоже должно подобрать заготовки — не только add_model/create_from_estimate."""
    db, project, qty_model, serial_model = env
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert not line.serial_items  # на момент создания склад без штрих-кодов (см. env)

    # Штрих-коды появились на складе уже после сборки packing-листа.
    for bc in ("M1", "M2"):
        item_service.create_item(db, qty_model, EquipmentItemInput(barcode=bc), user_id=None)

    service.update_quantity_line(db, line, fact_quantity=12, packed_quantity=0, comment=None)
    assert line.quantity == 12
    assert {si.barcode for si in line.serial_items} == {"M1", "M2"}
    assert all(not si.confirmed_by_scan for si in line.serial_items)


def test_quantity_auto_assigns_unconfirmed_items_on_create(env):
    """При наборе количества без сканирования строка получает заготовки на
    свободные экземпляры с штрих-кодом — они не подтверждены сканом."""
    db, project, qty_model, serial_model = env
    for bc in ("Q1", "Q2", "Q3"):
        item_service.create_item(db, qty_model, EquipmentItemInput(barcode=bc), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert line.quantity == 10  # факт не меняется — заготовки лишь фиксируют штрих-код
    assert {si.barcode for si in line.serial_items} == {"Q1", "Q2", "Q3"}
    assert all(not si.confirmed_by_scan for si in line.serial_items)


def test_quantity_add_model_auto_assigns_too(env):
    """Тот же авто-подбор — при ручном добавлении модели через подборщик (add_model)."""
    db, project, qty_model, serial_model = env
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id,
            name="Кабель",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=0,
        ),
    )
    item_service.create_item(db, other, EquipmentItemInput(barcode="C1"), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = service.add_model(db, packing, other, 3)
    assert line.quantity == 3
    assert len(line.serial_items) == 1  # доступен только один экземпляр со штрих-кодом
    assert line.serial_items[0].barcode == "C1"
    assert not line.serial_items[0].confirmed_by_scan


def test_quantity_scan_replaces_unconfirmed_slot(env):
    """Скан штрих-кода заменяет заготовку количественной строки, не наращивая факт."""
    db, project, qty_model, serial_model = env
    for bc in ("Q1", "Q2"):
        item_service.create_item(db, qty_model, EquipmentItemInput(barcode=bc), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert len(line.serial_items) == 2
    before_quantity = line.quantity

    real_item = item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q3"), user_id=None)
    outcome = service.scan(db, packing, "Q3")
    assert outcome.result == service.SerialResult.OK

    db.refresh(line)
    assert line.quantity == before_quantity  # заменили заготовку, не добавили сверху
    assert len(line.serial_items) == 2
    confirmed = [si for si in line.serial_items if si.confirmed_by_scan]
    assert len(confirmed) == 1
    assert confirmed[0].barcode == "Q3"
    assert confirmed[0].item_id == real_item.id
    remaining = [si for si in line.serial_items if not si.confirmed_by_scan]
    assert len(remaining) == 1
    assert remaining[0].barcode in ("Q1", "Q2")


def test_quantity_scan_protects_confirmed_slot_from_second_replace(env):
    """Подтверждённая сканом заготовка не заменяется повторно другим сканом."""
    db, project, qty_model, serial_model = env
    for bc in ("Q1", "Q2"):
        item_service.create_item(db, qty_model, EquipmentItemInput(barcode=bc), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)

    q3 = item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q3"), user_id=None)
    q4 = item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q4"), user_id=None)

    assert service.scan(db, packing, "Q3").result == service.SerialResult.OK
    db.refresh(line)
    assert {si.item_id for si in line.serial_items if si.confirmed_by_scan} == {q3.id}

    assert service.scan(db, packing, "Q4").result == service.SerialResult.OK
    db.refresh(line)
    confirmed_ids = {si.item_id for si in line.serial_items if si.confirmed_by_scan}
    assert confirmed_ids == {q3.id, q4.id}  # Q3 не переписан вторым сканом
    assert not any(not si.confirmed_by_scan for si in line.serial_items)


def test_quantity_scan_over_plan_when_no_unconfirmed_slot_left(env):
    """Когда все заготовки строки уже подтверждены сканом, дальнейший скан — сверх плана."""
    db, project, qty_model, serial_model = env
    for bc in ("Q1", "Q2"):
        item_service.create_item(db, qty_model, EquipmentItemInput(barcode=bc), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    service.scan(db, packing, "Q1")
    service.scan(db, packing, "Q2")
    db.refresh(line)
    assert line.quantity == 10  # план=факт=10 по умолчанию, замены его не трогали

    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q5"), user_id=None)
    blocked = service.scan(db, packing, "Q5")
    assert blocked.result == service.SerialResult.OVER_PLAN
    db.refresh(line)
    assert line.quantity == 10

    allowed = service.scan(db, packing, "Q5", allow_over=True)
    assert allowed.result == service.SerialResult.OVER_PLAN
    db.refresh(line)
    assert line.quantity == 11
    assert len(line.serial_items) == 3


def test_quantity_add_serial_item_replaces_and_checks_model(env):
    """Форма на основной странице (line_serial_add) теперь работает и для количественных строк."""
    db, project, qty_model, serial_model = env
    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q1"), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert len(line.serial_items) == 1
    assert not line.serial_items[0].confirmed_by_scan

    q2 = item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q2"), user_id=None)
    assert service.add_serial_item(db, line, "Q2") == service.SerialResult.OK
    assert len(line.serial_items) == 1
    assert line.serial_items[0].item_id == q2.id
    assert line.serial_items[0].confirmed_by_scan

    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="OTHERQ"), user_id=None)
    assert service.add_serial_item(db, line, "OTHERQ") == service.SerialResult.WRONG_MODEL


def test_carnet_prefers_confirmed_barcodes_falls_back_to_unconfirmed(env):
    """ТЗ по итогам обсуждения: если в строке есть подтверждённые сканом штрих-коды —
    в carnet идут только они; если нет ни одного подтверждённого — все имеющиеся."""
    from app.packing import carnet as carnet_module

    db, project, qty_model, serial_model = env
    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q1"), user_id=None)
    item_service.create_item(db, qty_model, EquipmentItemInput(barcode="Q2"), user_id=None)
    packing = service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.model_id == qty_model.id)
    assert len(line.serial_items) == 2

    rows = carnet_module.build_rows(db, packing)
    qty_row = next(r for r in rows if r.description.startswith("Колонка"))
    assert "Q1" in qty_row.description
    assert "Q2" in qty_row.description  # подтверждённых нет — берём все заготовки

    service.scan(db, packing, "Q1")
    db.refresh(line)
    rows2 = carnet_module.build_rows(db, packing)
    qty_row2 = next(r for r in rows2 if r.description.startswith("Колонка"))
    assert "Q1" in qty_row2.description
    assert "Q2" not in qty_row2.description  # есть подтверждённый — только он
