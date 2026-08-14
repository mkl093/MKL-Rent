"""Справочник персонала через интерфейс (ТЗ §54)."""

from __future__ import annotations

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


def _csrf(client, url="/staff") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_staff_page_requires_login(client):
    resp = client.get("/staff", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_create_edit_delete_employee(auth_client, db_session):
    auth_client.post(
        "/staff",
        data={
            "first_name": "Иван",
            "last_name": "Иванов",
            "position": "Монтажник",
            "is_active": "1",
            "csrf_token": _csrf(auth_client),
        },
        follow_redirects=False,
    )
    listing = auth_client.get("/staff").text
    assert "Иванов" in listing

    from app.staff import service as staff_service

    employee = staff_service.list_employees(db_session, staff_service.EmployeeFilters())[0]

    auth_client.post(
        f"/staff/{employee.id}",
        data={
            "first_name": "Иван",
            "last_name": "Иванов-Петров",
            "position": "Бригадир",
            "is_active": "1",
            "csrf_token": _csrf(auth_client, f"/staff/{employee.id}/edit"),
        },
        follow_redirects=False,
    )
    assert "Иванов-Петров" in auth_client.get("/staff").text

    auth_client.post(
        f"/staff/{employee.id}/delete",
        data={"csrf_token": _csrf(auth_client)},
        follow_redirects=False,
    )
    assert "Иванов-Петров" not in auth_client.get("/staff").text


def test_staff_list_grouped_by_department_order(auth_client, db_session):
    from app.staff import service as staff_service
    from app.staff.schemas import DepartmentInput, EmployeeInput

    dept_b = staff_service.create_department(db_session, DepartmentInput(name="Свет", sort_order=2))
    dept_a = staff_service.create_department(db_session, DepartmentInput(name="Звук", sort_order=1))
    staff_service.create_employee(
        db_session, EmployeeInput(first_name="Анна", last_name="Светова", department_id=dept_b.id)
    )
    staff_service.create_employee(
        db_session, EmployeeInput(first_name="Борис", last_name="Звуков", department_id=dept_a.id)
    )
    staff_service.create_employee(
        db_session, EmployeeInput(first_name="Вера", last_name="Безотдельная")
    )

    page = auth_client.get("/staff").text
    assert page.index("Звук") < page.index("Свет") < page.index("Без отдела")
    assert "Безотдельная" in page


def test_employee_create_without_csrf_forbidden(auth_client):
    resp = auth_client.post(
        "/staff", data={"first_name": "Пётр", "last_name": "Петров"}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_department_create_and_delete(auth_client):
    token = _csrf(auth_client, "/staff/departments")
    auth_client.post(
        "/staff/departments",
        data={"name": "Монтаж", "sort_order": "0", "csrf_token": token},
        follow_redirects=False,
    )
    listing = auth_client.get("/staff/departments").text
    assert "Монтаж" in listing


# --- Календарь занятости (API) -----------------------------------------------


@pytest.fixture
def employee(db_session):
    from app.staff import service as staff_service
    from app.staff.schemas import EmployeeInput

    return staff_service.create_employee(
        db_session, EmployeeInput(first_name="Иван", last_name="Иванов", position="Монтажник")
    )


def test_calendar_page_requires_login(client):
    resp = client.get("/calendar", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_calendar_page_renders(auth_client):
    resp = auth_client.get("/calendar")
    assert resp.status_code == 200
    assert "Календарь занятости" in resp.text


def test_view_switch_links_preserve_anchor_date(auth_client):
    """Переключение Day/Week/Month/Grid не должно сбрасывать фокус на начало
    периода (понедельник недели, 1-е число месяца) — должна сохраняться
    исходная выбранная дата (баг: ссылки использовали range_start)."""
    # 13 августа 2026 — четверг, не совпадает ни с началом недели, ни месяца.
    resp = auth_client.get("/calendar?view=week&start=2026-08-13")
    assert resp.status_code == 200
    for view in ("day", "week", "month", "grid"):
        assert f"view={view}&start=2026-08-13" in resp.text


def test_assignments_api_only_requested_range(auth_client, employee):
    _create_assignment(auth_client, employee.id, "2026-08-01T10:00:00", "2026-08-01T18:00:00")
    _create_assignment(auth_client, employee.id, "2026-08-10T10:00:00", "2026-08-10T18:00:00")

    resp = auth_client.get(
        "/calendar/api/assignments",
        params={"start": "2026-08-05T00:00:00", "end": "2026-08-15T00:00:00"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["starts_at"] == "2026-08-10T10:00:00"


def test_create_assignment_without_csrf_forbidden(auth_client, employee):
    resp = auth_client.post(
        "/calendar/api/assignments",
        data={
            "employee_id": employee.id,
            "type": "busy",
            "starts_at": "2026-08-10T10:00:00",
            "ends_at": "2026-08-10T18:00:00",
        },
    )
    assert resp.status_code == 403


def test_update_assignment_conflict_then_confirm(auth_client, employee):
    _create_assignment(auth_client, employee.id, "2026-08-10T10:00:00", "2026-08-10T18:00:00")
    r2 = _create_assignment(auth_client, employee.id, "2026-09-10T10:00:00", "2026-09-10T18:00:00")
    a2_id = r2.json()["assignment"]["id"]

    token = _csrf(auth_client, "/calendar")
    resp = auth_client.post(
        f"/calendar/api/assignments/{a2_id}",
        data={
            "employee_id": employee.id,
            "type": "busy",
            "starts_at": "2026-08-10T12:00:00",
            "ends_at": "2026-08-10T20:00:00",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 409
    assert len(resp.json()["conflicts"]) == 1

    resp2 = auth_client.post(
        f"/calendar/api/assignments/{a2_id}",
        data={
            "employee_id": employee.id,
            "type": "busy",
            "starts_at": "2026-08-10T12:00:00",
            "ends_at": "2026-08-10T20:00:00",
            "confirm": "1",
            "csrf_token": token,
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True


def test_delete_assignment_via_api(auth_client, employee):
    r1 = _create_assignment(auth_client, employee.id, "2026-08-10T10:00:00", "2026-08-10T18:00:00")
    assignment_id = r1.json()["assignment"]["id"]
    token = _csrf(auth_client, "/calendar")
    resp = auth_client.post(
        f"/calendar/api/assignments/{assignment_id}/delete", data={"csrf_token": token}
    )
    assert resp.status_code == 200
    listing = auth_client.get(
        "/calendar/api/assignments",
        params={"start": "2026-08-01T00:00:00", "end": "2026-08-20T00:00:00"},
    ).json()
    assert listing == []


def test_bulk_assignment_creates_for_each_employee(auth_client, employee, db_session):
    from app.staff import service as staff_service
    from app.staff.schemas import EmployeeInput

    other = staff_service.create_employee(
        db_session, EmployeeInput(first_name="Пётр", last_name="Петров", position="Водитель")
    )
    token = _csrf(auth_client, "/calendar")
    resp = auth_client.post(
        "/calendar/api/assignments/bulk",
        data={
            "employee_ids": [employee.id, other.id],
            "type": "project",
            "starts_at": "2026-08-10T08:00:00",
            "ends_at": "2026-08-10T18:00:00",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["created"]) == 2


def _create_assignment(client, employee_id, starts_at, ends_at):
    token = _csrf(client, "/calendar")
    return client.post(
        "/calendar/api/assignments",
        data={
            "employee_id": employee_id,
            "type": "busy",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "csrf_token": token,
        },
    )
