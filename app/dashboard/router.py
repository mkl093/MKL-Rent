"""Главная страница (ТЗ §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.dashboard import service
from app.database import get_db
from app.dependencies import render, require_login

router = APIRouter(tags=["dashboard"])


@router.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "dashboard/index.html",
        {
            "page_title": "Главная",
            "active_booked": service.active_booked(db),
            "overdue_booked": service.overdue_booked(db),
            "deficit_projects": service.projects_with_deficit(db),
            "repair_items": service.repair_items(db),
        },
        db=db,
        user=user,
    )
