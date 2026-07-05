"""Резервное копирование через интерфейс (ТЗ §36).

Создание и скачивание — через UI. Восстановление — только через CLI
(scripts/restore.py), так как оно перезаписывает БД и требует остановки приложения.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.models import User
from app.backup import service
from app.config import get_settings
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.templating import flash

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("")
def backup_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    settings = get_settings()
    return render(
        request,
        "backup/list.html",
        {
            "page_title": "Резервные копии",
            "backups": service.list_backups(),
            "backup_path": settings.backup_path,
            "backup_time": settings.backup_time,
            "retention_days": settings.backup_retention_days,
            "auto": settings.backup_auto,
        },
        db=db,
        user=user,
    )


@router.post("/create", dependencies=[Depends(verify_csrf)])
def backup_create(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    try:
        path = service.create_backup()
        service.apply_retention()
        flash(request, f"Резервная копия создана: {path.name}", "success")
    except service.BackupError as exc:
        flash(request, f"Не удалось создать копию: {exc}", "danger")
    return redirect("/backup")


@router.get("/{name}/download")
def backup_download(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    path = service.get_backup(name)
    if path is None:
        return redirect("/backup")
    return FileResponse(str(path), media_type="application/gzip", filename=path.name)
