"""Маршруты раздела «Проекты» (ТЗ §13–§15)."""

from __future__ import annotations

import calendar as pycalendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db, utcnow
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.models import EquipmentModel
from app.projects import service
from app.projects.availability import compute_availability, occupancy_detail
from app.projects.enums import PROJECT_COLOR_PRESETS, ProjectStatus
from app.projects.schemas import ProjectInput
from app.settings.service import get_company_settings
from app.staff import service as staff_service
from app.templating import flash
from app.utils.timezone import to_local

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


def _color(value: str | None) -> str | None:
    value = _str(value)
    if value is None:
        return None
    value = value.lower()
    return value if re.fullmatch(r"#[0-9a-f]{6}", value) else None


def _today() -> date:
    return to_local(utcnow()).date()


_CALENDAR_SPANS = (14, 31, 92, 366)
_CALENDAR_VIEWS = ("gantt", "grid")

_MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

# Родительный падеж для подписи 1-го числа при переходе между месяцами внутри
# сетки («1 сент.») — тот же список, что MONTH_SHORT в staff-calendar.js.
_MONTH_SHORT = [
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.",
]


def _month_segments(days: list[date]) -> list[dict]:
    """Заголовок месяцев для календаря проектов: label + colspan (ТЗ §13.9)."""
    segments: list[dict] = []
    for d in days:
        if segments and (segments[-1]["year"], segments[-1]["month"]) == (d.year, d.month):
            segments[-1]["span"] += 1
        else:
            segments.append({"year": d.year, "month": d.month, "span": 1})
    for seg in segments:
        seg["label"] = f"{_MONTH_NAMES[seg['month'] - 1]} {seg['year']}"
    return segments


def _month_grid_range(month_start: date) -> tuple[date, date]:
    """Недели Пн–Вс, целиком захватывающие месяц (сетка календаря проектов, ТЗ §13.9)."""
    days_in_month = pycalendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start + timedelta(days=days_in_month - 1)
    grid_start = month_start - timedelta(days=month_start.weekday())
    grid_last_week_start = month_end - timedelta(days=month_end.weekday())
    grid_end = grid_last_week_start + timedelta(days=6)
    return grid_start, grid_end


def _dates_label(shipped: date | None, returned: date | None) -> str:
    fmt = lambda d: d.strftime("%d.%m.%Y") if d else "—"  # noqa: E731
    return f"отгрузка {fmt(shipped)}, возврат {fmt(returned)}"


def _input(
    name: str,
    start_date: str | None,
    end_date: str | None,
    shipped_date: str | None,
    returned_date: str | None,
    rental_coefficient: str | None,
    vat: str | None,
    customer: str | None,
    address: str | None,
    comment: str | None,
    color: str | None,
    calendar_bar: str | None,
) -> ProjectInput:
    return ProjectInput(
        name=name,
        start_date=_date(start_date),
        end_date=_date(end_date),
        shipped_date=_date(shipped_date),
        returned_date=_date(returned_date),
        rental_coefficient=_dec(rental_coefficient, "1"),
        vat=_dec(vat, "0"),
        customer=_str(customer),
        address=_str(address),
        comment=_str(comment),
        color=_color(color),
        calendar_bar=calendar_bar is not None,
    )


@router.get("")
def index(
    request: Request,
    archived: int = 0,
    filter: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    status_filter = filter if filter in ("active", "overdue", "deficit") else None
    titles = {
        "active": "Активные брони",
        "overdue": "Просроченные брони",
        "deficit": "Проекты с дефицитом",
    }
    return render(
        request,
        "projects/list.html",
        {
            "page_title": titles.get(status_filter, "Проекты"),
            "projects": service.list_projects(
                db, archived=bool(archived), status_filter=status_filter
            ),
            "archived": bool(archived),
            "status_filter": status_filter,
            "status_filter_title": titles.get(status_filter),
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
        {
            "page_title": "Новый проект",
            "project": None,
            "default_vat": company.default_vat,
            "color_presets": PROJECT_COLOR_PRESETS,
        },
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def project_create(
    request: Request,
    name: str = Form(...),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    shipped_date: str | None = Form(None),
    returned_date: str | None = Form(None),
    rental_coefficient: str | None = Form("1"),
    vat: str | None = Form("0"),
    customer: str | None = Form(None),
    address: str | None = Form(None),
    comment: str | None = Form(None),
    color: str | None = Form(None),
    calendar_bar: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = _input(
        name,
        start_date,
        end_date,
        shipped_date,
        returned_date,
        rental_coefficient,
        vat,
        customer,
        address,
        comment,
        color,
        calendar_bar,
    )
    try:
        project = service.create_project(db, data)
    except service.ValidationError as exc:
        flash(request, str(exc), "danger")
        return redirect("/projects/new")
    audit_log(
        db,
        user,
        EventType.PROJECT_CREATE,
        f"Создан проект {project.number} «{project.name}»",
        object_type="project",
        object_id=project.id,
    )
    flash(request, f"Проект {project.number} создан.", "success")
    return redirect(f"/projects/{project.id}")


# Статический путь — обязательно выше /{project_id}, иначе "calendar" уйдёт в
# динамический параметр и упадёт с 422.
@router.get("/calendar")
def projects_calendar(
    request: Request,
    q: str | None = None,
    archived: int = 0,
    start: str | None = None,
    span: int = 31,
    view: str = "gantt",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if view not in _CALENDAR_VIEWS:
        view = "gantt"
    if span not in _CALENDAR_SPANS:
        span = 31
    anchor = _date(start) or _today()
    today = _today()
    is_archived = bool(archived)
    common_ctx = {
        "page_title": "Календарь проектов",
        "view": view,
        "anchor": anchor,
        "span": span,
        "spans": _CALENDAR_SPANS,
        "without_dates": service.list_projects_without_dates(db, archived=is_archived),
        "today": today,
        "q": q or "",
        "archived": is_archived,
    }

    if view == "grid":
        month_start = anchor.replace(day=1)
        grid_start, grid_end = _month_grid_range(month_start)
        days, rows, _load = service.compute_project_timeline(
            db, grid_start, grid_end, q=_str(q), archived=is_archived
        )
        weeks = service.build_project_grid(days, rows, month_start)
        prev_start = (month_start - timedelta(days=1)).replace(day=1)
        next_start = month_start + timedelta(days=pycalendar.monthrange(month_start.year, month_start.month)[1])

        return render(
            request,
            "projects/calendar.html",
            {
                **common_ctx,
                "weeks": weeks,
                "month_label": f"{_MONTH_NAMES[month_start.month - 1]} {month_start.year}",
                "is_current_month": month_start.year == today.year and month_start.month == today.month,
                "month_short": _MONTH_SHORT,
                "prev_start": prev_start,
                "next_start": next_start,
            },
            db=db,
            user=user,
        )

    start_date = anchor
    end_date = start_date + timedelta(days=span - 1)
    days, rows, load = service.compute_project_timeline(
        db, start_date, end_date, q=_str(q), archived=is_archived
    )

    return render(
        request,
        "projects/calendar.html",
        {
            **common_ctx,
            "days": days,
            "month_segments": _month_segments(days),
            "rows": rows,
            "load": load,
            "start_date": start_date,
            "end_date": end_date,
            "prev_start": start_date - timedelta(days=span),
            "next_start": start_date + timedelta(days=span),
        },
        db=db,
        user=user,
    )


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

    # Запоминаем текущий проект для быстрого возврата (ТЗ §24).
    request.session["current_project"] = {
        "id": project.id,
        "number": project.number,
        "name": project.name,
    }

    rows = []
    if project.start_date and project.end_date:
        for res in project.reservations:
            # Бронь может быть на комплект (model_id пуст) — такие строки пропускаем,
            # db.get(..., None) иначе даёт SAWarning о NULL-идентичности.
            if res.model_id is None:
                continue
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
            "staff_assignments": staff_service.list_project_assignments(db, project.id),
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
            "color_presets": PROJECT_COLOR_PRESETS,
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
    shipped_date: str | None = Form(None),
    returned_date: str | None = Form(None),
    rental_coefficient: str | None = Form("1"),
    vat: str | None = Form("0"),
    customer: str | None = Form(None),
    address: str | None = Form(None),
    comment: str | None = Form(None),
    color: str | None = Form(None),
    calendar_bar: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = service.get_project(db, project_id)
    if project is None or project.is_archived:
        return redirect("/projects")
    was_booked = project.status == ProjectStatus.BOOKED
    old_dates = (project.shipped_date, project.returned_date)
    data = _input(
        name,
        start_date,
        end_date,
        shipped_date,
        returned_date,
        rental_coefficient,
        vat,
        customer,
        address,
        comment,
        color,
        calendar_bar,
    )
    try:
        service.update_project(db, project, data)
    except service.ValidationError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/projects/{project.id}/edit")
    audit_log(
        db,
        user,
        EventType.PROJECT_UPDATE,
        f"Изменён проект {project.number}",
        object_type="project",
        object_id=project.id,
    )
    if old_dates != (project.shipped_date, project.returned_date):
        audit_log(
            db,
            user,
            EventType.PROJECT_DATES,
            f"Даты отгрузки/возврата проекта {project.number}",
            object_type="project",
            object_id=project.id,
            old_value=_dates_label(*old_dates),
            new_value=_dates_label(project.shipped_date, project.returned_date),
        )
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
        audit_log(
            db,
            user,
            EventType.PROJECT_BOOK,
            f"Проект {project.number} забронирован"
            + (" (с подтверждением дефицита)" if allow_deficit else ""),
            object_type="project",
            object_id=project.id,
        )
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
    old_status = project.status
    old_dates = (project.shipped_date, project.returned_date)
    service.set_status(db, project, new_status)
    audit_log(
        db,
        user,
        EventType.PROJECT_STATUS,
        f"Статус проекта {project.number}",
        object_type="project",
        object_id=project.id,
        old_value=old_status.label,
        new_value=new_status.label,
    )
    if old_dates != (project.shipped_date, project.returned_date):
        audit_log(
            db,
            user,
            EventType.PROJECT_DATES,
            f"Даты отгрузки/возврата проекта {project.number} (авто)",
            object_type="project",
            object_id=project.id,
            old_value=_dates_label(*old_dates),
            new_value=_dates_label(project.shipped_date, project.returned_date),
        )
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
    audit_log(
        db,
        user,
        EventType.PROJECT_COPY,
        f"Проект {project.number} скопирован в {copy.number}",
        object_type="project",
        object_id=copy.id,
    )
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
        pid = project.id
        service.delete_project(db, project)
        audit_log(
            db,
            user,
            EventType.PROJECT_DELETE,
            f"Удалён проект {number}",
            object_type="project",
            object_id=pid,
        )
        flash(request, f"Проект {number} удалён.", "success")
        return redirect("/projects")
    except service.ValidationError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/projects/{project.id}")
