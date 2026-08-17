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


def test_project_detail_shows_staff_assignments(auth_client, db_session):
    from datetime import UTC, datetime

    from app.staff import service as staff_service
    from app.staff.enums import AssignmentStatus, AssignmentType
    from app.staff.schemas import AssignmentInput, EmployeeInput

    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={"name": "Концерт", "rental_coefficient": "1", "vat": "0", "csrf_token": token},
        follow_redirects=False,
    )
    url = resp.headers["location"]
    project_id = int(url.rsplit("/", 1)[-1])

    employee = staff_service.create_employee(
        db_session, EmployeeInput(first_name="Иван", last_name="Монтажников")
    )
    cancelled_employee = staff_service.create_employee(
        db_session, EmployeeInput(first_name="Пётр", last_name="Отменённый")
    )
    staff_service.create_assignment(
        db_session,
        AssignmentInput(
            employee_id=employee.id,
            project_id=project_id,
            type=AssignmentType.PROJECT,
            starts_at=datetime(2026, 7, 1, 8, tzinfo=UTC),
            ends_at=datetime(2026, 7, 1, 18, tzinfo=UTC),
        ),
    )
    staff_service.create_assignment(
        db_session,
        AssignmentInput(
            employee_id=cancelled_employee.id,
            project_id=project_id,
            type=AssignmentType.PROJECT,
            status=AssignmentStatus.CANCELLED,
            starts_at=datetime(2026, 7, 1, 8, tzinfo=UTC),
            ends_at=datetime(2026, 7, 1, 18, tzinfo=UTC),
        ),
    )

    page = auth_client.get(url).text
    assert "Монтажников" in page
    assert "Отменённый" not in page


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


# --- Цветовая маркировка в календаре (ТЗ §54.3) --------------------------------


def test_create_project_with_color_and_calendar_bar(auth_client, db_session):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Цветной",
            "rental_coefficient": "1",
            "vat": "0",
            "color": "#039BE5",
            "calendar_bar": "1",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    url = resp.headers["location"]
    project_id = int(url.rsplit("/", 1)[-1])

    from app.projects import service as project_service

    project = project_service.get_project(db_session, project_id)
    assert project.color == "#039be5"
    assert project.calendar_bar is True

    # Форма редактирования подставляет сохранённый цвет обратно в скрытое поле.
    edit_page = auth_client.get(f"/projects/{project_id}/edit").text
    assert 'value="#039be5"' in edit_page
    checkbox = re.search(r'id="calendar_bar"[^>]*>', edit_page).group(0)
    assert "checked" in checkbox


def test_calendar_requires_login(client):
    assert client.get("/projects/calendar", follow_redirects=False).status_code == 303


def test_calendar_shows_overlapping_projects_and_undated_tail(auth_client):
    token = _csrf(auth_client, "/projects/new")
    r1 = auth_client.post(
        "/projects",
        data={
            "name": "Первый",
            "start_date": "2026-07-01",
            "end_date": "2026-07-05",
            "rental_coefficient": "1",
            "vat": "0",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    token = _csrf(auth_client, "/projects/new")
    r2 = auth_client.post(
        "/projects",
        data={
            "name": "Второй",
            "start_date": "2026-07-04",
            "end_date": "2026-07-08",
            "rental_coefficient": "1",
            "vat": "0",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    token = _csrf(auth_client, "/projects/new")
    auth_client.post(
        "/projects",
        data={
            "name": "Без дат",
            "rental_coefficient": "1",
            "vat": "0",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    id1 = int(r1.headers["location"].rsplit("/", 1)[-1])
    id2 = int(r2.headers["location"].rsplit("/", 1)[-1])

    page = auth_client.get(
        "/projects/calendar?start=2026-07-01&span=14", follow_redirects=False
    ).text
    assert "Первый" in page and "Второй" in page
    assert "Без дат" in page

    row1 = re.search(rf'data-project-id="{id1}" data-overlaps="([^"]*)"', page).group(1)
    assert str(id2) in row1.split(",")


def test_calendar_grid_view_shows_project_bar_link(auth_client):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Сеточный",
            "start_date": "2026-08-10",
            "end_date": "2026-08-15",
            "rental_coefficient": "1",
            "vat": "0",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    project_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    page = auth_client.get(
        "/projects/calendar?view=grid&start=2026-08-01", follow_redirects=False
    ).text
    assert "Август 2026" in page
    assert f'href="/projects/{project_id}"' in page


def test_calendar_invalid_view_falls_back_to_gantt(auth_client):
    page = auth_client.get(
        "/projects/calendar?view=bogus&start=2026-08-01", follow_redirects=False
    ).text
    assert "Диаграмма" in page
    assert "sc-grid-table" not in page


def test_invalid_color_is_silently_dropped(auth_client, db_session):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Без валидного цвета",
            "rental_coefficient": "1",
            "vat": "0",
            "color": "not-a-color",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    project_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    from app.projects import service as project_service

    project = project_service.get_project(db_session, project_id)
    assert project.color is None
