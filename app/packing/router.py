"""Маршруты packing-листа внутри проекта (ТЗ §17–§20)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.enums import ItemStatus
from app.inventory.models import EquipmentItem
from app.packing import service
from app.packing.calc import compute_line
from app.packing.enums import PackingStatus
from app.packing.schemas import CustomPackingLine
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/packing", tags=["packing"])


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


def _load(db: Session, project_id: int, *, require_editable: bool = False):
    project = get_project(db, project_id)
    if project is None:
        return None, None, False
    packing = service.get_packing(db, project)
    editable = not project.is_archived
    if require_editable and not editable:
        return project, packing, False
    return project, packing, editable


@router.get("")
def packing_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")

    if packing is None:
        return render(
            request,
            "packing/empty.html",
            {"page_title": "Packing-лист", "project": project, "editable": editable},
            db=db,
            user=user,
        )

    ordered = sorted(
        packing.lines,
        key=lambda ln: (
            ln.is_custom,
            ln.category_name or "",
            ln.subcategory_name or "",
            ln.sort_order,
            ln.id,
        ),
    )
    calcs = {ln.id: compute_line(ln) for ln in packing.lines}

    # Доступные экземпляры для ручного выбора серийных строк (ТЗ §17.7).
    available: dict[int, list[EquipmentItem]] = {}
    for ln in packing.lines:
        if ln.is_serial and ln.model_id:
            assigned = {si.item_id for si in ln.serial_items}
            items = (
                db.execute(
                    select(EquipmentItem).where(
                        EquipmentItem.model_id == ln.model_id,
                        EquipmentItem.status == ItemStatus.ACTIVE,
                    )
                )
                .scalars()
                .all()
            )
            available[ln.id] = [it for it in items if it.id not in assigned]

    return render(
        request,
        "packing/packing.html",
        {
            "page_title": f"Packing {packing.number}",
            "project": project,
            "packing": packing,
            "ordered": ordered,
            "calcs": calcs,
            "available": available,
            "totals": service.totals(db, packing),
            "discrepancies": service.discrepancies(db, project, packing),
            "undercomplete": service.is_undercomplete(packing),
            "editable": editable,
            "PackingStatus": PackingStatus,
        },
        db=db,
        user=user,
    )


@router.post("/create", dependencies=[Depends(verify_csrf)])
def packing_create(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, _, editable = _load(db, project_id, require_editable=True)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    try:
        packing = service.create_from_estimate(db, project)
        flash(request, f"Packing-лист {packing.number} создан.", "success")
    except service.AlreadyExists as exc:
        flash(request, str(exc), "warning")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/sync", dependencies=[Depends(verify_csrf)])
def packing_sync(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    service.apply_sync(db, project, packing)
    flash(request, "План синхронизирован со сметой.", "success")
    return redirect(f"/projects/{project_id}/packing")


def _line(db, project_id, line_id):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return None, None, None
    return packing, service.get_line(db, packing, line_id), project


@router.post("/lines/{line_id}/quantity", dependencies=[Depends(verify_csrf)])
def line_quantity(
    request: Request,
    project_id: int,
    line_id: int,
    fact_quantity: str = Form("0"),
    packed_quantity: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None and not line.is_serial:
        service.update_quantity_line(db, line, _int(fact_quantity), _int(packed_quantity), comment)
        flash(request, "Строка обновлена.", "success")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/lines/{line_id}/distribution", dependencies=[Depends(verify_csrf)])
def line_distribution(
    request: Request,
    project_id: int,
    line_id: int,
    packed_quantity: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None:
        service.set_distribution(db, line, _int(packed_quantity))
        flash(request, "Распределение по упаковке обновлено.", "success")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/lines/{line_id}/serial", dependencies=[Depends(verify_csrf)])
def line_serial_add(
    request: Request,
    project_id: int,
    line_id: int,
    barcode: str = Form(...),
    allow_over: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None and line.is_serial:
        result = service.add_serial_item(db, line, barcode, allow_over=bool(allow_over))
        messages = {
            service.SerialResult.OK: ("Экземпляр добавлен.", "success"),
            service.SerialResult.OVER_PLAN: ("Добавлено сверх плана.", "warning"),
            service.SerialResult.DUPLICATE: ("Экземпляр уже в листе.", "warning"),
            service.SerialResult.WRONG_MODEL: ("Штрих-код другой модели.", "danger"),
            service.SerialResult.BLOCKED: ("Экземпляр в ремонте или списан.", "danger"),
            service.SerialResult.NOT_FOUND: ("Штрих-код не найден.", "danger"),
        }
        if result == service.SerialResult.OVER_PLAN and not bool(allow_over):
            flash(request, "Сверх плана — подтвердите добавление.", "warning")
        else:
            msg, cat = messages[result]
            flash(request, msg, cat)
    return redirect(f"/projects/{project_id}/packing")


@router.post("/lines/{line_id}/serial/{si_id}/delete", dependencies=[Depends(verify_csrf)])
def line_serial_remove(
    request: Request,
    project_id: int,
    line_id: int,
    si_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None:
        service.remove_serial_item(db, line, si_id)
        flash(request, "Экземпляр убран.", "info")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/lines/{line_id}/move", dependencies=[Depends(verify_csrf)])
def line_move(
    request: Request,
    project_id: int,
    line_id: int,
    direction: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None:
        service.move_line(db, packing, line, 1 if direction > 0 else -1)
    return redirect(f"/projects/{project_id}/packing")


@router.post("/lines/{line_id}/delete", dependencies=[Depends(verify_csrf)])
def line_delete(
    request: Request,
    project_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None:
        service.delete_line(db, line)
        flash(request, "Строка удалена.", "info")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/custom", dependencies=[Depends(verify_csrf)])
def custom_add(
    request: Request,
    project_id: int,
    name: str = Form(...),
    quantity: str = Form("1"),
    unit_weight_kg: str = Form("0"),
    length_mm: str = Form("0"),
    width_mm: str = Form("0"),
    height_mm: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    service.add_custom_line(
        db,
        packing,
        CustomPackingLine(
            name=name,
            quantity=max(1, _int(quantity, 1)),
            unit_weight_kg=_dec(unit_weight_kg),
            length_mm=_int(length_mm),
            width_mm=_int(width_mm),
            height_mm=_int(height_mm),
            comment=(comment.strip() if comment else None),
        ),
    )
    flash(request, "Дополнительная позиция добавлена.", "success")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/status", dependencies=[Depends(verify_csrf)])
def packing_status(
    request: Request,
    project_id: int,
    status: str = Form(...),
    shortage_comment: str | None = Form(None),
    confirm_undercomplete: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    try:
        new_status = PackingStatus(status)
    except ValueError:
        return redirect(f"/projects/{project_id}/packing")
    try:
        service.set_status(
            db,
            packing,
            new_status,
            shortage_comment=shortage_comment,
            confirm_undercomplete=bool(confirm_undercomplete),
        )
        flash(request, f"Статус: {new_status.label}.", "info")
    except service.UndercompleteError:
        flash(request, "Недокомплект: подтвердите и укажите причину.", "danger")
    return redirect(f"/projects/{project_id}/packing")
