"""Справочник машин (ТЗ: транспорт)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.templating import flash
from app.transport import service

router = APIRouter(prefix="/transport", tags=["transport"])


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _opt_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@router.get("")
def vehicles_page(
    request: Request,
    show: str = "active",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicles = service.list_vehicles(db, include_archived=show != "active")
    if show == "archived":
        vehicles = [v for v in vehicles if v.is_archived]
    return render(
        request,
        "transport/list.html",
        {"page_title": "Транспорт", "vehicles": vehicles, "show": show},
        db=db,
        user=user,
    )


@router.get("/new")
def vehicle_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "transport/form.html",
        {"page_title": "Новая машина", "vehicle": None},
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def vehicle_create(
    request: Request,
    name: str = Form(...),
    plate_number: str | None = Form(None),
    max_weight_kg: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicle = service.create_vehicle(
        db,
        name=name,
        plate_number=_opt_str(plate_number),
        max_weight_kg=_dec(max_weight_kg),
        comment=_opt_str(comment),
    )
    audit_log(
        db,
        user,
        EventType.VEHICLE_MANAGE,
        f"Добавлена машина «{vehicle.name}»",
        object_type="vehicle",
        object_id=vehicle.id,
    )
    flash(request, "Машина добавлена.", "success")
    return redirect("/transport")


@router.get("/{vehicle_id}/edit")
def vehicle_edit(
    request: Request,
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicle = service.get_vehicle(db, vehicle_id)
    if vehicle is None:
        return redirect("/transport")
    return render(
        request,
        "transport/form.html",
        {"page_title": f"Машина — {vehicle.name}", "vehicle": vehicle},
        db=db,
        user=user,
    )


@router.post("/{vehicle_id}", dependencies=[Depends(verify_csrf)])
def vehicle_update(
    request: Request,
    vehicle_id: int,
    name: str = Form(...),
    plate_number: str | None = Form(None),
    max_weight_kg: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicle = service.get_vehicle(db, vehicle_id)
    if vehicle is None:
        return redirect("/transport")
    service.update_vehicle(
        db,
        vehicle,
        name=name,
        plate_number=_opt_str(plate_number),
        max_weight_kg=_dec(max_weight_kg),
        comment=_opt_str(comment),
    )
    audit_log(
        db,
        user,
        EventType.VEHICLE_MANAGE,
        f"Изменена машина «{vehicle.name}»",
        object_type="vehicle",
        object_id=vehicle.id,
    )
    flash(request, "Изменения сохранены.", "success")
    return redirect("/transport")


@router.post("/{vehicle_id}/archive", dependencies=[Depends(verify_csrf)])
def vehicle_archive(
    request: Request,
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicle = service.get_vehicle(db, vehicle_id)
    if vehicle is not None:
        service.archive_vehicle(db, vehicle)
        audit_log(
            db,
            user,
            EventType.VEHICLE_MANAGE,
            f"Машина «{vehicle.name}» перемещена в архив",
            object_type="vehicle",
            object_id=vehicle.id,
        )
        flash(request, "Машина перемещена в архив.", "info")
    return redirect("/transport")


@router.post("/{vehicle_id}/unarchive", dependencies=[Depends(verify_csrf)])
def vehicle_unarchive(
    request: Request,
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    vehicle = service.get_vehicle(db, vehicle_id)
    if vehicle is not None:
        service.unarchive_vehicle(db, vehicle)
        flash(request, "Машина возвращена из архива.", "success")
    return redirect("/transport")
