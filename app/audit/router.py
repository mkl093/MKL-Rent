"""Просмотр журнала действий (ТЗ §29)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.audit import service
from app.audit.events import EventType
from app.auth.models import User
from app.database import get_db
from app.dependencies import render, require_login

router = APIRouter(prefix="/audit", tags=["audit"])

PAGE_SIZE = 100


@router.get("")
def audit_page(
    request: Request,
    event_type: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    page = max(1, page)
    event_type = event_type or None
    entries = service.list_entries(
        db, event_type=event_type, limit=PAGE_SIZE + 1, offset=(page - 1) * PAGE_SIZE
    )
    has_next = len(entries) > PAGE_SIZE
    entries = entries[:PAGE_SIZE]

    labels = {e.value: e.label for e in EventType}
    return render(
        request,
        "audit/list.html",
        {
            "page_title": "Журнал действий",
            "entries": entries,
            "labels": labels,
            "event_types": list(EventType),
            "event_type": event_type,
            "page": page,
            "has_next": has_next,
        },
        db=db,
        user=user,
    )
