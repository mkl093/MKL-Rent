"""Маршруты packing-листа внутри проекта (ТЗ §17–§20)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accessory_kits import service as accessory_kit_service
from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.estimates.service import get_estimate
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.models import EquipmentItem
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.packing import service
from app.packing.calc import compute_line
from app.packing.enums import PackingStatus
from app.packing.schemas import CustomPackingLine
from app.projects.availability import compute_availability, compute_kit_availability
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


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


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

    # Перечень комплектации для строк-комплектов — «живьём» по kit_id («Комплект»).
    kit_content: dict[int, list] = {}
    accessory_kit_content: dict[int, list] = {}
    for ln in packing.lines:
        if ln.kit_id is not None:
            kit = kit_service.get_kit(db, ln.kit_id)
            if kit is not None:
                kit_content[ln.id] = kit_service.content_groups(kit)
        elif ln.accessory_kit_id is not None:
            ak = accessory_kit_service.get(db, ln.accessory_kit_id)
            if ak is not None:
                accessory_kit_content[ln.id] = ak.lines

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
            "kit_content": kit_content,
            "accessory_kit_content": accessory_kit_content,
            "totals": service.totals(db, packing),
            "breakdown": service.category_breakdown(db, packing),
            "accessories": service.accessory_totals(db, packing),
            "discrepancies": service.discrepancies(db, project, packing),
            "sync_removals_with_fact": [
                ln for ln in service.sync_removals(db, project, packing) if ln.fact_quantity > 0
            ],
            "estimate_sync_items": service.estimate_discrepancies(db, project, packing),
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
        audit_log(
            db,
            user,
            EventType.PACKING_CREATE,
            f"Создан packing-лист {packing.number} для {project.number}",
            object_type="packing_list",
            object_id=packing.id,
        )
        flash(request, f"Packing-лист {packing.number} создан.", "success")
    except service.AlreadyExists as exc:
        flash(request, str(exc), "warning")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/sync", dependencies=[Depends(verify_csrf)])
def packing_sync(
    request: Request,
    project_id: int,
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    try:
        service.apply_sync(db, project, packing, confirm_delete=confirm is not None)
    except service.SyncConfirmRequired as exc:
        flash(
            request,
            "Синхронизация удалит уже собранные позиции: "
            + ", ".join(exc.names)
            + ". Подтвердите удаление и повторите синхронизацию.",
            "danger",
        )
        return redirect(f"/projects/{project_id}/packing")
    audit_log(
        db,
        user,
        EventType.PACKING_SYNC,
        f"Packing-лист {packing.number}: план синхронизирован со сметой",
        object_type="packing_list",
        object_id=packing.id,
    )
    flash(request, "План синхронизирован со сметой.", "success")
    return redirect(f"/projects/{project_id}/packing")


@router.post("/estimate-sync", dependencies=[Depends(verify_csrf)])
async def packing_estimate_sync(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")
    form = await request.form()
    selected_keys = set(form.getlist("items"))
    if not selected_keys:
        flash(request, "Не отмечено ни одной позиции для переноса в смету.", "warning")
        return redirect(f"/projects/{project_id}/packing")
    items = service.apply_estimate_sync(db, project, packing, selected_keys)
    if items:
        estimate = get_estimate(db, project)
        audit_log(
            db,
            user,
            EventType.ESTIMATE_CHANGE,
            f"Смета {estimate.number if estimate else project.number}: количества обновлены"
            f" по факту packing-листа {packing.number} — позиций {len(items)}",
            object_type="estimate",
            object_id=estimate.id if estimate else None,
        )
        flash(request, f"Смета обновлена по факту packing-листа: позиций {len(items)}.", "success")
    else:
        flash(request, "Расхождений нет — смета уже соответствует факту.", "info")
    return redirect(f"/projects/{project_id}/packing")


@router.get("/add")
def add_picker(
    request: Request,
    project_id: int,
    q: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    only: str | None = None,
    barcode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id)
    if project is None or packing is None:
        flash(request, "Packing-лист не создан.", "warning")
        return redirect(f"/projects/{project_id}/packing")

    filters = eq_service.ModelFilters(
        query=q,
        category_id=_opt_id(category_id),
        subcategory_id=_opt_id(subcategory_id),
        archived=False,
    )
    kits = kit_service.list_kits(db)
    kit_in_packing = {ln.kit_id for ln in packing.lines if ln.kit_id is not None}
    model_in_packing: dict[int, int] = {}
    for ln in packing.lines:
        if ln.model_id is not None:
            model_in_packing[ln.model_id] = model_in_packing.get(ln.model_id, 0) + ln.planned_quantity

    scanned_model_id = None
    if barcode:
        item = item_service.find_by_barcode(db, barcode)
        if item is None:
            flash(request, f"Штрих-код «{barcode.strip()}» не найден.", "warning")
            models = eq_service.list_models(db, filters)
        else:
            models = [item.model]
            kits = []
            scanned_model_id = item.model_id
    else:
        models = eq_service.list_models(db, filters)

    only_added = only == "added"
    if only_added:
        models = [m for m in models if m.id in model_in_packing]
        kits = [k for k in kits if k.id in kit_in_packing]

    availability = {}
    kit_availability = {}
    if project.start_date and project.end_date:
        for m in models:
            availability[m.id] = compute_availability(
                db, m, project.start_date, project.end_date, exclude_project_id=project.id
            )
        for k in kits:
            kit_availability[k.id] = compute_kit_availability(
                db, k.id, project.start_date, project.end_date, exclude_project_id=project.id
            )

    return render(
        request,
        "packing/add.html",
        {
            "page_title": "Добавить оборудование",
            "project": project,
            "packing": packing,
            "models": models,
            "kits": kits,
            "kit_in_packing": kit_in_packing,
            "model_in_packing": model_in_packing,
            "only_added": only_added,
            "availability": availability,
            "kit_availability": kit_availability,
            "categories": cat_service.list_categories(db),
            "filters": filters,
            "q": q or "",
            "scanned_model_id": scanned_model_id,
            "editable": editable,
            "AccountingType": AccountingType,
        },
        db=db,
        user=user,
    )


@router.post("/add", dependencies=[Depends(verify_csrf)])
async def add_submit(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return redirect(f"/projects/{project_id}/packing")

    form = await request.form()
    added = 0
    for key in form:
        if key.startswith("select_"):
            model_id = int(key.split("_", 1)[1])
            model = eq_service.get_model(db, model_id)
            if model is None:
                continue
            qty = _int(form.get(f"qty_{model_id}"), 1)
            service.add_model(db, packing, model, qty)
            added += 1
        elif key.startswith("selectkit_"):
            kit_id = int(key.split("_", 1)[1])
            kit = kit_service.get_kit(db, kit_id)
            if kit is None:
                continue
            if service.add_kit(db, packing, kit) is not None:
                added += 1
    if added:
        audit_log(
            db,
            user,
            EventType.PACKING_ADD,
            f"Packing {packing.number}: добавлено оборудование со склада — позиций {added}",
            object_type="packing_list",
            object_id=packing.id,
        )
    flash(request, f"Добавлено позиций: {added}.", "success" if added else "warning")
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
    unit_weight_kg: str | None = Form(None),
    length_mm: str | None = Form(None),
    width_mm: str | None = Form(None),
    height_mm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    packing, line, _ = _line(db, project_id, line_id)
    if line is not None and not line.is_serial:
        service.update_quantity_line(
            db,
            line,
            _int(fact_quantity),
            _int(packed_quantity),
            comment,
            unit_weight_kg=_dec(unit_weight_kg) if unit_weight_kg is not None else None,
            length_mm=_int(length_mm) if length_mm is not None else None,
            width_mm=_int(width_mm) if width_mm is not None else None,
            height_mm=_int(height_mm) if height_mm is not None else None,
        )
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
            if result in (service.SerialResult.OK, service.SerialResult.OVER_PLAN):
                over = " (сверх плана)" if result == service.SerialResult.OVER_PLAN else ""
                audit_log(
                    db,
                    user,
                    EventType.PACKING_SCAN,
                    f"Packing {packing.number}: добавлен экземпляр {barcode.strip()}{over}"
                    f" — {line.name}",
                    object_type="packing_list",
                    object_id=packing.id,
                )
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


# --- Сканирование (ТЗ §22) ----------------------------------------------

_SCAN_MESSAGES = {
    service.SerialResult.OK: "Добавлено",
    service.SerialResult.OVER_PLAN: "Добавлено сверх плана",
    service.SerialResult.DUPLICATE: "Уже в листе",
    service.SerialResult.WRONG_MODEL: "Модели нет в packing-листе",
    service.SerialResult.BLOCKED: "Экземпляр в ремонте или списан",
    service.SerialResult.NOT_FOUND: "Штрих-код не найден",
}

_REMOVE_MESSAGES = {
    service.RemoveResult.OK: "Убрано",
    service.RemoveResult.NOT_IN_PACKING: "Экземпляра нет в этом packing-листе",
    service.RemoveResult.NOT_FOUND: "Штрих-код не найден",
}


@router.get("/scan")
def scan_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id)
    if project is None or packing is None:
        flash(request, "Packing-лист не создан.", "warning")
        return redirect(f"/projects/{project_id}/packing")
    serial_lines = [ln for ln in packing.lines if ln.is_serial]
    total_planned = sum(ln.planned_quantity for ln in serial_lines)
    total_fact = sum(ln.fact_quantity for ln in serial_lines)
    return render(
        request,
        "packing/scan.html",
        {
            "page_title": f"Сканирование {packing.number}",
            "project": project,
            "packing": packing,
            "editable": editable,
            "total_planned": total_planned,
            "total_fact": total_fact,
        },
        db=db,
        user=user,
    )


@router.post("/scan", dependencies=[Depends(verify_csrf)])
def scan_submit(
    request: Request,
    project_id: int,
    barcode: str = Form(...),
    allow_over: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    outcome = service.scan(db, packing, barcode, allow_over=bool(allow_over))
    if outcome.ok and outcome.serial_item_id:
        request.session["last_scan"] = {
            "project_id": project_id,
            "line_id": outcome.line_id,
            "serial_item_id": outcome.serial_item_id,
        }
        over = " (сверх плана)" if outcome.result == service.SerialResult.OVER_PLAN else ""
        audit_log(
            db,
            user,
            EventType.PACKING_SCAN,
            f"Packing {packing.number}: сканирование {outcome.barcode}{over}"
            f" — {outcome.model_name}",
            object_type="packing_list",
            object_id=packing.id,
        )
    serial_lines = [ln for ln in packing.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": outcome.ok,
            "result": outcome.result.value,
            "over": outcome.result == service.SerialResult.OVER_PLAN,
            "message": _SCAN_MESSAGES[outcome.result],
            "barcode": outcome.barcode,
            "model": outcome.model_name,
            "planned": outcome.planned,
            "fact": outcome.fact,
            "total_planned": sum(ln.planned_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


@router.post("/scan/add-model", dependencies=[Depends(verify_csrf)])
def scan_add_model(
    request: Request,
    project_id: int,
    barcode: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Подтверждённое добавление новой строки плана по сканированию (WRONG_MODEL → «Добавить»)."""
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    outcome = service.scan_add_new_model(db, packing, barcode)
    if outcome.ok and outcome.serial_item_id:
        request.session["last_scan"] = {
            "project_id": project_id,
            "line_id": outcome.line_id,
            "serial_item_id": outcome.serial_item_id,
        }
        audit_log(
            db,
            user,
            EventType.PACKING_SCAN,
            f"Packing {packing.number}: новая позиция по сканированию {outcome.barcode}"
            f" — {outcome.model_name}",
            object_type="packing_list",
            object_id=packing.id,
        )
    serial_lines = [ln for ln in packing.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": outcome.ok,
            "result": outcome.result.value,
            "message": _SCAN_MESSAGES[outcome.result],
            "barcode": outcome.barcode,
            "model": outcome.model_name,
            "planned": outcome.planned,
            "fact": outcome.fact,
            "total_planned": sum(ln.planned_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


@router.post("/scan/undo", dependencies=[Depends(verify_csrf)])
def scan_undo(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    last = request.session.get("last_scan")
    project, packing, editable = _load(db, project_id, require_editable=True)
    if not last or last.get("project_id") != project_id or packing is None or not editable:
        return JSONResponse({"ok": False, "message": "Нечего отменять"})
    line = service.get_line(db, packing, last["line_id"])
    if line is not None:
        service.remove_serial_item(db, line, last["serial_item_id"])
    audit_log(
        db,
        user,
        EventType.PACKING_SCAN_UNDO,
        f"Packing {packing.number}: отмена последнего сканирования",
        object_type="packing_list",
        object_id=packing.id,
    )
    request.session.pop("last_scan", None)
    serial_lines = [ln for ln in packing.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": True,
            "message": "Последнее сканирование отменено",
            "total_planned": sum(ln.planned_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


@router.post("/scan/remove", dependencies=[Depends(verify_csrf)])
def scan_remove(
    request: Request,
    project_id: int,
    barcode: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Убрать конкретный экземпляр из packing-листа по штрих-коду (не обязательно последний)."""
    project, packing, editable = _load(db, project_id, require_editable=True)
    if project is None or packing is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    outcome = service.remove_by_barcode(db, packing, barcode)
    if outcome.ok:
        audit_log(
            db,
            user,
            EventType.PACKING_SCAN_UNDO,
            f"Packing {packing.number}: убран по штрих-коду {outcome.barcode}"
            f" — {outcome.model_name}",
            object_type="packing_list",
            object_id=packing.id,
        )
    serial_lines = [ln for ln in packing.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": outcome.ok,
            "result": outcome.result.value,
            "message": _REMOVE_MESSAGES[outcome.result],
            "barcode": outcome.barcode,
            "model": outcome.model_name,
            "planned": outcome.planned,
            "fact": outcome.fact,
            "total_planned": sum(ln.planned_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


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
        note = ""
        if new_status == PackingStatus.PICKED and packing.shortage_comment:
            note = f" (недокомплект: {packing.shortage_comment})"
        audit_log(
            db,
            user,
            EventType.PACKING_STATUS,
            f"Packing {packing.number}: статус «{new_status.label}»{note}",
            object_type="packing_list",
            object_id=packing.id,
        )
        flash(request, f"Статус: {new_status.label}.", "info")
    except service.UndercompleteError:
        flash(request, "Недокомплект: подтвердите и укажите причину.", "danger")
    return redirect(f"/projects/{project_id}/packing")
