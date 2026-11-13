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


def _version_url(client, pid) -> str:
    """Адрес активной версии сметы единственной площадки (через редирект /estimate)."""
    resp = client.get(f"/projects/{pid}/estimate")
    assert resp.status_code == 200
    return resp.url.path


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
    est_url = _version_url(auth_client, pid)

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
    est_url = _version_url(auth_client, pid)
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


def test_estimate_coefficient_and_hide_prices_via_web(auth_client, db_session, model):
    from app.estimates.models import Estimate

    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={"name": "Выставка", "rental_coefficient": "1", "vat": "19", "csrf_token": token},
        follow_redirects=False,
    )
    pid = resp.headers["location"].rsplit("/", 1)[-1]
    est_url = _version_url(auth_client, pid)

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

    # Коэффициент сметы 2 → строка 100 × 2 × 2 = 400
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/coefficient",
        data={"rental_coefficient": "2", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(est_url).text
    assert "400.00" in page
    assert "Итого по разделу" in page

    # Некорректный коэффициент не применяется
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/coefficient",
        data={"rental_coefficient": "0", "csrf_token": token},
        follow_redirects=False,
    )
    estimate = db_session.query(Estimate).filter_by(project_id=int(pid)).one()
    db_session.refresh(estimate)
    assert estimate.rental_coefficient == Decimal("2")

    # Ручной коэффициент строки → кнопка сброса; сброс возвращает коэффициент сметы
    line = estimate.lines[0]
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/lines/{line.id}",
        data={"quantity": "2", "unit_price": "100", "coefficient": "3", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(est_url).text
    assert f"reset-coef-{line.id}" in page
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/lines/{line.id}/reset-coefficient",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    db_session.refresh(line)
    assert line.coefficient == Decimal("2")
    assert line.coefficient_custom is False

    # «Скрыть стоимость»: флаг ставится, редактор по-прежнему показывает цены
    token = _csrf(auth_client, est_url)
    auth_client.post(
        f"{est_url}/hide-prices",
        data={"hide_prices": "1", "csrf_token": token},
        follow_redirects=False,
    )
    db_session.refresh(estimate)
    assert estimate.hide_prices is True
    page = auth_client.get(est_url).text
    assert "100.00" in page  # цена в редакторе видна
    assert "В документах стоимость позиций скрыта" in page

    # Снятие галочки (чекбокс не передан)
    token = _csrf(auth_client, est_url)
    auth_client.post(f"{est_url}/hide-prices", data={"csrf_token": token}, follow_redirects=False)
    db_session.refresh(estimate)
    assert estimate.hide_prices is False
