"""Маршруты сметы внутри проекта (ТЗ §16)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.estimates import service
from app.estimates.schemas import CustomLineInput, LineUpdate
from app.inventory.enums import AccountingType
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.availability import compute_availability
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/estimate", tags=["estimates"])


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _int(value: str | None, default: int = 1) -> int:
    try:
        return max(default, int(float((value or "").replace(",", ".").strip())))
    except (ValueError, AttributeError):
        return default


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _load(db: Session, project_id: int, *, require_editable: bool = False):
    project = get_project(db, project_id)
    if project is None:
        return None, None
    if require_editable and project.is_archived:
        return project, None
    estimate = service.get_or_create_estimate(db, project)
    return project, estimate


@router.get("")
def estimate_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")
    return render(
        request,
        "estimates/estimate.html",
        {
            "page_title": f"Смета {estimate.number}",
            "project": project,
            "estimate": estimate,
            "groups": service.grouped_lines(estimate),
            "totals": service.totals(db, estimate, project),
        },
        db=db,
        user=user,
    )


@router.get("/add")
def add_picker(
    request: Request,
    project_id: int,
    q: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id)
    if project is None:
        return redirect("/projects")

    filters = eq_service.ModelFilters(
        query=q,
        category_id=_opt_id(category_id),
        subcategory_id=_opt_id(subcategory_id),
        archived=False,
    )
    models = eq_service.list_models(db, filters)

    availability = {}
    if project.start_date and project.end_date:
        for m in models:
            availability[m.id] = compute_availability(
                db, m, project.start_date, project.end_date, exclude_project_id=project.id
            )

    return render(
        request,
        "estimates/add.html",
        {
            "page_title": "Добавить оборудование",
            "project": project,
            "estimate": estimate,
            "models": models,
            "availability": availability,
            "categories": cat_service.list_categories(db),
            "filters": filters,
            "q": q or "",
            "AccountingType": AccountingType,
        },
        db=db,
        user=user,
    )


@router.post("/add", dependencies=[Depends(verify_csrf)])
async def add_submit(
    request: Request,
    project_id: int,
    mode: str = Form("merge"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None:
        return redirect("/projects")
    if estimate is None:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(f"/projects/{project_id}/estimate")

    form = await request.form()
    merge = mode != "separate"
    added = 0
    for key in form:
        if not key.startswith("select_"):
            continue
        model_id = int(key.split("_", 1)[1])
        model = eq_service.get_model(db, model_id)
        if model is None:
            continue
        qty = _int(form.get(f"qty_{model_id}"), 1)
        service.add_model(db, estimate, project, model, qty, merge=merge)
        added += 1
    flash(request, f"Добавлено позиций: {added}.", "success" if added else "warning")
    return redirect(f"/projects/{project_id}/estimate")


@router.post("/custom", dependencies=[Depends(verify_csrf)])
def add_custom(
    request: Request,
    project_id: int,
    name: str = Form(...),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    coefficient: str = Form("1"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None or estimate is None:
        return redirect(f"/projects/{project_id}/estimate")
    service.add_custom_line(
        db,
        estimate,
        project,
        CustomLineInput(
            name=name,
            quantity=_int(quantity, 1),
            unit_price=_dec(unit_price),
            coefficient=_dec(coefficient, "1"),
            comment=(comment.strip() if comment else None),
        ),
    )
    flash(request, "Произвольная строка добавлена.", "success")
    return redirect(f"/projects/{project_id}/estimate")


@router.post("/lines/{line_id}", dependencies=[Depends(verify_csrf)])
def update_line(
    request: Request,
    project_id: int,
    line_id: int,
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    coefficient: str = Form("1"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None or estimate is None:
        return redirect(f"/projects/{project_id}/estimate")
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        service.update_line(
            db,
            line,
            LineUpdate(
                quantity=_int(quantity, 1),
                unit_price=_dec(unit_price),
                coefficient=_dec(coefficient, "1"),
                comment=(comment.strip() if comment else None),
            ),
        )
        if not line.is_custom:
            service.sync_reservations(db, project, estimate)
        flash(request, "Строка обновлена.", "success")
    return redirect(f"/projects/{project_id}/estimate")


@router.post("/lines/{line_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_line(
    request: Request,
    project_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None or estimate is None:
        return redirect(f"/projects/{project_id}/estimate")
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        was_warehouse = not line.is_custom
        service.delete_line(db, line)
        if was_warehouse:
            db.refresh(estimate)
            service.sync_reservations(db, project, estimate)
        flash(request, "Строка удалена.", "info")
    return redirect(f"/projects/{project_id}/estimate")


@router.post("/lines/{line_id}/move", dependencies=[Depends(verify_csrf)])
def move_line(
    request: Request,
    project_id: int,
    line_id: int,
    direction: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None or estimate is None:
        return redirect(f"/projects/{project_id}/estimate")
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        service.move_line(db, estimate, line, 1 if direction > 0 else -1)
    return redirect(f"/projects/{project_id}/estimate")


@router.post("/discount", dependencies=[Depends(verify_csrf)])
def set_discount(
    request: Request,
    project_id: int,
    discount_percent: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, estimate = _load(db, project_id, require_editable=True)
    if project is None or estimate is None:
        return redirect(f"/projects/{project_id}/estimate")
    service.set_discount(db, estimate, _dec(discount_percent))
    flash(request, "Скидка обновлена.", "success")
    return redirect(f"/projects/{project_id}/estimate")
