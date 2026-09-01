"""Справочник шаблонов комплекта аксессуаров (типовые сетапы, переиспользуемые между проектами)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.accessory_kits import service
from app.accessory_kits.schemas import (
    AccessoryKitTemplateInput,
    TemplateCustomLine,
)
from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.services import equipment as eq_service
from app.templating import flash

router = APIRouter(prefix="/accessory-kit-templates", tags=["accessory_kit_templates"])


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


@router.get("")
def list_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "accessory_kits/templates_list.html",
        {"page_title": "Шаблоны комплектов аксессуаров", "templates": service.list_templates(db)},
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def create(
    request: Request,
    name: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if not name.strip():
        flash(request, "Укажите название шаблона.", "warning")
        return redirect("/accessory-kit-templates")
    template = service.create_template(db, AccessoryKitTemplateInput(name=name, comment=comment))
    audit_log(
        db, user, EventType.ACCESSORY_KIT_MANAGE,
        f"Создан шаблон комплекта аксессуаров «{template.name}»",
        object_type="accessory_kit_template", object_id=template.id,
    )
    return redirect(f"/accessory-kit-templates/{template.id}")


@router.get("/{template_id}")
def detail(
    request: Request,
    template_id: int,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is None:
        flash(request, "Шаблон не найден.", "warning")
        return redirect("/accessory-kit-templates")
    models = []
    if q:
        filters = eq_service.ModelFilters(query=q, archived=False)
        models = eq_service.list_models(db, filters)[:20]
    return render(
        request,
        "accessory_kits/template_detail.html",
        {"page_title": template.name, "template": template, "models": models, "q": q or ""},
        db=db,
        user=user,
    )


@router.post("/{template_id}", dependencies=[Depends(verify_csrf)])
def update(
    request: Request,
    template_id: int,
    name: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is not None and name.strip():
        service.update_template(db, template, AccessoryKitTemplateInput(name=name, comment=comment))
        flash(request, "Шаблон обновлён.", "success")
    return redirect(f"/accessory-kit-templates/{template_id}")


@router.post("/{template_id}/delete", dependencies=[Depends(verify_csrf)])
def delete(
    request: Request,
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is not None:
        service.delete_template(db, template)
        audit_log(
            db, user, EventType.ACCESSORY_KIT_MANAGE,
            f"Удалён шаблон комплекта аксессуаров «{template.name}»",
            object_type="accessory_kit_template", object_id=template_id,
        )
        flash(request, "Шаблон удалён.", "info")
    return redirect("/accessory-kit-templates")


@router.post("/{template_id}/add", dependencies=[Depends(verify_csrf)])
async def add_stock(
    request: Request,
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is None:
        return redirect("/accessory-kit-templates")
    form = await request.form()
    added = 0
    for key in form:
        if key.startswith("select_"):
            model_id = int(key.split("_", 1)[1])
            model = eq_service.get_model(db, model_id)
            if model is None:
                continue
            qty = _int(form.get(f"qty_{model_id}"), 1)
            service.add_template_model_line(db, template, model, qty)
            added += 1
    if added:
        flash(request, f"Добавлено позиций: {added}.", "success")
    return redirect(f"/accessory-kit-templates/{template_id}")


@router.post("/{template_id}/custom", dependencies=[Depends(verify_csrf)])
def add_custom(
    request: Request,
    template_id: int,
    name: str = Form(...),
    quantity: str = Form("1"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is not None and name.strip():
        service.add_template_custom_line(
            db, template, TemplateCustomLine(name=name, quantity=_int(quantity, 1), comment=comment)
        )
        flash(request, "Позиция добавлена.", "success")
    return redirect(f"/accessory-kit-templates/{template_id}")


@router.post("/{template_id}/lines/{line_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_line(
    request: Request,
    template_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    template = service.get_template(db, template_id)
    if template is not None:
        line = next((ln for ln in template.lines if ln.id == line_id), None)
        if line is not None:
            service.delete_template_line(db, template, line)
    return redirect(f"/accessory-kit-templates/{template_id}")
