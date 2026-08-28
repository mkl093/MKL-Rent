"""Управление гостевыми аккаунтами клиентов (для персонала)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.guests import service as guest_service
from app.guests.models import GUEST_LEVEL_AVAILABILITY, GUEST_LEVEL_VIEW
from app.templating import flash

router = APIRouter(prefix="/guest-access", tags=["guest-access"])


def _clamp_level(value: int) -> int:
    return max(GUEST_LEVEL_VIEW, min(GUEST_LEVEL_AVAILABILITY, value))


@router.get("")
def guest_access_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "guest_access/list.html",
        {"page_title": "Гостевой доступ", "guests": guest_service.list_guests(db)},
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def guest_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    access_level: int = Form(GUEST_LEVEL_VIEW),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    username = username.strip()
    display_name = display_name.strip()
    if not username or not password or not display_name:
        flash(request, "Укажите логин, пароль и название клиента.", "danger")
    elif guest_service.get_guest_by_username(db, username):
        flash(request, "Гостевой аккаунт с таким логином уже существует.", "danger")
    else:
        created = guest_service.create_guest(
            db, username, password, display_name, _clamp_level(access_level)
        )
        audit_log(
            db,
            user,
            EventType.GUEST_MANAGE,
            f"Создан гостевой аккаунт «{created.display_name}» ({created.username})",
            object_type="guest_user",
            object_id=created.id,
        )
        flash(request, "Гостевой аккаунт создан.", "success")
    return redirect("/guest-access")


def _target(db: Session, guest_id: int):
    return guest_service.get_guest_by_id(db, guest_id)


@router.post("/{guest_id}/level", dependencies=[Depends(verify_csrf)])
def guest_set_level(
    request: Request,
    guest_id: int,
    access_level: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, guest_id)
    if target is not None:
        guest_service.set_access_level(db, target, _clamp_level(access_level))
        audit_log(
            db,
            user,
            EventType.GUEST_MANAGE,
            f"Изменён уровень доступа «{target.display_name}»: {target.access_level}",
            object_type="guest_user",
            object_id=target.id,
        )
        flash(request, "Уровень доступа обновлён.", "success")
    return redirect("/guest-access")


@router.post("/{guest_id}/password", dependencies=[Depends(verify_csrf)])
def guest_password(
    request: Request,
    guest_id: int,
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, guest_id)
    if target is None:
        return redirect("/guest-access")
    if not password:
        flash(request, "Пароль не может быть пустым.", "danger")
        return redirect("/guest-access")
    guest_service.set_password(db, target, password)
    audit_log(
        db,
        user,
        EventType.GUEST_MANAGE,
        f"Сброшен пароль гостевого аккаунта «{target.display_name}»",
        object_type="guest_user",
        object_id=target.id,
    )
    flash(request, "Пароль обновлён.", "success")
    return redirect("/guest-access")


@router.post("/{guest_id}/unlock", dependencies=[Depends(verify_csrf)])
def guest_unlock(
    request: Request,
    guest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, guest_id)
    if target is not None:
        guest_service.unlock_guest(db, target)
        audit_log(
            db,
            user,
            EventType.GUEST_MANAGE,
            f"Снята блокировка попыток входа: «{target.display_name}»",
            object_type="guest_user",
            object_id=target.id,
        )
        flash(request, "Блокировка попыток входа снята.", "info")
    return redirect("/guest-access")


@router.post("/{guest_id}/block", dependencies=[Depends(verify_csrf)])
def guest_block(
    request: Request,
    guest_id: int,
    blocked: int = Form(1),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, guest_id)
    if target is None:
        return redirect("/guest-access")
    guest_service.set_blocked(db, target, bool(blocked))
    audit_log(
        db,
        user,
        EventType.GUEST_MANAGE,
        ("Заблокирован" if blocked else "Разблокирован")
        + f" гостевой аккаунт «{target.display_name}»",
        object_type="guest_user",
        object_id=target.id,
    )
    flash(request, "Аккаунт заблокирован." if blocked else "Аккаунт разблокирован.", "info")
    return redirect("/guest-access")


@router.post("/{guest_id}/delete", dependencies=[Depends(verify_csrf)])
def guest_delete(
    request: Request,
    guest_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, guest_id)
    if target is None:
        return redirect("/guest-access")
    name = target.display_name
    gid = target.id
    guest_service.delete_guest(db, target)
    audit_log(
        db,
        user,
        EventType.GUEST_MANAGE,
        f"Удалён гостевой аккаунт «{name}»",
        object_type="guest_user",
        object_id=gid,
    )
    flash(request, "Гостевой аккаунт удалён.", "success")
    return redirect("/guest-access")
