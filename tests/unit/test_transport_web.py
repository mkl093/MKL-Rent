"""Веб-маршруты транспорта: справочник машин и доска распределения (ТЗ: транспорт)."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput
from app.transport import service as transport_service


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
def project_with_packing(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("2.0"),
        ),
    )
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, model, 10)
    packing = packing_service.create_from_estimate(db_session, project)
    line = next(ln for ln in packing.lines if ln.model_id == model.id)
    return project, line


def test_vehicle_directory_crud(auth_client):
    token = _csrf(auth_client, "/transport/new")
    auth_client.post(
        "/transport",
        data={
            "name": "Газель",
            "plate_number": "А123ВС77",
            "max_weight_kg": "1500",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    page = auth_client.get("/transport").text
    assert "Газель" in page
    assert "А123ВС77" in page


def test_project_transport_requires_packing_first(auth_client, db_session):
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Без сметы", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    resp = auth_client.get(f"/projects/{project.id}/transport", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith(f"/projects/{project.id}/packing")


def test_add_vehicle_and_assign_via_checkboxes(auth_client, db_session, project_with_packing):
    project, line = project_with_packing
    vehicle = transport_service.create_vehicle(
        db_session, name="Газель", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    base = f"/projects/{project.id}/transport"

    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/vehicles", data={"vehicle_id": str(vehicle.id), "csrf_token": token}, follow_redirects=False
    )
    page = auth_client.get(base).text
    assert "Газель" in page

    pv = transport_service.list_project_vehicles(db_session, project)[0]
    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/assign",
        data={"line_id": str(line.id), "target": str(pv.id), "csrf_token": token},
        follow_redirects=False,
    )
    db_session.refresh(line)
    assert transport_service.remaining_quantity(db_session, project, line) == 0


def test_partial_assign_via_qty_field(auth_client, db_session, project_with_packing):
    """Форма с чекбоксами: поле qty_{line_id} дробит позицию (6 из 10)."""
    project, line = project_with_packing
    vehicle = transport_service.create_vehicle(
        db_session, name="Газель", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    base = f"/projects/{project.id}/transport"
    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/vehicles", data={"vehicle_id": str(vehicle.id), "csrf_token": token}, follow_redirects=False
    )
    pv = transport_service.list_project_vehicles(db_session, project)[0]

    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/assign",
        data={
            "line_id": str(line.id),
            "target": str(pv.id),
            f"qty_{line.id}": "6",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert transport_service.remaining_quantity(db_session, project, line) == 4


def test_api_move_from_pool_to_vehicle(auth_client, db_session, project_with_packing):
    project, line = project_with_packing
    vehicle = transport_service.create_vehicle(
        db_session, name="Газель", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    pv = transport_service.add_vehicle_to_project(db_session, project, vehicle)
    base = f"/projects/{project.id}/transport"
    token = _csrf(auth_client, base)

    resp = auth_client.post(
        f"{base}/api/move",
        data={
            "line_id": str(line.id),
            "source": "pool",
            "target": str(pv.id),
            "quantity": "4",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert transport_service.remaining_quantity(db_session, project, line) == 6


def test_api_move_rejects_bad_csrf(auth_client, db_session, project_with_packing):
    project, line = project_with_packing
    resp = auth_client.post(
        f"/projects/{project.id}/transport/api/move",
        data={"line_id": str(line.id), "source": "pool", "target": "pool", "csrf_token": "bad"},
    )
    assert resp.status_code == 403


def test_removing_project_vehicle_returns_lines_to_pool(auth_client, db_session, project_with_packing):
    project, line = project_with_packing
    vehicle = transport_service.create_vehicle(
        db_session, name="Газель", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    pv = transport_service.add_vehicle_to_project(db_session, project, vehicle)
    transport_service.assign(db_session, project, pv, line, 5)

    base = f"/projects/{project.id}/transport"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/vehicles/{pv.id}/delete", data={"csrf_token": token}, follow_redirects=False)

    assert transport_service.remaining_quantity(db_session, project, line) == 10
