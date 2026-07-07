"""Веб-маршруты packing-листа (ТЗ §17)."""

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
def project_with_estimate(db_session):
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
    est_service.add_model(db_session, estimate, project, model, 8)
    return project


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_packing_create_and_view(auth_client, project_with_estimate):
    pid = project_with_estimate.id
    base = f"/projects/{pid}/packing"

    # До создания — страница-заглушка с кнопкой
    page = auth_client.get(base)
    assert page.status_code == 200
    assert "Создать packing-лист" in page.text

    # Создаём
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)
    page = auth_client.get(base).text
    assert "PL-" in page
    assert "Колонка" in page
    assert "Общий вес" in page

    # Переводим в «Комплектуется»
    token = _csrf(auth_client, base)
    auth_client.post(
        f"{base}/status", data={"status": "picking", "csrf_token": token}, follow_redirects=False
    )
    assert "Комплектуется" in auth_client.get(base).text


def test_packing_add_equipment_web(auth_client, project_with_estimate):
    pid = project_with_estimate.id
    base = f"/projects/{pid}/packing"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)

    # Страница подбора со складским оборудованием
    page = auth_client.get(f"{base}/add")
    assert page.status_code == 200
    assert "Добавить оборудование" in page.text
    assert "Колонка" in page.text
    mid = re.search(r'name="select_(\d+)"', page.text).group(1)

    # Добавляем 5 шт той же модели → план вырастет с 8 до 13 (строка не задвоится)
    token = _csrf(auth_client, f"{base}/add")
    auth_client.post(
        f"{base}/add",
        data={f"select_{mid}": "1", f"qty_{mid}": "5", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(base).text
    assert "план 13" in page


def test_packing_blocks_project_delete(auth_client, project_with_estimate):
    pid = project_with_estimate.id
    base = f"/projects/{pid}/packing"
    token = _csrf(auth_client, base)
    auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)
    # Попытка удалить проект-черновик с packing-листом — отклонена
    token = _csrf(auth_client, f"/projects/{pid}")
    auth_client.post(f"/projects/{pid}/delete", data={"csrf_token": token}, follow_redirects=False)
    assert auth_client.get(f"/projects/{pid}").status_code == 200  # проект на месте
