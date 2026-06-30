"""Веб-слой: вход, CSRF, защита страниц (ТЗ §4, §41.2)."""

import re

from app.auth import service


def _csrf(client) -> str:
    html = client.get("/login").text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF-токен не найден на странице входа"
    return match.group(1)


def test_login_page_ok(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Войти" in resp.text


def test_protected_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_requires_csrf(client, db_session):
    service.create_user(db_session, "admin", "pass123")
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "pass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_login_flow_success(client, db_session):
    service.create_user(db_session, "admin", "pass123")
    token = _csrf(client)
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # Теперь главная доступна.
    home = client.get("/")
    assert home.status_code == 200
    assert "Главная" in home.text


def test_login_wrong_password(client, db_session):
    service.create_user(db_session, "admin", "pass123")
    token = _csrf(client)
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "WRONG", "csrf_token": token},
    )
    assert resp.status_code == 200
    assert "Неверный логин или пароль" in resp.text


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
