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
from app.packing import carnet, combined
from app.packing.service import backfill_quantity_barcodes, list_packings
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


def _project_packing(db: Session, project, packing_id: int):
    """Packing-лист стенда этого проекта по id."""
    return next((pl for pl in list_packings(db, project) if pl.id == packing_id), None)


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
    packings = list_packings(db, project)
    packing_by_section = {pl.section_id: pl for pl in packings}
    # По каждому стенду: PDF активной версии сметы (неактивные — со страницы сметы),
    # packing-лист и лист комплектации его packing-листа, carnet.
    section_blocks = []
    for section in estimate_versions.list_sections(db, project):
        packing = packing_by_section.get(section.id)
        section_blocks.append(
            {
                "section": section,
                "estimate": builder.estimate_status(db, project, section.active_version)
                if section.active_version is not None
                else [],
                "packing": packing,
                "packing_docs": builder.packing_status(db, project, packing) if packing else [],
            }
        )
    return render(
        request,
        "documents/page.html",
        {
            "page_title": "Документы",
            "project": project,
            "section_blocks": section_blocks,
            "statuses": builder.status(db, project),
            # Общий packing-лист проекта — только при нескольких листах стендов.
            "combined_docs": builder.combined_status(db, project) if len(packings) > 1 else [],
        },
        db=db,
        user=user,
    )


def _carnet_response(db: Session, project, packings: list):
    """Carnet листа стенда (один лист) или общий по проекту (строки одной модели слиты)."""
    if not project.is_archived:
        for pl in packings:
            backfill_quantity_barcodes(db, pl)
    if len(packings) == 1:
        packing = packings[0]
        rows = carnet.build_rows(db, packing)
        title, name = f"Packing-лист: {packing.number}", packing.number
    else:
        rows = carnet.build_rows(db, combined.combine_lines(packings))
        title = "Packing-листы: " + ", ".join(
            f"{pl.number} ({pl.section.name})" for pl in packings
        )
        name = f"{project.number}_all"
    wb = carnet.build_workbook(project, title, rows)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="carnet_{name}.xlsx"'},
    )


@router.get("/carnet")
def carnet_export(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """ATA Carnet General List (Excel) по проекту: общий по всем листам стендов
    (при одном листе — carnet этого листа)."""
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    packings = list_packings(db, project)
    if not packings:
        raise HTTPException(status_code=404)
    return _carnet_response(db, project, packings)


@router.get("/carnet/{packing_id}")
def carnet_export_packing(
    project_id: int,
    packing_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Экспорт packing-листа стенда в ATA Carnet General List (Excel)."""
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    packing = _project_packing(db, project, packing_id)
    if packing is None:
        raise HTTPException(status_code=404)
    return _carnet_response(db, project, [packing])


def _safe_back(project_id: int, back: str | None) -> str:
    """Куда вернуться после генерации: только внутрь этого проекта."""
    prefix = f"/projects/{project_id}/"
    if back and back.startswith(prefix) and "//" not in back and "\\" not in back:
        return back
    return f"/projects/{project_id}/documents"


def _resolve_object(db: Session, project, dt: DocumentType, object_id: int | None):
    """(estimate, packing) для документа: версия сметы или packing-лист стенда.

    Без явного id — смета активной версии первого стенда (старые ссылки); для
    packing-листа и листа комплектации — общий лист проекта при нескольких листах
    (packing=None) или лист единственного стенда.
    Возвращает None, если объекта нет или он не принадлежит проекту.
    """
    if dt == DocumentType.ESTIMATE:
        estimate = (
            get_project_estimate(db, project, object_id)
            if object_id is not None
            else builder.default_estimate(db, project)
        )
        return None if estimate is None else (estimate, None)
    if dt in (DocumentType.PACKING, DocumentType.PICKING):
        if object_id is None:
            packings = list_packings(db, project)
            if not packings:
                return None
            return (None, packings[0] if len(packings) == 1 else None)
        packing = _project_packing(db, project, object_id)
        return None if packing is None else (None, packing)
    return (None, None)


@router.post("/generate", dependencies=[Depends(verify_csrf)])
def documents_generate(
    request: Request,
    project_id: int,
    doc_type: str = Form(...),
    language: str = Form(...),
    estimate_id: str | None = Form(None),
    packing_id: str | None = Form(None),
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
    raw_id = estimate_id if dt == DocumentType.ESTIMATE else packing_id
    object_id = int(raw_id) if raw_id and raw_id.strip().isdigit() else None
    resolved = _resolve_object(db, project, dt, object_id)
    if resolved is None:
        flash(request, "PDF не сформирован: Packing-лист не создан", "danger")
        return redirect(back_url)
    estimate, packing = resolved
    try:
        builder.generate(db, project, dt, lang, estimate=estimate, packing=packing)
        what = dt.label
        if estimate is not None:
            what = f"{dt.label} {estimate.display_number} («{estimate.section.name}»)"
        elif packing is not None:
            what = f"{dt.label} {packing.number} («{packing.section.name}»)"
        elif dt in (DocumentType.PACKING, DocumentType.PICKING):
            what = f"{dt.label} — все стенды"
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
    object_id: int | None = None,
):
    try:
        dt = DocumentType(doc_type)
        lang = Language(language).value
    except ValueError:
        return redirect(f"/projects/{project_id}/documents")
    project = get_project(db, project_id)
    if project is None:
        return redirect(f"/projects/{project_id}/documents")
    resolved = _resolve_object(db, project, dt, object_id)
    if resolved is None:
        return redirect(f"/projects/{project_id}/documents")
    estimate, packing = resolved
    doc = builder.get_document(
        db, project_id, dt, lang, builder.doc_scope(dt, estimate, packing)
    )
    if doc is None:
        return redirect(f"/projects/{project_id}/documents")
    path = Path(get_settings().storage_path) / doc.file_path
    if not path.exists():
        return redirect(f"/projects/{project_id}/documents")
    parts = [dt.value, _filename_part(project.number), _filename_part(project.name)]
    multi_section = len(estimate_versions.list_sections(db, project)) > 1
    if estimate is not None:
        section = estimate.section
        if multi_section:
            parts.append(_filename_part(section.name))
        if len(section.versions) > 1:
            parts.append(f"v{estimate.version_no}")
    elif packing is not None and multi_section:
        parts.append(_filename_part(packing.section.name))
    elif dt in (DocumentType.PACKING, DocumentType.PICKING):
        parts.append("all")
    filename = "_".join(parts) + f"_{lang}.pdf"
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )


# Документ конкретного объекта: /estimate/{id_версии}/… или /packing|picking/{id_листа}/…
@router.get("/{doc_type}/{object_id}/{language}/view")
def object_document_view(
    project_id: int,
    doc_type: str,
    object_id: int,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _serve(db, project_id, doc_type, language, download=False, object_id=object_id)


@router.get("/{doc_type}/{object_id}/{language}/download")
def object_document_download(
    project_id: int,
    doc_type: str,
    object_id: int,
    language: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _serve(db, project_id, doc_type, language, download=True, object_id=object_id)


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
