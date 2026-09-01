"""Возврат комплекта аксессуаров: снимок содержимого, чек-лист, недостача (ТЗ §56)."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.accessory_kits import service as ak_service
from app.accessory_kits.schemas import AccessoryKitInput, CustomAccessoryKitLine
from app.auth import service as auth_service
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput
from app.returns import service as return_service


@pytest.fixture
def auth_client(client, db_session):
    auth_service.create_user(db_session, "admin", "pass123")
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    return client


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


@pytest.fixture
def env(db_session):
    db = db_session
    cat = cat_service.create_category(db, "Кабели")
    model = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id, name="XLR 10м", accounting_type=AccountingType.QUANTITY,
            total_quantity=50, weight_kg=Decimal("0.5"),
        ),
    )
    project = proj_service.create_project(
        db, ProjectInput(name="Тур", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db, project)
    kit = ak_service.create_kit(
        db, project, AccessoryKitInput(name="Кабелярка FOH", barcode="CASE-001")
    )
    ak_service.add_model_line(db, project, kit, model, 4)
    ak_service.add_custom_line(db, project, kit, CustomAccessoryKitLine(name="Скотч", quantity=2))
    est_service.add_accessory_kit_line(db, estimate, project, kit)
    packing = packing_service.create_from_estimate(db, project)
    return db, project, model, kit, packing


def test_create_from_packing_snapshots_content(env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)
    assert line.is_accessory_kit
    assert line.expected_quantity == 1  # кейс — одна позиция
    assert len(line.accessory_kit_lines) == 2
    names = {cl.name: cl.expected_quantity for cl in line.accessory_kit_lines}
    assert names == {"XLR 10м": 4, "Скотч": 2}


def test_content_snapshot_survives_kit_content_changes(env):
    """Правки состава кабелярки после оформления возврата не должны менять уже созданный лист."""
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)

    ak_service.add_model_line(db, project, kit, model, 10)  # правим кабелярку после возврата
    db.refresh(line)
    assert {cl.name: cl.expected_quantity for cl in line.accessory_kit_lines} == {
        "XLR 10м": 4, "Скотч": 2
    }


def test_update_accessory_content_line_and_missing_quantity(env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)
    cable_line = next(cl for cl in line.accessory_kit_lines if cl.name == "XLR 10м")

    return_service.update_accessory_content_line(db, cable_line, 3, "потерян один")
    assert cable_line.returned_quantity == 3
    assert cable_line.missing_quantity == 1
    assert cable_line.comment == "потерян один"


def test_incomplete_content_blocks_received_status_even_if_case_returned(env):
    """Кейс вернулся целиком, но внутри недостача — это тоже недостача проекта (§56.5)."""
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)
    return_service.update_quantity_line(db, line, 1, None)  # кейс целиком вернулся

    cable_line = next(cl for cl in line.accessory_kit_lines if cl.name == "XLR 10м")
    return_service.update_accessory_content_line(db, cable_line, 2, None)  # не хватает 2 кабелей

    assert return_service.is_incomplete(ret)
    with pytest.raises(return_service.IncompleteError):
        from app.returns.enums import ReturnStatus

        return_service.set_status(db, ret, ReturnStatus.RECEIVED, project_number=project.number)

    # Все позиции досчитаны — недостачи больше нет.
    return_service.update_accessory_content_line(db, cable_line, 4, None)
    scotch_line = next(cl for cl in line.accessory_kit_lines if cl.name == "Скотч")
    return_service.update_accessory_content_line(db, scotch_line, 2, None)
    assert not return_service.is_incomplete(ret)


def test_returns_page_renders_checklist_and_updates_via_web(auth_client, db_session, env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)
    cable_line = next(cl for cl in line.accessory_kit_lines if cl.name == "XLR 10м")

    base = f"/projects/{project.id}/returns"
    page = auth_client.get(base).text
    assert "Кабелярка FOH" in page
    assert "комплект аксессуаров" in page
    assert "XLR 10м" in page and "Скотч" in page

    token = _csrf(auth_client, base)
    resp = auth_client.post(
        f"{base}/lines/accessory/{cable_line.id}",
        data={"returned_quantity": "3", "comment": "потерян один", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(cable_line)
    assert cable_line.returned_quantity == 3
    assert "недостача" in auth_client.get(base).text


def test_scan_case_barcode_marks_kit_returned(env):
    """Сканирование штрих-кода кейса кабелярки отмечает возврат кейса целиком (ТЗ §56.3)."""
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)
    assert line.returned_quantity == 0

    outcome = return_service.scan(db, ret, "CASE-001")
    assert outcome.ok
    assert outcome.is_accessory_kit
    assert outcome.line_id == line.id
    db.refresh(line)
    assert line.returned_quantity == line.expected_quantity == 1

    # Повторный скан — уже принято, не задваивается.
    again = return_service.scan(db, ret, "CASE-001")
    assert not again.ok
    assert again.result == return_service.ScanResult.ALREADY


def test_scan_case_barcode_unknown_kit_not_in_list(env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)

    other_kit = ak_service.create_kit(
        db, project, AccessoryKitInput(name="Другая кабелярка", barcode="CASE-999")
    )
    outcome = return_service.scan(db, ret, "CASE-999")
    assert not outcome.ok
    assert outcome.result == return_service.ScanResult.NOT_IN_LIST
    assert outcome.model_name == other_kit.name


def test_undo_kit_scan_reverts_return(env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)

    return_service.scan(db, ret, "CASE-001")
    db.refresh(line)
    assert line.returned_quantity == 1

    return_service.undo_kit_scan(db, ret, line.id)
    db.refresh(line)
    assert line.returned_quantity == 0


def test_web_scan_and_undo_case_barcode(auth_client, db_session, env):
    db, project, model, kit, packing = env
    ret = return_service.create_from_packing(db, project)
    line = next(ln for ln in ret.lines if ln.accessory_kit_id == kit.id)

    scan_base = f"/projects/{project.id}/returns"
    token = _csrf(auth_client, f"{scan_base}/scan")
    resp = auth_client.post(
        f"{scan_base}/scan", data={"barcode": "CASE-001", "csrf_token": token}
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["is_accessory_kit"] is True
    db.refresh(line)
    assert line.returned_quantity == 1

    undo_token = _csrf(auth_client, scan_base)
    resp = auth_client.post(f"{scan_base}/scan/undo", data={"csrf_token": undo_token})
    assert resp.json()["ok"] is True
    db.refresh(line)
    assert line.returned_quantity == 0
