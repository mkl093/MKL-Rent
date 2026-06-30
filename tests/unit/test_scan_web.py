"""Веб-эндпоинты сканирования (ТЗ §22)."""

import re
from datetime import date

import pytest

from app.auth import service as auth_service
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.packing import service as pack_service
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


@pytest.fixture
def packing(db_session):
    cat = cat_service.create_category(db_session, "Свет")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Прожектор", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db_session, model, EquipmentItemInput(barcode="A1"), user_id=None)
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, model, 2)
    pack_service.create_from_estimate(db_session, project)
    return project


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_scan_page_and_submit(auth_client, packing):
    base = f"/projects/{packing.id}/packing"
    page = auth_client.get(f"{base}/scan")
    assert page.status_code == 200
    assert "Сканирование" in page.text

    token = _csrf(auth_client, f"{base}/scan")
    r = auth_client.post(
        f"{base}/scan", data={"barcode": "A1", "allow_over": "0", "csrf_token": token}
    )
    data = r.json()
    assert data["ok"] is True
    assert data["total_fact"] == 1
    assert data["model"] == "Прожектор"

    # Отмена последнего скана
    token = _csrf(auth_client, f"{base}/scan")
    u = auth_client.post(f"{base}/scan/undo", data={"csrf_token": token})
    assert u.json()["ok"] is True
    assert u.json()["total_fact"] == 0


def test_scan_not_found_json(auth_client, packing):
    base = f"/projects/{packing.id}/packing"
    token = _csrf(auth_client, f"{base}/scan")
    r = auth_client.post(f"{base}/scan", data={"barcode": "NOPE", "csrf_token": token})
    data = r.json()
    assert data["ok"] is False
    assert data["result"] == "not_found"
