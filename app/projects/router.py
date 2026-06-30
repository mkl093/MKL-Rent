"""Раздел «Проекты» (ТЗ §13). На Этапе 1 — заглушка, наполнение на Этапе 3."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import render, require_login

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "placeholder.html",
        {"page_title": "Проекты", "heading": "Проекты", "stage": "Этап 3"},
        db=db,
        user=user,
    )
