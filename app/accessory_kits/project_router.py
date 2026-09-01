"""Комплекты аксессуаров проекта: содержимое, вес, добавление в смету."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.accessory_kits import service
from app.accessory_kits.schemas import (
    AccessoryKitInput,
    CustomAccessoryKitLine,
)
from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.enums import KitWeightMode
from app.inventory.services import equipment as eq_service
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/accessory-kits", tags=["accessory_kits"])


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _opt_dec(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    return _dec(value)


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _load(db: Session, project_id: int):
    project = get_project(db, project_id)
    if project is None:
        return None, False
    return project, not project.is_archived


def _kit_input(
    name: str, barcode: str | None, weight_mode: str | None, weight_value: str | None,
    length_mm: str | None, width_mm: str | None, height_mm: str | None, comment: str | None,
) -> AccessoryKitInput:
    try:
        mode = KitWeightMode(weight_mode or "")
    except ValueError:
        mode = KitWeightMode.CONTENT
    return AccessoryKitInput(
        name=name,
        barcode=barcode,
        weight_mode=mode,
        weight_value=_opt_dec(weight_value),
        length_mm=_int(length_mm),
        width_mm=_int(width_mm),
        height_mm=_int(height_mm),
        comment=comment,
    )


@router.get("")
def list_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")
    return render(
        request,
        "accessory_kits/list.html",
        {
            "page_title": "Комплекты аксессуаров",
            "project": project,
            "editable": editable,
            "summaries": service.summaries(db, project),
            "templates": service.list_templates(db),
        },
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def create(
    request: Request,
    project_id: int,
    name: str = Form(...),
    template_id: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    if not name.strip():
        flash(request, "Укажите название комплекта.", "warning")
        return redirect(f"/projects/{project_id}/accessory-kits")

    tpl_id = _opt_id(template_id)
    if tpl_id:
        template = service.get_template(db, tpl_id)
        if template is None:
            flash(request, "Шаблон не найден.", "warning")
            return redirect(f"/projects/{project_id}/accessory-kits")
        kit = service.create_from_template(db, project, template, name=name.strip())
    else:
        kit = service.create_kit(db, project, AccessoryKitInput(name=name.strip()))

    audit_log(
        db, user, EventType.ACCESSORY_KIT_MANAGE,
        f"Проект {project.number}: создан комплект аксессуаров «{kit.name}»",
        object_type="accessory_kit", object_id=kit.id,
    )
    flash(request, f"Комплект аксессуаров «{kit.name}» создан.", "success")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit.id}")


@router.get("/{kit_id}")
def detail(
    request: Request,
    project_id: int,
    kit_id: int,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        flash(request, "Комплект аксессуаров не найден.", "warning")
        return redirect(f"/projects/{project_id}/accessory-kits")

    models = []
    if q:
        filters = eq_service.ModelFilters(query=q, archived=False)
        models = eq_service.list_models(db, filters)[:20]

    from app.packing import service as packing_service

    packing = packing_service.get_packing(db, project)
    in_packing = packing is not None and any(ln.accessory_kit_id == kit.id for ln in packing.lines)

    return render(
        request,
        "accessory_kits/detail.html",
        {
            "page_title": kit.name,
            "project": project,
            "kit": kit,
            "editable": editable,
            "content_weight": service.content_weight(kit),
            "total_weight": service.total_weight(kit),
            "in_estimate": service.is_in_estimate(db, kit.id),
            "packing_exists": packing is not None,
            "in_packing": in_packing,
            "models": models,
            "q": q or "",
            "KitWeightMode": KitWeightMode,
        },
        db=db,
        user=user,
    )


@router.post("/{kit_id}", dependencies=[Depends(verify_csrf)])
def update(
    request: Request,
    project_id: int,
    kit_id: int,
    name: str = Form(...),
    barcode: str | None = Form(None),
    weight_mode: str | None = Form(None),
    weight_value: str | None = Form(None),
    length_mm: str | None = Form(None),
    width_mm: str | None = Form(None),
    height_mm: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None or not name.strip():
        return redirect(f"/projects/{project_id}/accessory-kits")
    try:
        service.update_kit(
            db, project, kit,
            _kit_input(name, barcode, weight_mode, weight_value, length_mm, width_mm, height_mm, comment),
        )
        flash(request, "Комплект аксессуаров обновлён.", "success")
    except service.DuplicateBarcode as exc:
        flash(request, str(exc), "danger")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/delete", dependencies=[Depends(verify_csrf)])
def delete(
    request: Request,
    project_id: int,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        return redirect(f"/projects/{project_id}/accessory-kits")
    try:
        name = kit.name
        service.delete_kit(db, project, kit)
        audit_log(
            db, user, EventType.ACCESSORY_KIT_MANAGE,
            f"Проект {project.number}: удалён комплект аксессуаров «{name}»",
            object_type="accessory_kit", object_id=kit_id,
        )
        flash(request, f"Комплект аксессуаров «{name}» удалён.", "info")
        return redirect(f"/projects/{project_id}/accessory-kits")
    except service.InUse as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/move", dependencies=[Depends(verify_csrf)])
def move(
    request: Request,
    project_id: int,
    kit_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is not None:
        service.move_kit(db, project, kit, -1 if direction == "up" else 1)
    return redirect(f"/projects/{project_id}/accessory-kits")


@router.post("/{kit_id}/add-to-estimate", dependencies=[Depends(verify_csrf)])
def add_to_estimate(
    request: Request,
    project_id: int,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    from app.estimates.service import add_accessory_kit_line, get_or_create_estimate

    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        return redirect(f"/projects/{project_id}/accessory-kits")
    estimate = get_or_create_estimate(db, project)
    line = add_accessory_kit_line(db, estimate, project, kit)
    if line is not None:
        audit_log(
            db, user, EventType.ESTIMATE_CHANGE,
            f"Смета {estimate.number}: добавлен комплект аксессуаров «{kit.name}»",
            object_type="estimate", object_id=estimate.id,
        )
        flash(request, f"«{kit.name}» добавлен в смету.", "success")
    else:
        flash(request, f"«{kit.name}» уже в смете.", "info")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/add-to-packing", dependencies=[Depends(verify_csrf)])
def add_to_packing(
    request: Request,
    project_id: int,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Добавить комплект аксессуаров сразу в packing-лист, минуя смету."""
    from app.packing import service as packing_service

    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        return redirect(f"/projects/{project_id}/accessory-kits")
    packing = packing_service.get_packing(db, project)
    if packing is None:
        flash(request, "Сначала создайте packing-лист проекта.", "warning")
        return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")

    line = packing_service.add_accessory_kit(db, packing, kit)
    if line is not None:
        audit_log(
            db, user, EventType.PACKING_ADD,
            f"Packing {packing.number}: добавлен комплект аксессуаров «{kit.name}» напрямую",
            object_type="packing_list", object_id=packing.id,
        )
        flash(request, f"«{kit.name}» добавлен в packing-лист.", "success")
    else:
        flash(request, f"«{kit.name}» уже в packing-листе.", "info")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


# --- Содержимое --------------------------------------------------------------


@router.post("/{kit_id}/add", dependencies=[Depends(verify_csrf)])
async def add_stock(
    request: Request,
    project_id: int,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        return redirect(f"/projects/{project_id}/accessory-kits")

    form = await request.form()
    added = 0
    for key in form:
        if key.startswith("select_"):
            model_id = int(key.split("_", 1)[1])
            model = eq_service.get_model(db, model_id)
            if model is None:
                continue
            qty = _int(form.get(f"qty_{model_id}"), 1)
            service.add_model_line(db, project, kit, model, qty)
            added += 1
    if added:
        audit_log(
            db, user, EventType.ACCESSORY_KIT_MANAGE,
            f"Комплект аксессуаров «{kit.name}»: добавлено позиций со склада — {added}",
            object_type="accessory_kit", object_id=kit.id,
        )
        flash(request, f"Добавлено позиций: {added}.", "success")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/custom", dependencies=[Depends(verify_csrf)])
def add_custom(
    request: Request,
    project_id: int,
    kit_id: int,
    name: str = Form(...),
    quantity: str = Form("1"),
    unit_weight_kg: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/accessory-kits")
    kit = service.get_kit(db, project, kit_id)
    if kit is None or not name.strip():
        return redirect(f"/projects/{project_id}/accessory-kits")
    service.add_custom_line(
        db, project, kit,
        CustomAccessoryKitLine(
            name=name, quantity=_int(quantity, 1), unit_weight_kg=_dec(unit_weight_kg), comment=comment
        ),
    )
    flash(request, "Позиция добавлена.", "success")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


def _kit_and_line(db, project_id, kit_id, line_id):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return None, None, None
    kit = service.get_kit(db, project, kit_id)
    if kit is None:
        return project, None, None
    line = next((ln for ln in kit.lines if ln.id == line_id), None)
    return project, kit, line


@router.post("/{kit_id}/lines/{line_id}", dependencies=[Depends(verify_csrf)])
def update_line(
    request: Request,
    project_id: int,
    kit_id: int,
    line_id: int,
    quantity: str = Form("1"),
    comment: str | None = Form(None),
    name: str | None = Form(None),
    unit_weight_kg: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, kit, line = _kit_and_line(db, project_id, kit_id, line_id)
    if kit is not None and line is not None:
        service.update_line(
            db, project, kit, line,
            quantity=_int(quantity, 1), comment=comment, name=name,
            unit_weight_kg=_opt_dec(unit_weight_kg),
        )
        flash(request, "Позиция обновлена.", "success")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/lines/{line_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_line(
    request: Request,
    project_id: int,
    kit_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, kit, line = _kit_and_line(db, project_id, kit_id, line_id)
    if kit is not None and line is not None:
        service.delete_line(db, project, kit, line)
        flash(request, "Позиция удалена.", "info")
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")


@router.post("/{kit_id}/lines/{line_id}/move", dependencies=[Depends(verify_csrf)])
def move_line(
    request: Request,
    project_id: int,
    kit_id: int,
    line_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, kit, line = _kit_and_line(db, project_id, kit_id, line_id)
    if kit is not None and line is not None:
        service.move_line(db, kit, line, -1 if direction == "up" else 1)
    return redirect(f"/projects/{project_id}/accessory-kits/{kit_id}")
