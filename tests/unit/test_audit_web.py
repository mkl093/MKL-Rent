"""Журнал: запись событий из веб-действий и страница просмотра (ТЗ §29)."""

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


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_project_action_logged_and_shown(auth_client):
    # Создаём проект → событие журнала
    token = _csrf(auth_client, "/projects/new")
    auth_client.post(
        "/projects",
        data={"name": "Фестиваль", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get("/audit")
    assert page.status_code == 200
    assert "Создан проект" in page.text
    assert "Фестиваль" in page.text

    # Фильтр по типу события
    filtered = auth_client.get("/audit", params={"event_type": "project_create"}).text
    assert "Создан проект" in filtered
    empty = auth_client.get("/audit", params={"event_type": "user_manage"}).text
    assert "Записей нет" in empty
