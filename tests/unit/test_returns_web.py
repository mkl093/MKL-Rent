"""Веб-маршруты приёмки оборудования (ТЗ §56)."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.enums import ProjectStatus
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
def project_with_packing(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    qty_model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("2.0"),
        ),
    )
    serial_model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id, name="Микшер", accounting_type=AccountingType.SERIAL
        ),
    )
    item_service.create_item(db_session, serial_model, EquipmentItemInput(barcode="S1"), user_id=None)
    item_service.create_item(db_session, serial_model, EquipmentItemInput(barcode="S2"), user_id=None)

    project = proj_service.create_project(
        db_session, ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, qty_model, 8)
    est_service.add_model(db_session, estimate, project, serial_model, 2)
    packing = packing_service.create_from_estimate(db_session, project)
    serial_line = next(ln for ln in packing.lines if ln.model_id == serial_model.id)
    packing_service.add_serial_item(db_session, serial_line, "S1")
    packing_service.add_serial_item(db_session, serial_line, "S2")

    proj_service.book_project(db_session, project)
    proj_service.set_status(db_session, project, ProjectStatus.SHIPPED)
    return project


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_returns_create_and_view(auth_client, project_with_packing):
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"

    page = auth_client.get(base)
    assert page.status_code == 200
    assert "Оформить возврат" in page.text

    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)
    page = auth_client.get(base).text
    assert "RET-" in page
    assert "Колонка" in page
    assert "Микшер" in page
    assert "ожидается 8" in page


def test_returns_scan_web(auth_client, project_with_packing):
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    scan_page = auth_client.get(f"{base}/scan")
    assert scan_page.status_code == 200

    token = _csrf(auth_client, f"{base}/scan")
    r = auth_client.post(f"{base}/scan", data={"barcode": "S1", "csrf_token": token})
    assert r.json()["ok"] is True
    assert r.json()["fact"] == 1

    page = auth_client.get(base).text
    assert "не возвращено" in page  # S2 ещё не отсканирован


def test_returns_accept_all_web(auth_client, project_with_packing):
    """Приёмка пачкой со страницы обзора — без сканирования каждой единицы (ТЗ §56.3)."""
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    overview = auth_client.get(base).text
    line_id = re.search(r"/lines/(\d+)/accept_all", overview).group(1)

    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/lines/{line_id}/accept_all", data={"csrf_token": token}, follow_redirects=False
    )
    page = auth_client.get(base).text
    assert "не возвращено" not in page
    assert "ожидается 2 · принято 2" in page


def test_returns_condition_from_overview_web(auth_client, project_with_packing):
    """Состояние принятой единицы можно поменять прямо на обзоре, не только на сканировании."""
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    token = _csrf(auth_client, f"{base}/scan")
    auth_client.post(f"{base}/scan", data={"barcode": "S1", "csrf_token": token})

    overview = auth_client.get(base).text
    si_id = re.search(r"/serial/(\d+)/condition", overview).group(1)

    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/serial/{si_id}/condition",
        data={"condition": "defect", "comment": "скол корпуса", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(base).text
    assert "Есть дефект" in page


def test_returns_substitute_web(auth_client, db_session, project_with_packing):
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"

    serial_model = next(
        m for m in eq_service.list_models(db_session, eq_service.ModelFilters())
        if m.name == "Микшер"
    )
    item_service.create_item(db_session, serial_model, EquipmentItemInput(barcode="S3"), user_id=None)

    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    token = _csrf(auth_client, f"{base}/scan")
    r = auth_client.post(f"{base}/scan", data={"barcode": "S3", "csrf_token": token})
    body = r.json()
    assert body["result"] == "substitute_candidate"
    assert body["pending_barcode"] in ("S1", "S2")

    token = _csrf(auth_client, f"{base}/scan")
    r = auth_client.post(
        f"{base}/serial/substitute",
        data={
            "pending_serial_item_id": body["pending_serial_item_id"],
            "barcode": "S3",
            "csrf_token": token,
        },
    )
    assert r.json()["ok"] is True

    page = auth_client.get(base).text
    assert "S3" in page
    assert body["pending_barcode"] not in page


def test_returns_gate_blocks_project_completion(auth_client, project_with_packing):
    pid = project_with_packing.id
    base = f"/projects/{pid}/returns"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    # Попытка завершить проект до приёмки — статус не меняется
    token = _csrf(auth_client, f"/projects/{pid}")
    auth_client.post(
        f"/projects/{pid}/status",
        data={"status": "completed", "csrf_token": token},
        follow_redirects=False,
    )
    assert "Отгружено" in auth_client.get(f"/projects/{pid}").text

    # Принимаем всё и завершаем приёмку
    token = _csrf(auth_client, f"{base}/scan")
    auth_client.post(f"{base}/scan", data={"barcode": "S1", "csrf_token": token})
    auth_client.post(f"{base}/scan", data={"barcode": "S2", "csrf_token": token})
    overview = auth_client.get(base).text
    qty_line_id = re.search(r"/lines/(\d+)/quantity", overview).group(1)
    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/lines/{qty_line_id}/quantity",
        data={"returned_quantity": "8", "csrf_token": token},
        follow_redirects=False,
    )
    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/status", data={"status": "received", "csrf_token": token}, follow_redirects=False
    )
    assert "Принято" in auth_client.get(base).text

    # Теперь завершение проекта проходит
    token = _csrf(auth_client, f"/projects/{pid}")
    auth_client.post(
        f"/projects/{pid}/status",
        data={"status": "completed", "csrf_token": token},
        follow_redirects=False,
    )
    assert "Завершён" in auth_client.get(f"/projects/{pid}").text
