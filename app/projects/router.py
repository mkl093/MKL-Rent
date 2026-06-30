"""Маршруты раздела «Проекты» (ТЗ §13–§15)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.models import EquipmentModel
from app.projects import service
from app.projects.availability import compute_availability, occupancy_detail
from app.projects.enums import ProjectStatus
from app.projects.schemas import ProjectInput
from app.settings.service import get_company_settings
from app.templating import flash

router = APIRouter(prefix="/projects", tags=["projects"])


def _date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _dec(value: str | None, default: str) -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _input(
    name: str,
    start_date: str | None,
    end_date: str | None,
    rental_coefficient: str | None,
    vat: str | None,
    customer: str | None,
    address: str | None,
    comment: str | None,
) -> ProjectInput:
    return ProjectInput(
        name=name,
        start_date=_date(start_date),
        end_date=_date(end_date),
        rental_coefficient=_dec(rental_coefficient, "1"),
        vat=_dec(vat, "0"),
        customer=_str(customer),
        address=_str(address),
        comment=_str(comment),
    )


@router.get("")
def index(
    request: Request,
    archived: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "projects/list.html",
        {
            "page_title": "Проекты",
            "projects": service.list_projects(db, archived=bool(archived)),
            "archived": bool(archived),
            "ProjectStatus": ProjectStatus,
        },
        db=db,
        user=user,
    )


@router.get("/new")
def project_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    company = get_company_settings(db)
    return render(
        request,
        "projects/project_form.html",
        {"page_title": "Новый проект", "project": None, "default_vat": company.default_vat},
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def project_create(
    request: Request,
    name: str = Form(...),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    rental_coefficient: str | None = Form("1"),
    vat: str | None = Form("0"),
    customer: str | None = Form(None),
    address: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = _input(name, start_date, end_date, rental_coefficient, vat, customer, address, comment)
    project = service.create_project(db, data)
    flash(request, f"Проект {project.number} создан.", "success")
    return redirect(f"/projects/{project.id}")


@router.get("/{project_id}")
def project_detail(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")

    rows = []
    if project.start_date and project.end_date:
        for res in project.reservations:
            model = db.get(EquipmentModel, res.model_id)
            if model is None:
                continue
            avail = compute_availability(
                db,
                model,
                project.start_date,
                project.end_date,
                required=res.quantity,
                exclude_project_id=project.id,
            )
            rows.append(
                {
                    "model": model,
                    "quantity": res.quantity,
                    "availability": avail,
                    "occupancy": occupancy_detail(
                        db, model.id, project.start_date, project.end_date, project.id
                    ),
                }
            )
    return render(
        request,
        "projects/project_detail.html",
        {
            "page_title": project.number,
            "project": project,
            "rows": rows,
            "ProjectStatus": ProjectStatus,
        },
        db=db,
        user=user,
    )


@router.get("/{project_id}/edit")
def project_edit(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    if project.is_archived:
        flash(
            request,
            "Архивный проект только для просмотра. Верните его в «Черновик» для правок.",
            "info",
        )
        return redirect(f"/projects/{project.id}")
    return render(
        request,
        "projects/project_form.html",
        {
            "page_title": f"Редактирование {project.number}",
            "project": project,
            "default_vat": project.vat,
        },
        db=db,
        user=user,
    )


@router.post("/{project_id}", dependencies=[Depends(verify_csrf)])
def project_update(
    request: Request,
    project_id: int,
    name: str = Form(...),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    rental_coefficient: str | None = Form("1"),
    vat: str | None = Form("0"),
    customer: str | None = Form(None),
    address: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None or project.is_archived:
        return redirect("/projects")
    was_booked = project.status == ProjectStatus.BOOKED
    data = _input(name, start_date, end_date, rental_coefficient, vat, customer, address, comment)
    service.update_project(db, project, data)
    # Изменение дат брони пересчитывает доступность (ТЗ §13.5).
    if was_booked:
        deficits = service.project_deficits(db, project)
        if deficits:
            flash(
                request,
                "Внимание: после изменения возник дефицит. Проверьте доступность.",
                "warning",
            )
        else:
            flash(request, "Проект сохранён, доступность пересчитана.", "success")
    else:
        flash(request, "Проект сохранён.", "success")
    return redirect(f"/projects/{project.id}")


@router.post("/{project_id}/book", dependencies=[Depends(verify_csrf)])
def project_book(
    request: Request,
    project_id: int,
    allow_deficit: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    try:
        service.book_project(db, project, allow_deficit=bool(allow_deficit))
        flash(request, "Проект забронирован.", "success")
    except service.DeficitError:
        flash(
            request, "Есть дефицит оборудования — подтвердите бронирование с дефицитом.", "danger"
        )
    except service.ValidationError as exc:
        flash(request, str(exc), "danger")
    return redirect(f"/projects/{project.id}")


@router.post("/{project_id}/status", dependencies=[Depends(verify_csrf)])
def project_status(
    request: Request,
    project_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    try:
        new_status = ProjectStatus(status)
    except ValueError:
        return redirect(f"/projects/{project.id}")
    service.set_status(db, project, new_status)
    flash(request, f"Статус: {new_status.label}.", "info")
    return redirect(f"/projects/{project.id}")


@router.post("/{project_id}/copy", dependencies=[Depends(verify_csrf)])
def project_copy(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    copy = service.copy_project(db, project)
    # Копируется и смета (ТЗ §13.8).
    from app.estimates.service import copy_estimate

    copy_estimate(db, project, copy)
    flash(request, f"Создана копия {copy.number} (черновик, без дат).", "success")
    return redirect(f"/projects/{copy.id}")


@router.post("/{project_id}/delete", dependencies=[Depends(verify_csrf)])
def project_delete(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    try:
        number = project.number
        service.delete_project(db, project)
        flash(request, f"Проект {number} удалён.", "success")
        return redirect("/projects")
    except service.ValidationError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/projects/{project.id}")
