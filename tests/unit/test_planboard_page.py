"""Страница календаря склада: окно занятости, мобильная вёрстка, вход в htmx (ТЗ §15.3)."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects import service as proj_service
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput

PAGE = "/inventory/planboard"
CELL = "/inventory/planboard/cell"


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
def booked(db_session):
    """Модель «Колонка» (8 шт.) и бронь 3 шт. на 3–6 июля 2026."""
    cat = cat_service.create_category(db_session, "Звук")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=8,
            base_price_eur=Decimal("0"),
        ),
    )
    proj = proj_service.create_project(
        db_session,
        ProjectInput(name="Концерт", start_date=date(2026, 7, 3), end_date=date(2026, 7, 6)),
    )
    proj.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=proj.id, model_id=model.id, quantity=3))
    db_session.commit()
    return model


def _page(client, **params):
    resp = client.get(PAGE, params={"start": "2026-07-01", "span": 14, **params})
    assert resp.status_code == 200
    return resp.text


# --- Вход в htmx-запросах -------------------------------------------------------


def test_htmx_request_without_login_redirects_whole_page(client):
    # Без этого htmx прошёл бы 303 сам и вставил страницу входа в окно занятости.
    resp = client.get(
        CELL,
        params={"model_id": 1, "d": "2026-07-04"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/login"
    assert resp.text == ""


def test_plain_request_without_login_still_redirects(client):
    resp = client.get(PAGE, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert "HX-Redirect" not in resp.headers


def test_guest_htmx_request_without_login_redirects_whole_page(client):
    resp = client.get("/guest/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/guest/login"
    plain = client.get("/guest/", follow_redirects=False)
    assert plain.status_code == 303
    assert plain.headers["location"] == "/guest/login"


# --- Окно занятости -------------------------------------------------------------


def test_busy_cell_opens_modal_and_is_keyboard_focusable(auth_client, booked):
    html = _page(auth_client)
    # Детали грузятся в тело окна, а не в панель под таблицей.
    modal = re.search(
        r'<div class="modal fade" id="pb-modal".*?</div>\s*</div>\s*</div>', html, re.S
    )
    assert modal and 'id="pb-detail"' in modal.group(0)
    # Цель и способ вставки заданы на таблице и наследуются ячейками.
    assert re.search(r'<table [^>]*hx-target="#pb-detail"[^>]*hx-swap="innerHTML"', html)
    cells = re.findall(r"<td class=\"pb-cell[^>]*>", html)
    assert len(cells) == 4  # 3–6 июля
    for td in cells:
        assert 'role="button"' in td and 'tabindex="0"' in td
        assert f'hx-get="{CELL}?model_id={booked.id}&d=2026-07-0' in td
        assert "hx-target" not in td


def test_cell_fragment_lists_project(auth_client, booked):
    resp = auth_client.get(CELL, params={"model_id": booked.id, "d": "2026-07-04"})
    assert resp.status_code == 200
    assert "Колонка" in resp.text and "04.07.2026" in resp.text
    assert "Концерт" in resp.text and "&times; 3" in resp.text
    assert "btn-close" not in resp.text  # закрытие — в шапке окна


def test_cell_fragment_free_day_and_unknown_model(auth_client, booked):
    free = auth_client.get(CELL, params={"model_id": booked.id, "d": "2026-07-10"})
    assert "На этот день занятости нет" in free.text
    missing = auth_client.get(CELL, params={"model_id": 999_999, "d": "2026-07-04"})
    assert missing.status_code == 200
    assert "Модель или дата не найдены" in missing.text
    bad_day = auth_client.get(CELL, params={"model_id": booked.id, "d": "не дата"})
    assert "Модель или дата не найдены" in bad_day.text


# --- Вёрстка ----------------------------------------------------------------------


def test_table_scrolls_in_own_area_with_sticky_group_labels(auth_client, booked):
    html = _page(auth_client)
    assert 'id="pb-scroll"' in html
    assert 'class="table-responsive"' not in html
    group = re.search(r'<td class="pb-group[^"]*" colspan="15">(.*?)</td>', html)
    assert group and group.group(1) == '<span class="pb-group-label">Звук</span>'


@pytest.mark.parametrize(
    ("params", "badge"),
    [({}, None), ({"q": "Колон"}, "1"), ({"q": "Колон", "category_id": "__cat__"}, "2")],
)
def test_filter_button_shows_active_filter_count(auth_client, booked, params, badge):
    params = {k: (str(booked.category_id) if v == "__cat__" else v) for k, v in params.items()}
    html = _page(auth_client, **params)
    # Форма свёрнута на телефоне и всегда видна с md.
    assert '<div class="collapse d-md-block" id="pb-filters">' in html
    button = re.search(
        r'<button class="btn [^"]*"[^>]*data-bs-target="#pb-filters".*?</button>', html, re.S
    )
    assert button
    if badge is None:
        assert "badge" not in button.group(0)
        assert "btn-outline-secondary" in button.group(0)
    else:
        assert f'<span class="badge text-bg-primary">{badge}</span>' in button.group(0)
        assert "btn-outline-primary" in button.group(0)
