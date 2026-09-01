"""Кабелярка в packing-листе: добавление напрямую и сканирование кейса (ТЗ §22)."""

import re
from datetime import date

import pytest

from app.accessory_kits import service as ak_service
from app.accessory_kits.schemas import AccessoryKitInput
from app.auth import service as auth_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput


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
    project = proj_service.create_project(
        db, ProjectInput(name="Тур", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    kit = ak_service.create_kit(db, project, AccessoryKitInput(name="Кабелярка FOH", barcode="CASE-001"))
    packing = packing_service.create_from_estimate(db, project)
    return db, project, kit, packing


def test_add_accessory_kit_to_packing_directly(env):
    db, project, kit, packing = env
    line = packing_service.add_accessory_kit(db, packing, kit)
    assert line is not None
    assert line.accessory_kit_id == kit.id
    assert line.is_manual
    assert line.planned_quantity == 1
    assert line.fact_quantity == 1

    # Повторное добавление того же комплекта не задваивает строку.
    assert packing_service.add_accessory_kit(db, packing, kit) is None


def test_scan_confirms_accessory_kit_case_without_changing_quantity(env):
    db, project, kit, packing = env
    packing_service.add_accessory_kit(db, packing, kit)

    outcome = packing_service.scan(db, packing, "CASE-001")
    assert outcome.ok
    assert outcome.result == packing_service.SerialResult.ACCESSORY_KIT_CONFIRMED
    assert outcome.fact == 1
    assert outcome.planned == 1

    line = packing_service.get_line(db, packing, outcome.line_id)
    assert line.fact_quantity == 1  # без изменений — только подтверждение скана


def test_scan_accessory_kit_not_in_packing(env):
    db, project, kit, packing = env
    # Кабелярка ещё не добавлена в packing-лист.
    outcome = packing_service.scan(db, packing, "CASE-001")
    assert not outcome.ok
    assert outcome.result == packing_service.SerialResult.ACCESSORY_KIT_NOT_IN_LIST


def test_scan_unknown_barcode_not_found(env):
    db, project, kit, packing = env
    outcome = packing_service.scan(db, packing, "does-not-exist")
    assert not outcome.ok
    assert outcome.result == packing_service.SerialResult.NOT_FOUND


def test_web_add_to_packing_button_and_scan(auth_client, db_session, env):
    db, project, kit, packing = env
    base = f"/projects/{project.id}/accessory-kits/{kit.id}"

    page = auth_client.get(base).text
    assert "Добавить в packing-лист" in page

    token = _csrf(auth_client, base)
    resp = auth_client.post(
        f"{base}/add-to-packing", data={"csrf_token": token}, follow_redirects=False
    )
    assert resp.status_code == 303
    db.refresh(packing)
    assert any(ln.accessory_kit_id == kit.id for ln in packing.lines)

    page = auth_client.get(base).text
    assert "В packing-листе" in page

    scan_base = f"/projects/{project.id}/packing"
    scan_token = _csrf(auth_client, f"{scan_base}/scan")
    resp = auth_client.post(
        f"{scan_base}/scan",
        data={"barcode": "CASE-001", "csrf_token": scan_token},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "accessory_kit_confirmed"
