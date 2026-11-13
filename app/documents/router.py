"""Маршруты PDF-документов внутри проекта (ТЗ §26)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.config import get_settings
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.documents import builder
from app.documents.enums import DocumentType, Language
from app.estimates import versions as estimate_versions
from app.estimates.service import get_project_estimate
from app.packing import carnet
from app.packing.service import backfill_quantity_barcodes, get_packing
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
    estimate_versions.ensure_default_section(db, project)
    # PDF смет — по активной версии каждого стенда (неактивные — со страницы сметы).
    estimate_blocks = [
        (section, builder.estimate_status(db, project, section.active_version))
        for section in estimate_versions.list_sections(db, project)
        if section.active_version is not None
    ]
    return render(
        request,
        "documents/page.html",
        {
            "page_title": "Документы",
            "project": project,
            "estimate_blocks": estimate_blocks,
            "statuses": builder.status(db, project),
            "carnet_available": get_packing(db, project) is not None,
        },
        db=db,
        user=user,
    )


@router.get("/carnet")
def carnet_export(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Экспорт паккинг-листа в формате ATA Carnet General List (Excel)."""
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    packing = get_packing(db, project)
    if packing is None:
        raise HTTPException(status_code=404)
    if not project.is_archived:
        backfill_quantity_barcodes(db, packing)

    rows = carnet.build_rows(db, packing)
    wb = carnet.build_workbook(project, packing, rows)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="carnet_{packing.number}.xlsx"'},
    )


def _safe_back(project_id: int, back: str | None) -> str:
    """Куда вернуться после генерации: только внутрь этого проекта."""
    prefix = f"/projects/{project_id}/"
    if back and back.startswith(prefix) and "//" not in back and "\\" not in back:
        return back
    return f"/projects/{project_id}/documents"


@router.post("/generate", dependencies=[Depends(verify_csrf)])
def documents_generate(
    request: Request,
    project_id: int,
    doc_type: str = Form(...),
    language: str = Form(...),
    estimate_id: str | None = Form(None),
    back: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    back_url = _safe_back(project_id, back)
    try:
        dt = DocumentType(doc_type)
        lang = Language(language).value
    except ValueError:
        return redirect(back_url)
    estimate = None
    if dt == DocumentType.ESTIMATE and estimate_id and estimate_id.strip().isdigit():
        estimate = get_project_estimate(db, project, int(estimate_id))
        if estimate is None:
            return redirect(back_url)
    try:
        builder.generate(db, project, dt, lang, estimate=estimate)
        what = dt.label
        if dt == DocumentType.ESTIMATE:
            est = estimate or builder.default_estimate(db, project)
            what = f"{dt.label} {est.display_number} («{est.section.name}»)"
        audit_log(
            db,
            user,
            EventType.DOCUMENT_GENERATE,
            f"Сформирован PDF: {what} ({lang.upper()}) для {project.number}",
            object_type="project",
            object_id=project.id,
        )
        flash(request, f"PDF сформирован: {what} ({lang.upper()}).", "success")
    except builder.PdfUnavailable as exc:
        flash(request, f"PDF не сформирован: {exc}", "danger")
    return redirect(back_url)


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _filename_part(value: str) -> str:
    """Убрать символы, недопустимые в имени файла (Windows/заголовок Content-Disposition)."""
    return _UNSAFE_FILENAME_CHARS.sub("_", value).strip() or "-"


def _serve(
    db: Session,
    project_id: int,
    doc_type: str,
    language: str,
    *,
    download: bool,
    estimate_id: int | None = None,
):
    try:
        dt = DocumentType(doc_type)
        lang = Language(language).value
    except ValueError:
        return redirect(f"/projects/{project_id}/documents")
    project = get_project(db, project_id)
    if project is None:
        return redirect(f"/projects/{project_id}/documents")
    estimate = None
    if dt == DocumentType.ESTIMATE:
        # Без явной версии — PDF активной версии первого стенда (старые ссылки).
        estimate = (
            get_project_estimate(db, project, estimate_id)
            if estimate_id is not None
            else builder.default_estimate(db, project)
        )
        if estimate is None:
            return redirect(f"/projects/{project_id}/documents")
    doc = builder.get_document(db, project_id, dt, lang, builder.doc_scope(dt, estimate))
    if doc is None:
        return redirect(f"/projects/{project_id}/documents")
    path = Path(get_settings().storage_path) / doc.file_path
    if not path.exists():
        return redirect(f"/projects/{project_id}/documents")
    parts = [dt.value, _filename_part(project.number), _filename_part(project.name)]
    if estimate is not None:
        section = estimate.section
        if len(estimate_versions.list_sections(db, project)) > 1:
            parts.append(_filename_part(section.name))
        if len(section.versions) > 1:
            parts.append(f"v{estimate.version_no}")
    filename = "_".join(parts) + f"_{lang}.pdf"
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/estimate/{estimate_id}/{language}/view")
def estimate_document_view(
    project_id: int,
    estimate_id: int,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """PDF конкретной версии сметы."""
    return _serve(db, project_id, "estimate", language, download=False, estimate_id=estimate_id)


@router.get("/estimate/{estimate_id}/{language}/download")
def estimate_document_download(
    project_id: int,
    estimate_id: int,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _serve(db, project_id, "estimate", language, download=True, estimate_id=estimate_id)


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
