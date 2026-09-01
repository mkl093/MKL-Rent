"""Маршруты приёмки оборудования внутри проекта (ТЗ §56)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.projects.service import get_project
from app.returns import service
from app.returns.enums import ReturnCondition, ReturnStatus
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/returns", tags=["returns"])


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


def _load(db: Session, project_id: int, *, require_editable: bool = False):
    project = get_project(db, project_id)
    if project is None:
        return None, None, False
    ret = service.get_return(db, project)
    editable = not project.is_archived
    if require_editable and not editable:
        return project, ret, False
    return project, ret, editable


@router.get("")
def returns_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")

    if ret is None:
        return render(
            request,
            "returns/empty.html",
            {"page_title": "Приёмка оборудования", "project": project, "editable": editable},
            db=db,
            user=user,
        )

    ordered = sorted(
        ret.lines,
        key=lambda ln: (
            ln.is_custom,
            ln.category_name or "",
            ln.subcategory_name or "",
            ln.sort_order,
            ln.id,
        ),
    )
    return render(
        request,
        "returns/returns.html",
        {
            "page_title": f"Приёмка {ret.number}",
            "project": project,
            "ret": ret,
            "ordered": ordered,
            "incomplete": service.is_incomplete(ret),
            "editable": editable,
            "ReturnStatus": ReturnStatus,
            "ReturnCondition": ReturnCondition,
        },
        db=db,
        user=user,
    )


@router.post("/create", dependencies=[Depends(verify_csrf)])
def returns_create(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, _, editable = _load(db, project_id, require_editable=True)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/returns")
    try:
        ret = service.create_from_packing(db, project)
        audit_log(
            db,
            user,
            EventType.RETURN_CREATE,
            f"Оформлен возврат {ret.number} для {project.number}",
            object_type="return_list",
            object_id=ret.id,
        )
        flash(request, f"Возврат {ret.number} оформлен.", "success")
    except service.AlreadyExists as exc:
        flash(request, str(exc), "warning")
    return redirect(f"/projects/{project_id}/returns")


@router.post("/lines/{line_id}/quantity", dependencies=[Depends(verify_csrf)])
def line_quantity(
    request: Request,
    project_id: int,
    line_id: int,
    returned_quantity: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id, require_editable=True)
    if ret is not None and editable:
        line = service.get_line(db, ret, line_id)
        if line is not None and not line.is_serial:
            service.update_quantity_line(db, line, _int(returned_quantity), comment)
            flash(request, "Строка обновлена.", "success")
    return redirect(f"/projects/{project_id}/returns")


@router.post("/lines/accessory/{content_line_id}", dependencies=[Depends(verify_csrf)])
def accessory_content_line_quantity(
    request: Request,
    project_id: int,
    content_line_id: int,
    returned_quantity: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Сверить одну позицию содержимого комплекта аксессуаров (ТЗ §56.1, чек-лист)."""
    project, ret, editable = _load(db, project_id, require_editable=True)
    if ret is not None and editable:
        content_line = service.get_accessory_content_line(db, ret, content_line_id)
        if content_line is not None:
            service.update_accessory_content_line(db, content_line, _int(returned_quantity), comment)
            flash(request, "Позиция сверена.", "success")
    return redirect(f"/projects/{project_id}/returns")


@router.post("/lines/{line_id}/accept_all", dependencies=[Depends(verify_csrf)])
def line_accept_all(
    request: Request,
    project_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Принять пачкой всю серийную строку без сканирования (ТЗ §56.3)."""
    project, ret, editable = _load(db, project_id, require_editable=True)
    if ret is not None and editable:
        line = service.get_line(db, ret, line_id)
        if line is not None and line.is_serial:
            count = service.accept_all(db, line)
            if count:
                audit_log(
                    db,
                    user,
                    EventType.RETURN_ACCEPT_ALL,
                    f"Возврат {ret.number}: строка «{line.name}» принята пачкой — {count} шт.",
                    object_type="return_list",
                    object_id=ret.id,
                )
                flash(request, f"Принято пачкой: {count} шт.", "success")
            else:
                flash(request, "Нечего принимать — все единицы уже приняты.", "info")
    return redirect(f"/projects/{project_id}/returns")


@router.post("/serial/{si_id}/condition", dependencies=[Depends(verify_csrf)])
def serial_condition(
    request: Request,
    project_id: int,
    si_id: int,
    condition: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id, require_editable=True)
    if ret is not None and editable:
        si = service.get_serial_item(db, ret, si_id)
        try:
            new_condition = ReturnCondition(condition)
        except ValueError:
            new_condition = None
        if si is not None and new_condition is not None:
            service.set_condition(db, si, new_condition, comment)
            audit_log(
                db,
                user,
                EventType.RETURN_CONDITION,
                f"Возврат {ret.number}: {si.barcode} — {new_condition.label}",
                object_type="return_list",
                object_id=ret.id,
            )
            flash(request, "Состояние обновлено.", "success")
    return redirect(f"/projects/{project_id}/returns")


# --- Сканирование (ТЗ §56.3) ---------------------------------------------

_SCAN_MESSAGES = {
    service.ScanResult.OK: "Принято",
    service.ScanResult.ALREADY: "Уже принято",
    service.ScanResult.SUBSTITUTE_CANDIDATE: "Другая единица той же модели",
    service.ScanResult.NOT_IN_LIST: "Не выдавалось по этому проекту",
    service.ScanResult.NOT_FOUND: "Штрих-код не найден",
}


@router.get("/scan")
def scan_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id)
    if project is None or ret is None:
        flash(request, "Возврат не оформлен.", "warning")
        return redirect(f"/projects/{project_id}/returns")
    serial_lines = [ln for ln in ret.lines if ln.is_serial]
    total_expected = sum(ln.expected_quantity for ln in serial_lines)
    total_fact = sum(ln.fact_quantity for ln in serial_lines)
    return render(
        request,
        "returns/scan.html",
        {
            "page_title": f"Приёмка {ret.number}",
            "project": project,
            "ret": ret,
            "editable": editable,
            "total_expected": total_expected,
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
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id, require_editable=True)
    if project is None or ret is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    outcome = service.scan(db, ret, barcode)
    if outcome.ok:
        request.session["last_return_scan"] = {
            "project_id": project_id,
            "serial_item_id": outcome.serial_item_id,
        }
        audit_log(
            db,
            user,
            EventType.RETURN_SCAN,
            f"Возврат {ret.number}: приём {outcome.barcode} — {outcome.model_name}",
            object_type="return_list",
            object_id=ret.id,
        )
    serial_lines = [ln for ln in ret.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": outcome.ok,
            "result": outcome.result.value,
            "message": _SCAN_MESSAGES[outcome.result],
            "barcode": outcome.barcode,
            "model": outcome.model_name,
            "serial_item_id": outcome.serial_item_id,
            "expected": outcome.expected,
            "fact": outcome.fact,
            "pending_serial_item_id": outcome.pending_serial_item_id,
            "pending_barcode": outcome.pending_barcode,
            "total_expected": sum(ln.expected_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


@router.post("/serial/substitute", dependencies=[Depends(verify_csrf)])
def serial_substitute(
    request: Request,
    project_id: int,
    pending_serial_item_id: int = Form(...),
    barcode: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Подтвердить замену единицы при приёмке (ТЗ §56.3)."""
    project, ret, editable = _load(db, project_id, require_editable=True)
    if project is None or ret is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    outcome = service.confirm_substitute(db, ret, pending_serial_item_id, barcode)
    if outcome.ok:
        request.session["last_return_scan"] = {
            "project_id": project_id,
            "serial_item_id": outcome.serial_item_id,
        }
        audit_log(
            db,
            user,
            EventType.RETURN_SUBSTITUTE,
            f"Возврат {ret.number}: замена — ожидался {outcome.pending_barcode}, "
            f"принят {outcome.barcode} ({outcome.model_name})",
            object_type="return_list",
            object_id=ret.id,
        )
    serial_lines = [ln for ln in ret.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": outcome.ok,
            "result": outcome.result.value,
            "message": _SCAN_MESSAGES[outcome.result],
            "barcode": outcome.barcode,
            "model": outcome.model_name,
            "serial_item_id": outcome.serial_item_id,
            "expected": outcome.expected,
            "fact": outcome.fact,
            "total_expected": sum(ln.expected_quantity for ln in serial_lines),
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
    last = request.session.get("last_return_scan")
    project, ret, editable = _load(db, project_id, require_editable=True)
    if not last or last.get("project_id") != project_id or ret is None or not editable:
        return JSONResponse({"ok": False, "message": "Нечего отменять"})
    service.undo_scan(db, ret, last["serial_item_id"])
    audit_log(
        db,
        user,
        EventType.RETURN_SCAN_UNDO,
        f"Возврат {ret.number}: отмена последнего сканирования",
        object_type="return_list",
        object_id=ret.id,
    )
    request.session.pop("last_return_scan", None)
    serial_lines = [ln for ln in ret.lines if ln.is_serial]
    return JSONResponse(
        {
            "ok": True,
            "message": "Последнее сканирование отменено",
            "total_expected": sum(ln.expected_quantity for ln in serial_lines),
            "total_fact": sum(ln.fact_quantity for ln in serial_lines),
        }
    )


@router.post("/status", dependencies=[Depends(verify_csrf)])
def returns_status(
    request: Request,
    project_id: int,
    status: str = Form(...),
    shortage_comment: str | None = Form(None),
    confirm_incomplete: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, ret, editable = _load(db, project_id, require_editable=True)
    if project is None or ret is None or not editable:
        return redirect(f"/projects/{project_id}/returns")
    try:
        new_status = ReturnStatus(status)
    except ValueError:
        return redirect(f"/projects/{project_id}/returns")
    try:
        service.set_status(
            db,
            ret,
            new_status,
            user_id=user.id,
            project_number=project.number,
            shortage_comment=shortage_comment,
            confirm_incomplete=bool(confirm_incomplete),
        )
        note = ""
        if new_status == ReturnStatus.RECEIVED and ret.shortage_comment:
            note = f" (недостача: {ret.shortage_comment})"
        audit_log(
            db,
            user,
            EventType.RETURN_STATUS,
            f"Возврат {ret.number}: статус «{new_status.label}»{note}",
            object_type="return_list",
            object_id=ret.id,
        )
        flash(request, f"Статус: {new_status.label}.", "info")
    except service.IncompleteError:
        flash(request, "Недостача: подтвердите и укажите причину.", "danger")
    return redirect(f"/projects/{project_id}/returns")
