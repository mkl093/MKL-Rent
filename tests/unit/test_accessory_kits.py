"""Комплект аксессуаров: содержимое, вес, резерв склада, смета, packing, шаблоны."""

from datetime import date
from decimal import Decimal

import pytest

from app.accessory_kits import service
from app.accessory_kits.schemas import (
    AccessoryKitInput,
    AccessoryKitTemplateInput,
    CustomAccessoryKitLine,
    TemplateCustomLine,
)
from app.estimates import service as est_service
from app.inventory.enums import AccountingType, KitWeightMode
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.packing import service as packing_service
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


@pytest.fixture
def env(db_session):
    cat = cat_service.create_category(db_session, "Кабели")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="XLR 10м",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("0.5"),
        ),
    )
    project = create_project(
        db_session,
        ProjectInput(name="Концерт", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    return db_session, project, model


def test_create_kit_and_add_stock_line_sums_weight(env):
    db, project, model = env
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка FOH"))
    service.add_model_line(db, project, kit, model, 4)
    db.refresh(kit)
    assert service.content_weight(kit) == Decimal("2.0")  # 4 × 0.5
    assert service.total_weight(kit) == Decimal("2.0")  # режим CONTENT по умолчанию


def test_add_model_line_merges_same_model(env):
    db, project, model = env
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    service.add_model_line(db, project, kit, model, 2)
    service.add_model_line(db, project, kit, model, 3)
    db.refresh(kit)
    assert len(kit.lines) == 1
    assert kit.lines[0].quantity == 5


def test_weight_modes_packaging_and_total(env):
    db, project, model = env
    kit = service.create_kit(
        db, project,
        AccessoryKitInput(name="Кейс", weight_mode=KitWeightMode.PACKAGING, weight_value=Decimal("3")),
    )
    service.add_model_line(db, project, kit, model, 4)  # содержимое 2.0 кг
    db.refresh(kit)
    assert service.total_weight(kit) == Decimal("5.0")  # 2.0 + 3 (кейс)

    service.update_kit(
        db, project, kit,
        AccessoryKitInput(name="Кейс", weight_mode=KitWeightMode.TOTAL, weight_value=Decimal("9")),
    )
    assert service.total_weight(kit) == Decimal("9")  # фиксированный, содержимое не учитывается


def test_custom_line_and_update(env):
    db, project, model = env
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    line = service.add_custom_line(
        db, project, kit, CustomAccessoryKitLine(name="Скотч", quantity=2, unit_weight_kg=Decimal("0.3"))
    )
    assert service.content_weight(kit) == Decimal("0.6")
    service.update_line(db, project, kit, line, quantity=5, comment="запас", name="Скотч чёрный")
    assert line.quantity == 5
    assert line.name == "Скотч чёрный"


def test_duplicate_barcode_rejected(env):
    db, project, model = env
    service.create_kit(db, project, AccessoryKitInput(name="A", barcode="AK-001"))
    with pytest.raises(service.DuplicateBarcode):
        service.create_kit(db, project, AccessoryKitInput(name="B", barcode="AK-001"))


def test_stock_content_feeds_project_reservation(env):
    db, project, model = env
    estimate = est_service.get_or_create_estimate(db, project)
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    service.add_model_line(db, project, kit, model, 6)

    res = db.query(ProjectReservation).filter_by(project_id=project.id, model_id=model.id).one()
    assert res.quantity == 6  # ничего из сметы ещё нет

    est_service.add_model(db, estimate, project, model, 4)
    res = db.query(ProjectReservation).filter_by(project_id=project.id, model_id=model.id).one()
    assert res.quantity == 10  # смета (4) + содержимое кабелярки (6)

    service.delete_line(db, project, kit, kit.lines[0])
    res = db.query(ProjectReservation).filter_by(project_id=project.id, model_id=model.id).one()
    assert res.quantity == 4  # снова только смета


def test_delete_kit_blocked_when_in_estimate(env):
    db, project, model = env
    estimate = est_service.get_or_create_estimate(db, project)
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    est_service.add_accessory_kit_line(db, estimate, project, kit)
    with pytest.raises(service.InUse):
        service.delete_kit(db, project, kit)


def test_add_accessory_kit_line_is_single_and_hides_content(env):
    db, project, model = env
    estimate = est_service.get_or_create_estimate(db, project)
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    service.add_model_line(db, project, kit, model, 3)

    line = est_service.add_accessory_kit_line(db, estimate, project, kit)
    assert line.accessory_kit_id == kit.id
    assert line.quantity == 1
    assert line.unit_price == Decimal("0")
    # повторное добавление не задваивает строку
    assert est_service.add_accessory_kit_line(db, estimate, project, kit) is None
    assert sum(1 for ln in estimate.lines if ln.accessory_kit_id == kit.id) == 1


def test_packing_pulls_accessory_kit_with_live_weight(env):
    db, project, model = env
    estimate = est_service.get_or_create_estimate(db, project)
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    service.add_model_line(db, project, kit, model, 4)  # 2.0 кг
    est_service.add_accessory_kit_line(db, estimate, project, kit)

    packing = packing_service.create_from_estimate(db, project)
    line = next(ln for ln in packing.lines if ln.accessory_kit_id == kit.id)
    assert line.unit_weight_kg == Decimal("2.0")
    assert line.category_name == packing_service.ACCESSORY_KIT_GROUP_NAME

    # Содержимое достраивается уже после создания packing-строки — вес должен
    # проталкиваться в снимок строки (ТЗ: кабелярка собирается в процессе packing).
    service.add_model_line(db, project, kit, model, 4)  # ещё 2.0 кг → итого 4.0
    db.refresh(line)
    assert line.unit_weight_kg == Decimal("4.0")


def test_packing_category_breakdown_groups_accessory_kit(env):
    db, project, model = env
    estimate = est_service.get_or_create_estimate(db, project)
    kit = service.create_kit(db, project, AccessoryKitInput(name="Кабелярка"))
    service.add_model_line(db, project, kit, model, 2)  # 1.0 кг
    est_service.add_accessory_kit_line(db, estimate, project, kit)
    packing = packing_service.create_from_estimate(db, project)

    breakdown = packing_service.category_breakdown(db, packing)
    group = next(b for b in breakdown if b.category_name == packing_service.ACCESSORY_KIT_GROUP_NAME)
    assert group.total_weight == Decimal("1.0")


def test_create_from_template_copies_lines(db_session):
    cat = cat_service.create_category(db_session, "Кабели")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="XLR 10м", accounting_type=AccountingType.QUANTITY,
            total_quantity=50, weight_kg=Decimal("0.5"),
        ),
    )
    project = create_project(
        db_session, ProjectInput(name="Тур", start_date=date(2026, 8, 1), end_date=date(2026, 8, 5))
    )
    template = service.create_template(db_session, AccessoryKitTemplateInput(name="FOH"))
    service.add_template_model_line(db_session, template, model, 4)
    service.add_template_custom_line(
        db_session, template, TemplateCustomLine(name="Скотч", quantity=1)
    )

    kit = service.create_from_template(db_session, project, template, name="Кабелярка FOH #1")
    assert kit.name == "Кабелярка FOH #1"
    assert len(kit.lines) == 2
    assert kit.barcode is None  # физические атрибуты кейса из шаблона не переносятся

    res = db_session.query(ProjectReservation).filter_by(project_id=project.id, model_id=model.id).one()
    assert res.quantity == 4  # резерв пересчитан после клонирования из шаблона
