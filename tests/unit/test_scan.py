"""Сканирование штрих-кодов в packing-лист (ТЗ §22)."""

from datetime import date

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.packing import service
from app.packing.service import SerialResult
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput


@pytest.fixture
def env(db_session):
    cat = cat_service.create_category(db_session, "Свет")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Прожектор", accounting_type=AccountingType.SERIAL
        ),
    )
    for bc in ("A1", "A2", "A3"):
        item_service.create_item(db_session, model, EquipmentItemInput(barcode=bc), user_id=None)
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, model, 2)  # план 2
    packing = service.create_from_estimate(db_session, project)
    return db_session, packing, model


def test_scan_ok_routes_by_model(env):
    db, packing, model = env
    out = service.scan(db, packing, "A1")
    assert out.result == SerialResult.OK
    assert out.fact == 1
    line = next(ln for ln in packing.lines if ln.model_id == model.id)
    assert line.fact_quantity == 1


def test_scan_duplicate(env):
    db, packing, model = env
    service.scan(db, packing, "A1")
    out = service.scan(db, packing, "A1")
    assert out.result == SerialResult.DUPLICATE


def test_scan_not_found(env):
    db, packing, model = env
    assert service.scan(db, packing, "ZZZ").result == SerialResult.NOT_FOUND


def test_scan_blocked(env):
    db, packing, model = env
    item = item_service.find_by_barcode(db, "A1")
    item_service.change_status(db, item, ItemStatus.REPAIR, user_id=None)
    assert service.scan(db, packing, "A1").result == SerialResult.BLOCKED


def test_scan_wrong_model_not_in_packing(env):
    db, packing, model = env
    other = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=model.category_id, name="Дым", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db, other, EquipmentItemInput(barcode="B1"), user_id=None)
    assert service.scan(db, packing, "B1").result == SerialResult.WRONG_MODEL


def test_scan_over_plan(env):
    db, packing, model = env
    service.scan(db, packing, "A1")
    service.scan(db, packing, "A2")  # план 2 достигнут
    out = service.scan(db, packing, "A3")
    assert out.result == SerialResult.OVER_PLAN
    assert out.serial_item_id is None  # не добавлен без подтверждения
    out2 = service.scan(db, packing, "A3", allow_over=True)
    assert out2.result == SerialResult.OVER_PLAN
    assert out2.serial_item_id is not None
    line = next(ln for ln in packing.lines if ln.model_id == model.id)
    assert line.fact_quantity == 3
