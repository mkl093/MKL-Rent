"""Доска распределения оборудования по машинам внутри проекта (ТЗ: транспорт)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.packing import service as packing_service
from app.projects.service import get_project
from app.templating import flash
from app.transport import service

router = APIRouter(prefix="/projects/{project_id}/transport", tags=["transport"])


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


def _load(db: Session, project_id: int):
    project = get_project(db, project_id)
    if project is None:
        return None, None
    return project, not project.is_archived


@router.get("")
def transport_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")

    if not packing_service.project_has_packing(db, project_id):
        flash(request, "Сначала создайте packing-лист.", "warning")
        return redirect(f"/projects/{project_id}/packing")

    board = service.board(db, project)
    used_vehicle_ids = {vb.project_vehicle.vehicle_id for vb in board.vehicles}
    available_vehicles = [v for v in service.list_vehicles(db) if v.id not in used_vehicle_ids]

    return render(
        request,
        "transport/board.html",
        {
            "page_title": "Транспорт",
            "project": project,
            "editable": editable,
            "board": board,
            "available_vehicles": available_vehicles,
        },
        db=db,
        user=user,
    )


@router.post("/vehicles", dependencies=[Depends(verify_csrf)])
def add_vehicle(
    request: Request,
    project_id: int,
    vehicle_id: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/transport")
    vehicle = service.get_vehicle(db, _int(vehicle_id))
    if vehicle is not None:
        service.add_vehicle_to_project(db, project, vehicle)
        flash(request, f"Машина «{vehicle.name}» добавлена в проект.", "success")
    return redirect(f"/projects/{project_id}/transport")


@router.post("/vehicles/{pv_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_vehicle(
    request: Request,
    project_id: int,
    pv_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/transport")
    pv = service.get_project_vehicle(db, project, pv_id)
    if pv is not None:
        name = pv.name
        service.remove_project_vehicle(db, pv)
        flash(request, f"Машина «{name}» убрана из проекта.", "info")
    return redirect(f"/projects/{project_id}/transport")


@router.post("/assign", dependencies=[Depends(verify_csrf)])
async def assign_lines(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/transport")

    form = await request.form()
    pv_id = _int(form.get("target"))
    pv = service.get_project_vehicle(db, project, pv_id)
    if pv is None:
        flash(request, "Выберите машину назначения.", "warning")
        return redirect(f"/projects/{project_id}/transport")

    line_ids = [_int(v) for v in form.getlist("line_id")]
    assigned = 0
    for line_id in line_ids:
        line = service.get_line(db, project, line_id)
        if line is None:
            continue
        remaining = service.remaining_quantity(db, project, line)
        if remaining <= 0:
            continue
        # Необязательное поле qty_{line_id} — частичный перенос (дробление позиции).
        requested = _int(form.get(f"qty_{line_id}"), 0)
        qty = min(requested, remaining) if requested > 0 else remaining
        try:
            service.assign(db, project, pv, line, qty)
            assigned += 1
        except service.TransportError:
            continue
    if assigned:
        audit_log(
            db,
            user,
            EventType.TRANSPORT_ASSIGN,
            f"Проект {project.number}: в машину «{pv.name}» назначено позиций — {assigned}",
            object_type="project",
            object_id=project.id,
        )
        flash(request, f"Назначено позиций: {assigned}.", "success")
    else:
        flash(request, "Ничего не назначено — выберите позиции.", "warning")
    return redirect(f"/projects/{project_id}/transport")


@router.post("/assignments/{assignment_id}/quantity", dependencies=[Depends(verify_csrf)])
def set_assignment_quantity(
    request: Request,
    project_id: int,
    assignment_id: int,
    quantity: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Уменьшить/увеличить конкретное назначение (частичный возврат в пул)."""
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/transport")
    assignment = service.get_assignment(db, project, assignment_id)
    if assignment is not None:
        qty = _int(quantity)
        if qty <= 0:
            service.unassign(db, assignment)
        elif qty < assignment.quantity:
            service.unassign(db, assignment, assignment.quantity - qty)
        flash(request, "Количество обновлено.", "success")
    return redirect(f"/projects/{project_id}/transport")


@router.post("/assignments/{assignment_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_assignment(
    request: Request,
    project_id: int,
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return redirect(f"/projects/{project_id}/transport")
    assignment = service.get_assignment(db, project, assignment_id)
    if assignment is not None:
        service.unassign(db, assignment)
        flash(request, "Позиция возвращена в нераспределённые.", "info")
    return redirect(f"/projects/{project_id}/transport")


@router.post("/api/move", dependencies=[Depends(verify_csrf)])
def api_move(
    request: Request,
    project_id: int,
    line_id: str = Form(...),
    source: str = Form(...),
    target: str = Form(...),
    quantity: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Перетаскивание карточки позиции между пулом и машинами (drag-and-drop).

    ``source``/``target`` — id ProjectVehicle или литерал "pool" (нераспределённое).
    """
    project, editable = _load(db, project_id)
    if project is None or not editable:
        return JSONResponse({"ok": False, "message": "Недоступно"}, status_code=400)

    line = service.get_line(db, project, _int(line_id))
    if line is None:
        return JSONResponse({"ok": False, "message": "Позиция не найдена"}, status_code=404)

    requested = _int(quantity) if quantity else None

    if source == "pool":
        target_pv = service.get_project_vehicle(db, project, _int(target))
        if target_pv is None:
            return JSONResponse({"ok": False, "message": "Машина не найдена"}, status_code=404)
        remaining = service.remaining_quantity(db, project, line)
        qty = min(requested, remaining) if requested else remaining
        if qty <= 0:
            return JSONResponse({"ok": False, "message": "Нечего распределять"}, status_code=400)
        service.assign(db, project, target_pv, line, qty)
    else:
        source_pv = service.get_project_vehicle(db, project, _int(source))
        if source_pv is None:
            return JSONResponse({"ok": False, "message": "Машина не найдена"}, status_code=404)
        assignment = service.get_assignment_for(db, source_pv, line)
        if assignment is None:
            return JSONResponse({"ok": False, "message": "Назначение не найдено"}, status_code=404)
        qty = min(requested, assignment.quantity) if requested else assignment.quantity
        if target == "pool":
            service.unassign(db, assignment, qty)
        else:
            target_pv = service.get_project_vehicle(db, project, _int(target))
            if target_pv is None:
                return JSONResponse({"ok": False, "message": "Машина не найдена"}, status_code=404)
            service.move(db, assignment, target_pv, qty)

    audit_log(
        db,
        user,
        EventType.TRANSPORT_ASSIGN,
        f"Проект {project.number}: перемещение позиции между машинами",
        object_type="project",
        object_id=project.id,
    )

    overloaded = [
        vb.project_vehicle.name for vb in service.board(db, project).vehicles if vb.totals.overload
    ]
    return JSONResponse({"ok": True, "overloaded": overloaded})
