"""Управление пользователями через интерфейс (ТЗ §4)."""

import re

import pytest

from app.auth import service as auth_service


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


def _csrf(client, url="/users") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_create_block_password_delete(auth_client, db_session):
    # Создание
    auth_client.post(
        "/users",
        data={"username": "bob", "password": "pw1", "csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    assert "bob" in auth_client.get("/users").text
    bob = auth_service.get_user_by_username(db_session, "bob")

    # Дубликат — отклонён
    auth_client.post(
        "/users",
        data={"username": "bob", "password": "x", "csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    assert auth_service.count_users(db_session) == 2

    # Сброс пароля → новый пароль работает
    auth_client.post(
        f"/users/{bob.id}/password",
        data={"password": "pw2", "csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert auth_service.authenticate(db_session, "bob", "pw2").username == "bob"

    # Блокировка
    auth_client.post(
        f"/users/{bob.id}/block",
        data={"blocked": "1", "csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert auth_service.get_user_by_username(db_session, "bob").is_blocked

    # Удаление
    auth_client.post(
        f"/users/{bob.id}/delete", data={"csrf_token": _csrf(auth_client)}, follow_redirects=False
    )
    db_session.expire_all()
    assert auth_service.get_user_by_username(db_session, "bob") is None


def test_cannot_delete_or_block_self(auth_client, db_session):
    admin = auth_service.get_user_by_username(db_session, "admin")
    auth_client.post(
        f"/users/{admin.id}/delete", data={"csrf_token": _csrf(auth_client)}, follow_redirects=False
    )
    auth_client.post(
        f"/users/{admin.id}/block",
        data={"blocked": "1", "csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    db_session.expire_all()
    admin = auth_service.get_user_by_username(db_session, "admin")
    assert admin is not None and not admin.is_blocked
