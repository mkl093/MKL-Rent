"""Гостевой портал через веб-интерфейс: видимость каталога, уровни, админка."""

import re
from decimal import Decimal

import pytest

from app.guests import service as guest_service
from app.guests.models import GUEST_LEVEL_AVAILABILITY, GUEST_LEVEL_VIEW
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def _make_model(db_session, category_name, model_name, quantity=5):
    cat = cat_service.create_category(db_session, category_name)
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name=model_name,
            accounting_type=AccountingType.QUANTITY,
            total_quantity=quantity,
            base_price_eur=Decimal("0"),
        ),
    )
    return cat, model


@pytest.fixture
def guest_login():
    def _login(client, db_session, level=GUEST_LEVEL_VIEW, username="acme"):
        guest_service.create_guest(db_session, username, "pass123", "Acme Events", level)
        token = _csrf(client, "/guest/login")
        client.post(
            "/guest/login",
            data={"username": username, "password": "pass123", "csrf_token": token},
            follow_redirects=False,
        )
        return client

    return _login


@pytest.fixture
def staff_login():
    def _login(client, db_session):
        from app.auth import service as auth_service

        auth_service.create_user(db_session, "admin", "pass123")
        token = _csrf(client, "/login")
        client.post(
            "/login",
            data={"username": "admin", "password": "pass123", "csrf_token": token},
            follow_redirects=False,
        )
        return client

    return _login


# --- Логин -----------------------------------------------------------------


def test_guest_login_wrong_password(client, db_session, guest_login):
    guest_service.create_guest(db_session, "acme", "pass123", "Acme Events")
    token = _csrf(client, "/guest/login")
    resp = client.post(
        "/guest/login",
        data={"username": "acme", "password": "wrong", "csrf_token": token},
    )
    assert resp.status_code == 200
    assert "Incorrect username or password" in resp.text


def test_guest_login_empty_fields_does_not_crash(client, db_session):
    """Пустые логин/пароль раньше приводили к 422 от FastAPI вместо страницы с ошибкой."""
    token = _csrf(client, "/guest/login")
    resp = client.post(
        "/guest/login",
        data={"username": "", "password": "", "csrf_token": token},
    )
    assert resp.status_code == 200
    assert "Please enter your username and password" in resp.text


def test_guest_login_missing_fields_does_not_crash(client, db_session):
    token = _csrf(client, "/guest/login")
    resp = client.post("/guest/login", data={"csrf_token": token})
    assert resp.status_code == 200


def test_guest_login_success_redirects_to_catalog(client, db_session):
    guest_service.create_guest(db_session, "acme", "pass123", "Acme Events")
    token = _csrf(client, "/guest/login")
    resp = client.post(
        "/guest/login",
        data={"username": "acme", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/guest/"


def test_catalog_requires_login(client):
    resp = client.get("/guest/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/guest/login"


# --- Видимость каталога -----------------------------------------------------


def test_catalog_hides_archived_and_guest_hidden_categories(client, db_session, guest_login):
    _cat_a, model_a = _make_model(db_session, "Свет", "Прожектор")
    cat_b, model_b = _make_model(db_session, "Звук", "Колонка")
    _cat_c, model_c = _make_model(db_session, "Видео", "Камера")

    cat_b.hidden_from_guests = True
    model_c.is_archived = True
    db_session.commit()

    guest_login(client, db_session)
    body = client.get("/guest/", params={"view": "table"}).text

    assert model_a.name in body
    assert model_b.name not in body
    assert model_c.name not in body


def test_catalog_search_filters_by_name(client, db_session, guest_login):
    cat = cat_service.create_category(db_session, "Свет")
    for name in ("Прожектор ML-1", "Пульт DMX"):
        eq_service.create_model(
            db_session,
            EquipmentModelCreate(
                category_id=cat.id,
                name=name,
                accounting_type=AccountingType.QUANTITY,
                total_quantity=1,
                base_price_eur=Decimal("0"),
            ),
        )

    guest_login(client, db_session)
    body = client.get("/guest/", params={"q": "Прожектор", "view": "table"}).text

    assert "Прожектор ML-1" in body
    assert "Пульт DMX" not in body


# --- Уровни доступа ----------------------------------------------------------


def test_level1_has_no_date_search_and_ignores_date_params(client, db_session, guest_login):
    _cat, model = _make_model(db_session, "Свет", "Прожектор", quantity=5)
    guest_login(client, db_session, level=GUEST_LEVEL_VIEW)

    body = client.get(
        "/guest/", params={"start": "2026-09-01", "end": "2026-09-05", "view": "table"}
    ).text

    # Дата-поля не отрисованы вообще — уровень не раскрывается клиенту.
    assert 'name="start"' not in body
    assert "5" in body  # общее количество на складе всё равно видно


def test_level2_shows_availability_for_period(client, db_session, guest_login):
    _cat, model = _make_model(db_session, "Свет", "Прожектор", quantity=5)
    guest_login(client, db_session, level=GUEST_LEVEL_AVAILABILITY)

    body = client.get(
        "/guest/", params={"start": "2026-09-01", "end": "2026-09-05", "view": "table"}
    ).text

    assert 'name="start"' in body
    assert "5 / 5" in body  # ничего не забронировано — всё свободно


def test_level2_deficit_shows_zero_not_negative(client, db_session, guest_login):
    """Перебронь (available < 0 у персонала) — гостю показываем 0, а не минус."""
    from datetime import date

    _cat, model = _make_model(db_session, "Свет", "Прожектор", quantity=5)
    project = create_project(
        db_session,
        ProjectInput(name="Другой", start_date=date(2026, 9, 1), end_date=date(2026, 9, 5)),
    )
    project.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=project.id, model_id=model.id, quantity=8))
    db_session.commit()

    guest_login(client, db_session, level=GUEST_LEVEL_AVAILABILITY)
    body = client.get(
        "/guest/", params={"start": "2026-09-01", "end": "2026-09-05", "view": "table"}
    ).text

    assert "-3" not in body
    assert "0 / 5" in body


def test_no_level_wording_ever_shown_to_guest(client, db_session, guest_login):
    """Клиент не должен видеть слово «уровень» нигде на своих страницах (см. фидбек)."""
    _make_model(db_session, "Свет", "Прожектор")
    guest_login(client, db_session, level=GUEST_LEVEL_AVAILABILITY)
    body = client.get("/guest/").text
    assert "уровень" not in body.lower()
    assert "level" not in body.lower()


# --- Плитка / таблица / фото ---------------------------------------------


def test_grid_view_shows_categories_not_models(client, db_session, guest_login):
    """Плитка — это обзор категорий (для больших складов), не карточки моделей."""
    _make_model(db_session, "Свет", "Прожектор ML-1")
    guest_login(client, db_session)
    body = client.get("/guest/").text  # view по умолчанию — grid

    assert "Свет" in body
    assert "Прожектор ML-1" not in body


def test_lists_never_request_real_photos(client, db_session, guest_login):
    """Фото модели грузится только на карточке модели — не на плитке и не в таблице
    (у большого склада куча фото на одной странице ощутимо тормозила загрузку)."""
    _cat, model = _make_model(db_session, "Свет", "Прожектор")
    model.photo_path = "models/whatever.jpg"
    db_session.commit()

    guest_login(client, db_session)
    grid_body = client.get("/guest/").text
    table_body = client.get("/guest/", params={"view": "table"}).text
    detail_body = client.get(f"/guest/models/{model.id}").text

    assert "/photo" not in grid_body
    assert "/photo" not in table_body
    assert f"/guest/models/{model.id}/photo" in detail_body


def test_photo_endpoint_hidden_for_guest_hidden_category(client, db_session, guest_login):
    cat, model = _make_model(db_session, "Звук", "Колонка")
    cat.hidden_from_guests = True
    db_session.commit()
    guest_login(client, db_session)
    resp = client.get(f"/guest/models/{model.id}/photo")
    assert resp.status_code == 404


# --- Админка (персонал) ------------------------------------------------


def test_staff_can_create_and_manage_guest_account(client, db_session, staff_login):
    staff_login(client, db_session)
    token = _csrf(client, "/guest-access")
    client.post(
        "/guest-access",
        data={
            "display_name": "Acme Events",
            "username": "acme",
            "password": "pass123",
            "access_level": "2",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    guest = guest_service.get_guest_by_username(db_session, "acme")
    assert guest is not None
    assert guest.access_level == GUEST_LEVEL_AVAILABILITY

    # Сброс пароля
    client.post(
        f"/guest-access/{guest.id}/password",
        data={"password": "newpass", "csrf_token": _csrf(client, "/guest-access")},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert guest_service.authenticate_guest(db_session, "acme", "newpass").username == "acme"

    # Блокировка
    client.post(
        f"/guest-access/{guest.id}/block",
        data={"blocked": "1", "csrf_token": _csrf(client, "/guest-access")},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert guest_service.get_guest_by_username(db_session, "acme").is_blocked

    # Удаление
    client.post(
        f"/guest-access/{guest.id}/delete",
        data={"csrf_token": _csrf(client, "/guest-access")},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert guest_service.get_guest_by_username(db_session, "acme") is None


def test_staff_hide_category_from_guests_toggle(client, db_session, staff_login):
    cat = cat_service.create_category(db_session, "Такелаж")
    staff_login(client, db_session)
    client.post(
        f"/inventory/categories/{cat.id}/hide-from-guests",
        data={"hidden": "1", "csrf_token": _csrf(client, "/inventory/categories")},
        follow_redirects=False,
    )
    db_session.expire_all()
    db_session.refresh(cat)
    assert cat.hidden_from_guests is True
