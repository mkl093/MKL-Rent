"""Раздел «Склад» (ТЗ §6). На Этапе 1 — заглушка, наполнение на Этапе 2."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import render, require_login

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "placeholder.html",
        {"page_title": "Склад", "heading": "Склад", "stage": "Этап 2"},
        db=db,
        user=user,
    )
