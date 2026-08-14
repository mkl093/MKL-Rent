"""Справочник персонала: сотрудники и отделы (ТЗ §54)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth import service as auth_service
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.staff import service
from app.staff.schemas import DepartmentInput, EmployeeInput
from app.templating import flash

router = APIRouter(prefix="/staff", tags=["staff"])


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# --- Сотрудники --------------------------------------------------------------


@router.get("")
def employees_page(
    request: Request,
    q: str | None = None,
    department_id: str | None = None,
    position: str | None = None,
    show: str = "active",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    filters = service.EmployeeFilters(
        query=_opt_str(q),
        department_id=_opt_id(department_id),
        position=_opt_str(position),
        is_active=None if show == "all" else (show != "inactive"),
    )
    departments = service.list_departments(db)
    employees = service.list_employees(db, filters)
    return render(
        request,
        "staff/list.html",
        {
            "page_title": "Персонал",
            "groups": service.group_employees_by_department(departments, employees),
            "departments": departments,
            "q": q or "",
            "department_id": filters.department_id,
            "position": position or "",
            "show": show,
        },
        db=db,
        user=user,
    )


@router.get("/new")
def employee_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "staff/form.html",
        {
            "page_title": "Новый сотрудник",
            "employee": None,
            "departments": service.list_departments(db),
            "users": auth_service.list_users(db),
        },
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def employee_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    position: str | None = Form(None),
    department_id: str | None = Form(None),
    user_id: str | None = Form(None),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    is_active: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = EmployeeInput(
        first_name=first_name,
        last_name=last_name,
        position=_opt_str(position),
        department_id=_opt_id(department_id),
        user_id=_opt_id(user_id),
        phone=_opt_str(phone),
        email=_opt_str(email),
        is_active=is_active is not None,
        comment=_opt_str(comment),
    )
    created = service.create_employee(db, data)
    audit_log(
        db,
        user,
        EventType.STAFF_MANAGE,
        f"Добавлен сотрудник «{created.full_name}»",
        object_type="employee",
        object_id=created.id,
    )
    flash(request, "Сотрудник добавлен.", "success")
    return redirect("/staff")


@router.get("/departments")
def departments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "staff/departments.html",
        {"page_title": "Отделы", "departments": service.list_departments(db)},
        db=db,
        user=user,
    )


@router.post("/departments", dependencies=[Depends(verify_csrf)])
def department_create(
    request: Request,
    name: str = Form(...),
    sort_order: str | None = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if _opt_str(name):
        created = service.create_department(
            db, DepartmentInput(name=name, sort_order=int(sort_order or 0))
        )
        audit_log(
            db,
            user,
            EventType.STAFF_MANAGE,
            f"Добавлен отдел «{created.name}»",
            object_type="department",
            object_id=created.id,
        )
        flash(request, "Отдел добавлен.", "success")
    return redirect("/staff/departments")


@router.post("/departments/{department_id}", dependencies=[Depends(verify_csrf)])
def department_update(
    request: Request,
    department_id: int,
    name: str = Form(...),
    sort_order: str | None = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    dept = service.get_department(db, department_id)
    if dept is not None and _opt_str(name):
        data = DepartmentInput(name=name, sort_order=int(sort_order or 0))
        service.update_department(db, dept, data)
        flash(request, "Отдел изменён.", "success")
    return redirect("/staff/departments")


@router.post("/departments/{department_id}/delete", dependencies=[Depends(verify_csrf)])
def department_delete(
    request: Request,
    department_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    dept = service.get_department(db, department_id)
    if dept is not None:
        service.delete_department(db, dept)
        flash(request, "Отдел удалён.", "success")
    return redirect("/staff/departments")


# --- Сотрудники (динамические маршруты — регистрируются после /departments,
# иначе /staff/departments перехватывается /staff/{employee_id}) -----------


@router.get("/{employee_id}/edit")
def employee_edit(
    request: Request,
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    employee = service.get_employee(db, employee_id)
    if employee is None:
        return redirect("/staff")
    return render(
        request,
        "staff/form.html",
        {
            "page_title": f"Сотрудник — {employee.full_name}",
            "employee": employee,
            "departments": service.list_departments(db),
            "users": auth_service.list_users(db),
        },
        db=db,
        user=user,
    )


@router.post("/{employee_id}", dependencies=[Depends(verify_csrf)])
def employee_update(
    request: Request,
    employee_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    position: str | None = Form(None),
    department_id: str | None = Form(None),
    user_id: str | None = Form(None),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    is_active: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    employee = service.get_employee(db, employee_id)
    if employee is None:
        return redirect("/staff")
    data = EmployeeInput(
        first_name=first_name,
        last_name=last_name,
        position=_opt_str(position),
        department_id=_opt_id(department_id),
        user_id=_opt_id(user_id),
        phone=_opt_str(phone),
        email=_opt_str(email),
        is_active=is_active is not None,
        comment=_opt_str(comment),
    )
    service.update_employee(db, employee, data)
    audit_log(
        db,
        user,
        EventType.STAFF_MANAGE,
        f"Изменён сотрудник «{employee.full_name}»",
        object_type="employee",
        object_id=employee.id,
    )
    flash(request, "Изменения сохранены.", "success")
    return redirect("/staff")


@router.post("/{employee_id}/delete", dependencies=[Depends(verify_csrf)])
def employee_delete(
    request: Request,
    employee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    employee = service.get_employee(db, employee_id)
    if employee is not None:
        name = employee.full_name
        service.delete_employee(db, employee)
        audit_log(
            db,
            user,
            EventType.STAFF_MANAGE,
            f"Удалён сотрудник «{name}»",
            object_type="employee",
            object_id=employee_id,
        )
        flash(request, "Сотрудник удалён.", "success")
    return redirect("/staff")
