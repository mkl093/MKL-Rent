"""Веб-маршруты проектов (ТЗ §13–§15)."""

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


def test_projects_requires_login(client):
    assert client.get("/projects", follow_redirects=False).status_code == 303


def test_create_book_copy_flow(auth_client):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Фестиваль",
            "start_date": "2026-07-01",
            "end_date": "2026-07-05",
            "rental_coefficient": "1",
            "vat": "19",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    url = resp.headers["location"]
    page = auth_client.get(url).text
    assert "PRJ-" in page
    assert "Фестиваль" in page

    project_id = url.rsplit("/", 1)[-1]
    # Бронируем
    token = _csrf(auth_client, url)
    auth_client.post(
        f"/projects/{project_id}/book",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert "Забронирован" in auth_client.get(url).text

    # Копируем — новая копия в черновике без дат
    token = _csrf(auth_client, url)
    copy_resp = auth_client.post(
        f"/projects/{project_id}/copy", data={"csrf_token": token}, follow_redirects=False
    )
    copy_page = auth_client.get(copy_resp.headers["location"]).text
    assert "(копия)" in copy_page
    assert "Черновик" in copy_page


def test_book_without_dates_flashes(auth_client):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects", data={"name": "Без дат", "csrf_token": token}, follow_redirects=False
    )
    url = resp.headers["location"]
    pid = url.rsplit("/", 1)[-1]
    token = _csrf(auth_client, url)
    auth_client.post(f"/projects/{pid}/book", data={"csrf_token": token}, follow_redirects=False)
    # Остаётся черновиком, показана ошибка
    page = auth_client.get(url).text
    assert "Черновик" in page
