"""Приёмка оборудования: создание из packing-листа, сканирование, статусы (ТЗ §56)."""

from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.enums import ProjectStatus
from app.projects.schemas import ProjectInput
from app.returns import service
from app.returns.enums import ReturnCondition, ReturnStatus


@pytest.fixture
def env(db_session):
    db = db_session
    cat = cat_service.create_category(db, "Звук")
    qty_model = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("2.0"),
        ),
    )
    serial_model = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id, name="Микшер", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="S1"), user_id=None)
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="S2"), user_id=None)

    project = proj_service.create_project(
        db, ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db, project)
    est_service.add_model(db, estimate, project, qty_model, 10)
    est_service.add_model(db, estimate, project, serial_model, 2)

    packing = packing_service.create_from_estimate(db, project)
    serial_line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    packing_service.add_serial_item(db, serial_line, "S1")
    packing_service.add_serial_item(db, serial_line, "S2")

    return db, project, qty_model, serial_model, packing


# --- Создание из packing-листа (ТЗ §56.1) --------------------------------


def test_create_from_packing_snapshot(env):
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)
    assert ret.number.startswith("RET-")
    assert len(ret.lines) == 2

    qty_line = next(ln for ln in ret.lines if ln.model_id == qty_model.id)
    assert qty_line.expected_quantity == 10  # факт выдачи количественной строки
    assert qty_line.fact_quantity == 0

    serial_line = next(ln for ln in ret.lines if ln.model_id == serial_model.id)
    assert serial_line.expected_quantity == 2
    assert len(serial_line.serial_items) == 2
    assert all(not si.is_returned for si in serial_line.serial_items)


def test_create_without_packing_is_empty(db_session):
    project = proj_service.create_project(
        db_session, ProjectInput(name="Без packing", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    ret = service.create_from_packing(db_session, project)
    assert ret.lines == []


def test_cannot_create_twice(env):
    db, project, *_ = env
    service.create_from_packing(db, project)
    with pytest.raises(service.AlreadyExists):
        service.create_from_packing(db, project)


# --- Сканирование (ТЗ §56.3) ---------------------------------------------


def test_scan_flow(env):
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)

    outcome = service.scan(db, ret, "S1")
    assert outcome.result == service.ScanResult.OK
    line = next(ln for ln in ret.lines if ln.model_id == serial_model.id)
    assert line.fact_quantity == 1

    assert service.scan(db, ret, "S1").result == service.ScanResult.ALREADY
    assert service.scan(db, ret, "NOPE").result == service.ScanResult.NOT_FOUND


def test_scan_wrong_model_not_in_list(env):
    db, project, qty_model, serial_model, packing = env
    other_model = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=qty_model.category_id, name="Прожектор", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db, other_model, EquipmentItemInput(barcode="X1"), user_id=None)
    ret = service.create_from_packing(db, project)
    assert service.scan(db, ret, "X1").result == service.ScanResult.NOT_IN_LIST


def test_scan_same_model_extra_unit_all_pending_returned(env):
    """Если все ожидаемые единицы модели уже приняты, лишний штрихкод — не по листу, не замена."""
    db, project, qty_model, serial_model, packing = env
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="S3"), user_id=None)
    ret = service.create_from_packing(db, project)
    service.scan(db, ret, "S1")
    service.scan(db, ret, "S2")
    assert service.scan(db, ret, "S3").result == service.ScanResult.NOT_IN_LIST


def test_undo_scan(env):
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)
    outcome = service.scan(db, ret, "S1")
    service.undo_scan(db, ret, outcome.serial_item_id)
    line = next(ln for ln in ret.lines if ln.model_id == serial_model.id)
    assert line.fact_quantity == 0


def test_accept_all_marks_all_pending(env):
    """Приёмка пачкой (рэк из нескольких модулей) — без поштучного сканирования (ТЗ §56.3)."""
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.model_id == serial_model.id)

    service.scan(db, ret, "S1")  # одна единица уже принята вручную
    count = service.accept_all(db, line)
    assert count == 1  # приняли только оставшуюся S2
    assert line.fact_quantity == 2
    assert all(si.condition == ReturnCondition.OK for si in line.serial_items)

    # Повторный вызов идемпотентен — принимать уже нечего.
    assert service.accept_all(db, line) == 0
    assert line.fact_quantity == 2


def test_scan_substitute_flow(env):
    """Физически привезли не ту единицу той же модели — подмена на погрузке (ТЗ §56.3)."""
    db, project, qty_model, serial_model, packing = env
    item_service.create_item(db, serial_model, EquipmentItemInput(barcode="S3"), user_id=None)
    ret = service.create_from_packing(db, project)

    outcome = service.scan(db, ret, "S3")
    assert outcome.result == service.ScanResult.SUBSTITUTE_CANDIDATE
    assert outcome.pending_barcode in ("S1", "S2")
    pending_id = outcome.pending_serial_item_id

    confirmed = service.confirm_substitute(db, ret, pending_id, "S3")
    assert confirmed.result == service.ScanResult.OK
    assert confirmed.barcode == "S3"

    line = next(ln for ln in ret.lines if ln.model_id == serial_model.id)
    barcodes = {si.barcode for si in line.serial_items}
    assert barcodes == {"S3", "S2"} or barcodes == {"S1", "S3"}
    assert line.fact_quantity == 1
    # S1 (или S2, смотря что заменили) больше не в листе как «не возвращено» —
    # он просто исчез из ожидания, потому что физически не уезжал.
    assert outcome.pending_barcode not in barcodes

    # Замена не должна ложно списать в дефект единицу, которая никуда не уезжала.
    service.scan(db, ret, "S2" if outcome.pending_barcode == "S1" else "S1")
    service.update_quantity_line(db, next(ln for ln in ret.lines if ln.model_id == qty_model.id), 10, None)
    service.set_status(db, ret, ReturnStatus.RECEIVED, project_number=project.number)
    substituted_out = next(
        it for it in eq_service.get_model(db, serial_model.id).items if it.barcode == outcome.pending_barcode
    )
    assert substituted_out.status == ItemStatus.ACTIVE


# --- Статусы и недостача (ТЗ §56.2, §56.5) -------------------------------


def test_received_requires_confirmation_when_incomplete(env):
    db, project, *_ = env
    ret = service.create_from_packing(db, project)
    assert service.is_incomplete(ret)
    with pytest.raises(service.IncompleteError):
        service.set_status(db, ret, ReturnStatus.RECEIVED, project_number=project.number)
    service.set_status(
        db,
        ret,
        ReturnStatus.RECEIVED,
        project_number=project.number,
        shortage_comment="не всё привезли",
        confirm_incomplete=True,
    )
    assert ret.status == ReturnStatus.RECEIVED
    assert ret.shortage_comment == "не всё привезли"


# --- Отражение в статусах экземпляров (ТЗ §56.4) -------------------------


def test_apply_item_statuses_marks_missing_as_defect(env):
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)
    service.scan(db, ret, "S1")  # S2 остаётся не возвращённым
    service.update_quantity_line(db, next(ln for ln in ret.lines if ln.model_id == qty_model.id), 10, None)
    service.set_status(
        db,
        ret,
        ReturnStatus.RECEIVED,
        project_number=project.number,
        shortage_comment="потеряли",
        confirm_incomplete=True,
    )

    s1 = next(it for it in eq_service.get_model(db, serial_model.id).items if it.barcode == "S1")
    s2 = next(it for it in eq_service.get_model(db, serial_model.id).items if it.barcode == "S2")
    assert s1.status == ItemStatus.ACTIVE  # вернулось без замечаний
    assert s2.status == ItemStatus.DEFECT  # не возвращено
    assert "не возвращено по проекту" in s2.status_history[0].comment


def test_apply_item_statuses_respects_marked_condition(env):
    db, project, qty_model, serial_model, packing = env
    ret = service.create_from_packing(db, project)
    outcome = service.scan(db, ret, "S1")
    service.set_condition(db, service.get_serial_item(db, ret, outcome.serial_item_id), ReturnCondition.REPAIR, "треснул корпус")
    service.scan(db, ret, "S2")
    service.update_quantity_line(db, next(ln for ln in ret.lines if ln.model_id == qty_model.id), 10, None)
    service.set_status(db, ret, ReturnStatus.RECEIVED, project_number=project.number)

    s1 = next(it for it in eq_service.get_model(db, serial_model.id).items if it.barcode == "S1")
    assert s1.status == ItemStatus.REPAIR
    assert "треснул корпус" in s1.status_history[0].comment


# --- Гейт завершения проекта (ТЗ §56.6) ----------------------------------


def test_project_completion_blocked_until_received(env):
    db, project, qty_model, serial_model, packing = env
    proj_service.book_project(db, project)
    proj_service.set_status(db, project, ProjectStatus.SHIPPED)
    service.create_from_packing(db, project)

    with pytest.raises(proj_service.ValidationError):
        proj_service.set_status(db, project, ProjectStatus.COMPLETED)

    ret = service.get_return(db, project)
    service.scan(db, ret, "S1")
    service.scan(db, ret, "S2")
    service.update_quantity_line(db, next(ln for ln in ret.lines if ln.model_id == qty_model.id), 10, None)
    service.set_status(db, ret, ReturnStatus.RECEIVED, project_number=project.number)

    proj_service.set_status(db, project, ProjectStatus.COMPLETED)
    assert project.status == ProjectStatus.COMPLETED


def test_project_completion_not_blocked_without_return_list(env):
    """Гейт не срабатывает, если лист приёмки вообще не заводили."""
    db, project, *_ = env
    proj_service.book_project(db, project)
    proj_service.set_status(db, project, ProjectStatus.SHIPPED)
    proj_service.set_status(db, project, ProjectStatus.COMPLETED)
    assert project.status == ProjectStatus.COMPLETED
