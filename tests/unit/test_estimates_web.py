"""Веб-маршруты сметы (ТЗ §16)."""

import re
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service


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
def model(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=20,
            base_price_eur=Decimal("100.00"),
        ),
    )


def test_estimate_flow(auth_client, model):
    # Проект с датами
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Концерт",
            "start_date": "2026-07-01",
            "end_date": "2026-07-05",
            "rental_coefficient": "1",
            "vat": "19",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    pid = resp.headers["location"].rsplit("/", 1)[-1]
    est_url = f"/projects/{pid}/estimate"

    # Страница сметы
    page = auth_client.get(est_url)
    assert page.status_code == 200
    assert "EST-" in page.text

    # Добавляем модель через подбор
    token = _csrf(auth_client, f"{est_url}/add")
    auth_client.post(
        f"{est_url}/add",
        data={
            f"select_{model.id}": "1",
            f"qty_{model.id}": "2",
            "mode": "merge",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    page = auth_client.get(est_url).text
    assert "Колонка" in page
    assert "200.00" in page  # 100 × 2 × 1

    # Скидка 10% → появляется строка скидки и VAT
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/discount",
        data={"discount_percent": "10", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(est_url).text
    assert "Скидка" in page

    # Бронь синхронизирована → деталь проекта показывает требуется 2
    detail = auth_client.get(f"/projects/{pid}").text
    assert "Колонка" in detail


def test_custom_line_via_web(auth_client):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects", data={"name": "X", "csrf_token": token}, follow_redirects=False
    )
    pid = resp.headers["location"].rsplit("/", 1)[-1]
    est_url = f"/projects/{pid}/estimate"
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/custom",
        data={
            "name": "Доставка",
            "quantity": "1",
            "unit_price": "250",
            "coefficient": "1",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    page = auth_client.get(est_url).text
    assert "Доставка" in page
    assert "Прочее" in page
