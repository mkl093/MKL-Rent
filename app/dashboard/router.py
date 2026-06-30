"""Главная страница (ТЗ §5). На Этапе 1 — заглушка с виджетами-плейсхолдерами."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import render, require_login

router = APIRouter(tags=["dashboard"])


@router.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(request, "dashboard/index.html", {"page_title": "Главная"}, db=db, user=user)
