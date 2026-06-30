"""Маршруты PDF-документов внутри проекта (ТЗ §26)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import get_settings
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.documents import builder
from app.documents.enums import DocumentType, Language
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


@router.get("")
def documents_page(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")
    return render(
        request,
        "documents/page.html",
        {
            "page_title": "Документы",
            "project": project,
            "statuses": builder.status(db, project),
        },
        db=db,
        user=user,
    )


@router.post("/generate", dependencies=[Depends(verify_csrf)])
def documents_generate(
    request: Request,
    project_id: int,
    doc_type: str = Form(...),
    language: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    try:
        dt = DocumentType(doc_type)
        lang = Language(language).value
    except ValueError:
        return redirect(f"/projects/{project_id}/documents")
    try:
        builder.generate(db, project, dt, lang)
        flash(request, f"PDF сформирован: {dt.label} ({lang.upper()}).", "success")
    except builder.PdfUnavailable as exc:
        flash(request, f"PDF не сформирован: {exc}", "danger")
    return redirect(f"/projects/{project_id}/documents")


def _serve(db: Session, project_id: int, doc_type: str, language: str, *, download: bool):
    try:
        dt = DocumentType(doc_type)
        lang = Language(language).value
    except ValueError:
        return redirect(f"/projects/{project_id}/documents")
    doc = builder.get_document(db, project_id, dt, lang)
    if doc is None:
        return redirect(f"/projects/{project_id}/documents")
    path = Path(get_settings().storage_path) / doc.file_path
    if not path.exists():
        return redirect(f"/projects/{project_id}/documents")
    filename = f"{dt.value}_{lang}.pdf"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/{doc_type}/{language}/view")
def documents_view(
    project_id: int,
    doc_type: str,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _serve(db, project_id, doc_type, language, download=False)


@router.get("/{doc_type}/{language}/download")
def documents_download(
    project_id: int,
    doc_type: str,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _serve(db, project_id, doc_type, language, download=True)
