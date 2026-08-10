"""Календарь занятости персонала: HTML-оболочка и JSON API таймлайна (ТЗ §54).

Единственный JSON-API в приложении — обоснованное отклонение от конвенции
(остальные роутеры отдают HTML/htmx-фрагменты), см. план реализации: таймлайн
не может работать без JS-виджета. CSRF при этом переиспользуется без изменений
(app.dependencies.verify_csrf читает csrf_token из тела формы), поэтому все
мутирующие запросы уходят как application/x-www-form-urlencoded, а не JSON.

Время на проводе — «настенные часы» в часовом поясе компании (naive-строка
"YYYY-MM-DDTHH:MM:SS"), а не UTC: так JS не обязан ничего знать о часовых
поясах — достаточно читать/писать поля Date без пересчётов, а сервер хранит
и фильтрует по UTC через parse_local_datetime()/to_local().
"""

from __future__ import annotations

import calendar as pycalendar
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db, utcnow
from app.dependencies import render, require_login, verify_csrf
from app.projects.service import list_projects
from app.staff import service
from app.staff.enums import AssignmentStatus, AssignmentType
from app.staff.models import Assignment, Employee
from app.staff.schemas import AssignmentInput, BulkAssignmentInput
from app.utils.timezone import get_app_timezone, parse_local_datetime, to_local

router = APIRouter(prefix="/calendar", tags=["calendar"])

VIEWS = ("day", "week", "month", "grid")


# --- Время на проводе --------------------------------------------------------


def _wire(value: datetime) -> str:
    """UTC → «настенная» строка в часовом поясе компании, без смещения."""
    return to_local(value).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_wire(value: str) -> datetime:
    """«Настенная» строка в часовом поясе компании → UTC для хранения/фильтра."""
    return parse_local_datetime(value)


def _today() -> date:
    return to_local(utcnow()).date()


# --- Диапазон дат тулбара ----------------------------------------------------


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _compute_range(view: str, anchor: date) -> tuple[date, date, date, date]:
    """(начало диапазона, конец диапазона исключительно, начало пред., начало след.)."""
    if view == "day":
        return (
            anchor,
            anchor + timedelta(days=1),
            anchor - timedelta(days=1),
            anchor + timedelta(days=1),
        )
    if view in ("month", "grid"):
        start = anchor.replace(day=1)
        days_in_month = pycalendar.monthrange(start.year, start.month)[1]
        end = start + timedelta(days=days_in_month)
        prev = (start - timedelta(days=1)).replace(day=1)
        return start, end, prev, end
    # week (по умолчанию)
    start = _week_start(anchor)
    end = start + timedelta(days=7)
    return start, end, start - timedelta(days=7), end


# --- Разбор фильтров из query-строки -----------------------------------------


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _split_ids(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(v) for v in value.split(",") if v.strip()]


def _split_str(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v for v in value.split(",") if v.strip()]


# --- Сериализация -------------------------------------------------------------


def _employee_dict(e: Employee) -> dict:
    return {
        "id": e.id,
        "full_name": e.full_name,
        "position": e.position,
        "department": e.department.name if e.department else None,
        "department_id": e.department_id,
        "is_active": e.is_active,
    }


def _assignment_dict(a: Assignment) -> dict:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "project_id": a.project_id,
        "project_number": a.project.number if a.project else None,
        "project_name": a.project.name if a.project else None,
        "project_customer": a.project.customer if a.project else None,
        "project_address": a.project.address if a.project else None,
        "type": a.type.value,
        "type_label": a.type.label,
        "status": a.status.value,
        "status_label": a.status.label,
        "starts_at": _wire(a.starts_at),
        "ends_at": _wire(a.ends_at),
        "title": a.title,
        "comment": a.comment,
        "blocks": a.status.blocks,
    }


# --- HTML-оболочка -------------------------------------------------------------


@router.get("")
def calendar_page(
    request: Request,
    view: str = "week",
    start: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if view not in VIEWS:
        view = "week"
    anchor = date.fromisoformat(start) if start else _today()
    range_start, range_end, prev_start, next_start = _compute_range(view, anchor)

    return render(
        request,
        "staff/calendar.html",
        {
            "page_title": "Календарь занятости",
            "view": view,
            "views": VIEWS,
            "range_start": range_start,
            "range_end": range_end - timedelta(days=1),
            "prev_start": prev_start,
            "next_start": next_start,
            "today": _today(),
            "departments": service.list_departments(db),
            "positions": service.list_positions(db),
            "projects": list_projects(db, archived=False),
            "assignment_types": list(AssignmentType),
            "assignment_statuses": list(AssignmentStatus),
            "timezone_name": str(get_app_timezone()),
        },
        db=db,
        user=user,
    )


# --- JSON API ------------------------------------------------------------------


@router.get("/api/resources")
def api_resources(
    q: str | None = None,
    department_id: str | None = None,
    position: str | None = None,
    show: str = "active",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> list[dict]:
    filters = service.EmployeeFilters(
        query=_opt_str(q),
        department_id=_opt_id(department_id),
        position=_opt_str(position),
        is_active=None if show == "all" else (show != "inactive"),
    )
    return [_employee_dict(e) for e in service.list_employees(db, filters)]


@router.get("/api/assignments")
def api_assignments(
    start: str,
    end: str,
    employee_ids: str | None = None,
    department_id: str | None = None,
    position: str | None = None,
    project_id: str | None = None,
    types: str | None = None,
    statuses: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> list[dict]:
    filters = service.AssignmentFilters(
        start=_parse_wire(start),
        end=_parse_wire(end),
        employee_ids=_split_ids(employee_ids),
        department_id=_opt_id(department_id),
        position=_opt_str(position),
        project_id=_opt_id(project_id),
        types=[AssignmentType(t) for t in _split_str(types) or []] or None,
        statuses=[AssignmentStatus(s) for s in _split_str(statuses) or []] or None,
    )
    return [_assignment_dict(a) for a in service.list_assignments(db, filters)]


@router.get("/api/free")
def api_free(
    start: str,
    end: str,
    department_id: str | None = None,
    position: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> list[dict]:
    filters = service.EmployeeFilters(
        query=_opt_str(q), department_id=_opt_id(department_id), position=_opt_str(position)
    )
    free = service.free_employees(db, _parse_wire(start), _parse_wire(end), filters)
    return [_employee_dict(e) for e in free]


@router.get("/api/conflicts")
def api_conflicts(
    employee_id: int,
    start: str,
    end: str,
    exclude_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> dict:
    conflicts = service.find_conflicts(
        db, employee_id, _parse_wire(start), _parse_wire(end), exclude_id=exclude_id
    )
    return {"conflicts": [_assignment_dict(c) for c in conflicts]}


def _assignment_form_data(
    employee_id: int,
    project_id: str | None,
    type: str,
    status: str,
    starts_at: str,
    ends_at: str,
    title: str | None,
    comment: str | None,
) -> AssignmentInput:
    return AssignmentInput(
        employee_id=employee_id,
        project_id=_opt_id(project_id),
        type=AssignmentType(type),
        status=AssignmentStatus(status),
        starts_at=_parse_wire(starts_at),
        ends_at=_parse_wire(ends_at),
        title=_opt_str(title),
        comment=_opt_str(comment),
    )


@router.post("/api/assignments", dependencies=[Depends(verify_csrf)])
def api_assignment_create(
    employee_id: int = Form(...),
    project_id: str | None = Form(None),
    type: str = Form(...),
    status: str = Form(AssignmentStatus.PLANNED.value),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    title: str | None = Form(None),
    comment: str | None = Form(None),
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = _assignment_form_data(
        employee_id, project_id, type, status, starts_at, ends_at, title, comment
    )
    try:
        created = service.create_assignment(
            db, data, created_by_id=user.id, confirm=confirm is not None
        )
    except service.ConflictError as exc:
        return JSONResponse(
            {"ok": False, "conflicts": [_assignment_dict(c) for c in exc.conflicts]},
            status_code=409,
        )
    audit_log(
        db,
        user,
        EventType.ASSIGNMENT_MANAGE,
        f"Создана занятость сотрудника #{created.employee_id}",
        object_type="staff_assignment",
        object_id=created.id,
    )
    return {"ok": True, "assignment": _assignment_dict(created)}


@router.post("/api/assignments/bulk", dependencies=[Depends(verify_csrf)])
def api_assignment_bulk(
    employee_ids: list[int] = Form(...),
    project_id: str | None = Form(None),
    type: str = Form(...),
    status: str = Form(AssignmentStatus.PLANNED.value),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    title: str | None = Form(None),
    comment: str | None = Form(None),
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = BulkAssignmentInput(
        employee_ids=employee_ids,
        project_id=_opt_id(project_id),
        type=AssignmentType(type),
        status=AssignmentStatus(status),
        starts_at=_parse_wire(starts_at),
        ends_at=_parse_wire(ends_at),
        title=_opt_str(title),
        comment=_opt_str(comment),
    )
    result = service.bulk_create_assignments(
        db, data, created_by_id=user.id, confirm=confirm is not None
    )
    if result.conflicts:
        return JSONResponse(
            {
                "ok": False,
                "conflicts": {
                    str(emp_id): [_assignment_dict(c) for c in conflicts]
                    for emp_id, conflicts in result.conflicts.items()
                },
            },
            status_code=409,
        )
    audit_log(
        db,
        user,
        EventType.ASSIGNMENT_MANAGE,
        f"Массовое назначение: {len(result.created)} сотрудник(ов)",
        object_type="staff_assignment",
        object_id=None,
    )
    return {"ok": True, "created": [_assignment_dict(a) for a in result.created]}


# Динамические маршруты /{assignment_id}* регистрируются после /bulk,
# иначе .../bulk перехватывается .../{assignment_id} (см. app/staff/router.py
# для того же паттерна с /staff/departments).


@router.post("/api/assignments/{assignment_id}", dependencies=[Depends(verify_csrf)])
def api_assignment_update(
    assignment_id: int,
    employee_id: int = Form(...),
    project_id: str | None = Form(None),
    type: str = Form(...),
    status: str = Form(AssignmentStatus.PLANNED.value),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    title: str | None = Form(None),
    comment: str | None = Form(None),
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    assignment = service.get_assignment(db, assignment_id)
    if assignment is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    data = _assignment_form_data(
        employee_id, project_id, type, status, starts_at, ends_at, title, comment
    )
    try:
        updated = service.update_assignment(db, assignment, data, confirm=confirm is not None)
    except service.ConflictError as exc:
        return JSONResponse(
            {"ok": False, "conflicts": [_assignment_dict(c) for c in exc.conflicts]},
            status_code=409,
        )
    audit_log(
        db,
        user,
        EventType.ASSIGNMENT_MANAGE,
        f"Изменена занятость сотрудника #{updated.employee_id}",
        object_type="staff_assignment",
        object_id=updated.id,
    )
    return {"ok": True, "assignment": _assignment_dict(updated)}


@router.post("/api/assignments/{assignment_id}/delete", dependencies=[Depends(verify_csrf)])
def api_assignment_delete(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    assignment = service.get_assignment(db, assignment_id)
    if assignment is not None:
        employee_id = assignment.employee_id
        service.delete_assignment(db, assignment)
        audit_log(
            db,
            user,
            EventType.ASSIGNMENT_MANAGE,
            f"Удалена занятость сотрудника #{employee_id}",
            object_type="staff_assignment",
            object_id=assignment_id,
        )
    return {"ok": True}
