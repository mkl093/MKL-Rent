"""Маршруты входа/выхода (ТЗ §4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.auth import service
from app.database import get_db
from app.dependencies import (
    SESSION_USER_KEY,
    get_current_user,
    redirect,
    render,
    verify_csrf,
)
from app.templating import flash

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user is not None:
        return redirect("/")
    return render(request, "auth/login.html", {"page_title": "Вход"}, db=db)


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = service.authenticate(db, username.strip(), password)
    except service.AccountLocked:
        flash(request, "Слишком много попыток входа. Повторите позже.", "danger")
        return render(
            request, "auth/login.html", {"page_title": "Вход", "username": username}, db=db
        )
    except service.AccountDisabled:
        flash(request, "Учётная запись отключена или заблокирована.", "danger")
        return render(
            request, "auth/login.html", {"page_title": "Вход", "username": username}, db=db
        )
    except service.AuthError:
        flash(request, "Неверный логин или пароль.", "danger")
        return render(
            request, "auth/login.html", {"page_title": "Вход", "username": username}, db=db
        )

    request.session[SESSION_USER_KEY] = user.id
    flash(request, f"Добро пожаловать, {user.username}!", "success")
    return redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.pop(SESSION_USER_KEY, None)
    flash(request, "Вы вышли из системы.", "info")
    return redirect("/login")
