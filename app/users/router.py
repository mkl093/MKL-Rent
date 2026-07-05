"""Управление пользователями (ТЗ §4). Все пользователи имеют одинаковые права."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth import service as auth_service
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.templating import flash

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "users/list.html",
        {"page_title": "Пользователи", "users": auth_service.list_users(db)},
        db=db,
        user=user,
    )


@router.post("", dependencies=[Depends(verify_csrf)])
def user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    username = username.strip()
    if not username or not password:
        flash(request, "Укажите логин и пароль.", "danger")
    elif auth_service.get_user_by_username(db, username):
        flash(request, "Пользователь с таким логином уже существует.", "danger")
    else:
        created = auth_service.create_user(db, username, password)
        audit_log(
            db,
            user,
            EventType.USER_MANAGE,
            f"Создан пользователь «{created.username}»",
            object_type="user",
            object_id=created.id,
        )
        flash(request, "Пользователь создан.", "success")
    return redirect("/users")


def _target(db: Session, user_id: int) -> User | None:
    return auth_service.get_user_by_id(db, user_id)


@router.post("/{user_id}/block", dependencies=[Depends(verify_csrf)])
def user_block(
    request: Request,
    user_id: int,
    blocked: int = Form(1),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, user_id)
    if target is None:
        return redirect("/users")
    if target.id == user.id:
        flash(request, "Нельзя заблокировать самого себя.", "danger")
        return redirect("/users")
    auth_service.set_blocked(db, target, bool(blocked))
    audit_log(
        db,
        user,
        EventType.USER_MANAGE,
        ("Заблокирован" if blocked else "Разблокирован") + f" пользователь «{target.username}»",
        object_type="user",
        object_id=target.id,
    )
    flash(
        request, "Пользователь заблокирован." if blocked else "Пользователь разблокирован.", "info"
    )
    return redirect("/users")


@router.post("/{user_id}/unlock", dependencies=[Depends(verify_csrf)])
def user_unlock(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, user_id)
    if target is not None:
        auth_service.unlock_user(db, target)
        audit_log(
            db,
            user,
            EventType.USER_MANAGE,
            f"Снята блокировка попыток входа: «{target.username}»",
            object_type="user",
            object_id=target.id,
        )
        flash(request, "Блокировка попыток входа снята.", "info")
    return redirect("/users")


@router.post("/{user_id}/password", dependencies=[Depends(verify_csrf)])
def user_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, user_id)
    if target is None:
        return redirect("/users")
    if not password:
        flash(request, "Пароль не может быть пустым.", "danger")
        return redirect("/users")
    auth_service.set_password(db, target, password)
    audit_log(
        db,
        user,
        EventType.USER_MANAGE,
        f"Сброшен пароль: «{target.username}»",
        object_type="user",
        object_id=target.id,
    )
    flash(request, "Пароль обновлён.", "success")
    return redirect("/users")


@router.post("/{user_id}/delete", dependencies=[Depends(verify_csrf)])
def user_delete(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    target = _target(db, user_id)
    if target is None:
        return redirect("/users")
    if target.id == user.id:
        flash(request, "Нельзя удалить самого себя.", "danger")
        return redirect("/users")
    if auth_service.count_users(db) <= 1:
        flash(request, "Нельзя удалить последнего пользователя.", "danger")
        return redirect("/users")
    username = target.username
    tid = target.id
    auth_service.delete_user(db, target)
    audit_log(
        db,
        user,
        EventType.USER_MANAGE,
        f"Удалён пользователь «{username}»",
        object_type="user",
        object_id=tid,
    )
    flash(request, "Пользователь удалён.", "success")
    return redirect("/users")
