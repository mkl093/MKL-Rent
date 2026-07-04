"""Веб-маршруты склада (ТЗ §6–§9, §21, §25)."""

import re

import pytest

from app.auth import service as auth_service


@pytest.fixture
def auth_client(client, db_session):
    """Залогиненный клиент."""
    auth_service.create_user(db_session, "admin", "pass123")
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    return client


def _csrf(client, url="/inventory/categories") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_inventory_requires_login(client):
    resp = client.get("/inventory", follow_redirects=False)
    assert resp.status_code == 303


def test_inventory_list_empty(auth_client):
    resp = auth_client.get("/inventory")
    assert resp.status_code == 200
    assert "Склад" in resp.text


def test_create_category_and_model_and_item(auth_client):
    # Категория
    token = _csrf(auth_client)
    auth_client.post(
        "/inventory/categories",
        data={"name": "Звук", "csrf_token": token},
        follow_redirects=False,
    )
    # Получаем id категории со страницы (через форму создания модели)
    new_page = auth_client.get("/inventory/models/new").text
    cat_id = re.search(r'name="category_id"[^>]*>\s*<option value="(\d+)"', new_page).group(1)

    # Создаём посерийную модель
    token = _csrf(auth_client, "/inventory/models/new")
    resp = auth_client.post(
        "/inventory/models",
        data={
            "name": "Микшер X32",
            "category_id": cat_id,
            "accounting_type": "serial",
            "base_price_eur": "150,00",
            "weight_kg": "21,5",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    model_url = resp.headers["location"]
    assert "/inventory/models/" in model_url
    model_id = model_url.rsplit("/", 1)[-1]

    # Добавляем экземпляр
    token = _csrf(auth_client, model_url)
    auth_client.post(
        f"/inventory/models/{model_id}/items",
        data={"barcode": "BC-100", "csrf_token": token},
        follow_redirects=False,
    )
    detail = auth_client.get(model_url).text
    assert "BC-100" in detail

    # Глобальный поиск по штрих-коду → редирект на экземпляр
    found = auth_client.get("/inventory/scan", params={"barcode": "BC-100"}, follow_redirects=False)
    assert found.status_code == 303
    assert "/inventory/items/" in found.headers["location"]

    # Несуществующий штрих-код
    miss = auth_client.get("/inventory/scan", params={"barcode": "NOPE"})
    assert "не найден" in miss.text


def test_create_quantity_model_and_adjust(auth_client):
    token = _csrf(auth_client)
    auth_client.post(
        "/inventory/categories",
        data={"name": "Кабели", "csrf_token": token},
        follow_redirects=False,
    )
    new_page = auth_client.get("/inventory/models/new").text
    cat_id = re.search(r'name="category_id"[^>]*>\s*<option value="(\d+)"', new_page).group(1)

    token = _csrf(auth_client, "/inventory/models/new")
    resp = auth_client.post(
        "/inventory/models",
        data={
            "name": "XLR 5м",
            "category_id": cat_id,
            "accounting_type": "quantity",
            "total_quantity": "20",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    model_url = resp.headers["location"]
    model_id = model_url.rsplit("/", 1)[-1]

    token = _csrf(auth_client, model_url)
    auth_client.post(
        f"/inventory/models/{model_id}/quantity",
        data={"new_quantity": "25", "comment": "приход", "csrf_token": token},
        follow_redirects=False,
    )
    detail = auth_client.get(model_url).text
    assert "25" in detail
    assert "приход" in detail


def test_warehouse_availability_by_dates(auth_client, db_session):
    """Фильтр по датам показывает доступность с учётом брони (ТЗ §15)."""
    from datetime import date
    from decimal import Decimal

    from app.inventory.enums import AccountingType
    from app.inventory.schemas import EquipmentModelCreate
    from app.inventory.services import categories as cat_service
    from app.inventory.services import equipment as eq_service
    from app.projects import service as proj_service
    from app.projects.enums import ProjectStatus
    from app.projects.models import ProjectReservation
    from app.projects.schemas import ProjectInput

    cat = cat_service.create_category(db_session, "Звук")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=20,
            base_price_eur=Decimal("0"),
        ),
    )
    # Забронированный проект держит 5 шт на пересекающийся период.
    neighbor = proj_service.create_project(
        db_session,
        ProjectInput(name="Сосед", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    neighbor.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=neighbor.id, model_id=model.id, quantity=5))
    db_session.commit()

    # Без дат — колонки доступности нет.
    assert "Доступно</th>" not in auth_client.get("/inventory").text

    # На пересекающийся период доступно 20 − 5 = 15.
    page = auth_client.get(
        "/inventory", params={"avail_start": "2026-07-03", "avail_end": "2026-07-04"}
    ).text
    assert "Доступно</th>" in page
    assert "15" in page

    # На непересекающийся период — все 20 доступны.
    page2 = auth_client.get(
        "/inventory", params={"avail_start": "2026-08-01", "avail_end": "2026-08-05"}
    ).text
    assert ">20<" in page2
