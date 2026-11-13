"""Маршруты смет внутри проекта: площадки, версии, строки (ТЗ §16).

Смета открывается по площадке и номеру версии:
/projects/{pid}/sections/{sid}/estimate/v/{n}. Старый адрес
/projects/{pid}/estimate ведёт на единственную площадку (или на карточку проекта,
если площадок несколько).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.estimates import service, versions
from app.estimates.models import Estimate, ProjectSection
from app.estimates.schemas import CustomLineInput, LineUpdate
from app.inventory.enums import AccountingType
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.projects.availability import compute_availability, compute_kit_availability
from app.projects.models import Project
from app.projects.service import get_project
from app.templating import flash

router = APIRouter(prefix="/projects/{project_id}", tags=["estimates"])


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _int(value: str | None, default: int = 1) -> int:
    try:
        return max(default, int(float((value or "").replace(",", ".").strip())))
    except (ValueError, AttributeError):
        return default


def _pct(value: str | None) -> Decimal:
    """Скидка/процент, приведённый к диапазону 0–100."""
    return max(Decimal("0"), min(Decimal("100"), _dec(value)))


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None


def _version_url(project_id: int, section_id: int, version_no: int) -> str:
    return f"/projects/{project_id}/sections/{section_id}/estimate/v/{version_no}"


def _project_url(project_id: int) -> str:
    return f"/projects/{project_id}#sections"


def _load_section(
    db: Session, project_id: int, section_id: int
) -> tuple[Project | None, ProjectSection | None]:
    project = get_project(db, project_id)
    if project is None:
        return None, None
    return project, versions.get_section(db, project, section_id)


def _load_version(
    db: Session, project_id: int, section_id: int, version_no: int
) -> tuple[Project, ProjectSection, Estimate] | None:
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None:
        return None
    estimate = versions.get_version(db, section, version_no)
    if estimate is None:
        return None
    return project, section, estimate


def _deny_edit(request: Request, project: Project, estimate: Estimate) -> bool:
    """Изменять нельзя: архивный проект или замороженная версия (с сообщением)."""
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return True
    if estimate.is_locked:
        flash(
            request,
            f"Версия {estimate.display_number} заморожена — изменения запрещены.",
            "warning",
        )
        return True
    return False


def _audit(db: Session, user: User, estimate: Estimate, text: str) -> None:
    audit_log(
        db,
        user,
        EventType.ESTIMATE_CHANGE,
        f"Смета {estimate.display_number} (площадка «{estimate.section.name}»): {text}",
        object_type="estimate",
        object_id=estimate.id,
    )


# --- Совместимость со старыми адресами ---------------------------------------


def _single_section_redirect(db: Session, project: Project, suffix: str = ""):
    sections = versions.list_sections(db, project)
    if not sections:
        sections = [versions.ensure_default_section(db, project)]
    if len(sections) == 1 and sections[0].active_version is not None:
        section = sections[0]
        return redirect(
            _version_url(project.id, section.id, section.active_version.version_no) + suffix
        )
    return redirect(_project_url(project.id))


@router.get("/estimate")
def estimate_legacy(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        flash(request, "Проект не найден.", "danger")
        return redirect("/projects")
    return _single_section_redirect(db, project)


@router.get("/estimate/add")
def estimate_add_legacy(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    return _single_section_redirect(db, project, "/add")


# --- Площадки ------------------------------------------------------------------


@router.post("/sections/create", dependencies=[Depends(verify_csrf)])
def section_create(
    request: Request,
    project_id: int,
    name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project = get_project(db, project_id)
    if project is None:
        return redirect("/projects")
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(_project_url(project_id))
    try:
        section = versions.create_section(db, project, name, user=user)
    except service.EstimateError as exc:
        flash(request, str(exc), "danger")
        return redirect(_project_url(project_id))
    audit_log(
        db,
        user,
        EventType.SECTION_MANAGE,
        f"Проект {project.number}: создана площадка «{section.name}» (смета {section.number})",
        object_type="project",
        object_id=project.id,
    )
    flash(request, f"Площадка «{section.name}» создана.", "success")
    return redirect(_version_url(project_id, section.id, 1))


@router.post("/sections/{section_id}/rename", dependencies=[Depends(verify_csrf)])
def section_rename(
    request: Request,
    project_id: int,
    section_id: int,
    name: str = Form(""),
    back: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None:
        return redirect(_project_url(project_id))
    target = (
        back if back and back.startswith(f"/projects/{project_id}/") else _project_url(project_id)
    )
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(target)
    old = section.name
    try:
        versions.rename_section(db, section, name)
    except service.EstimateError as exc:
        flash(request, str(exc), "danger")
        return redirect(target)
    audit_log(
        db,
        user,
        EventType.SECTION_MANAGE,
        f"Проект {project.number}: площадка «{old}» переименована в «{section.name}»",
        object_type="project",
        object_id=project.id,
    )
    flash(request, "Площадка переименована.", "success")
    return redirect(target)


@router.post("/sections/{section_id}/move", dependencies=[Depends(verify_csrf)])
def section_move(
    request: Request,
    project_id: int,
    section_id: int,
    direction: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, section = _load_section(db, project_id, section_id)
    if project is not None and section is not None and not project.is_archived:
        versions.move_section(db, project, section, 1 if direction > 0 else -1)
    return redirect(_project_url(project_id))


@router.post("/sections/{section_id}/delete", dependencies=[Depends(verify_csrf)])
def section_delete(
    request: Request,
    project_id: int,
    section_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None:
        return redirect(_project_url(project_id))
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(_project_url(project_id))
    name = section.name
    try:
        versions.delete_section(db, project, section)
    except service.EstimateError as exc:
        flash(request, str(exc), "danger")
        return redirect(_project_url(project_id))
    audit_log(
        db,
        user,
        EventType.SECTION_MANAGE,
        f"Проект {project.number}: удалена площадка «{name}»",
        object_type="project",
        object_id=project.id,
    )
    flash(request, f"Площадка «{name}» удалена.", "info")
    return redirect(_project_url(project_id))


@router.get("/sections/{section_id}/estimate")
def section_estimate(
    request: Request,
    project_id: int,
    section_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Смета площадки → её активная версия."""
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None or section.active_version is None:
        flash(request, "Площадка не найдена.", "warning")
        return redirect(_project_url(project_id))
    return redirect(_version_url(project_id, section_id, section.active_version.version_no))


# --- Версии ------------------------------------------------------------------


@router.post("/sections/{section_id}/estimate/versions/create", dependencies=[Depends(verify_csrf)])
def version_create(
    request: Request,
    project_id: int,
    section_id: int,
    source_version_no: str | None = Form(None),
    label: str | None = Form(None),
    activate: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None:
        return redirect(_project_url(project_id))
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(f"/projects/{project_id}/sections/{section_id}/estimate")
    source = None
    source_no = _opt_id(source_version_no)
    if source_no is not None:
        source = versions.get_version(db, section, source_no)
    version = versions.create_version(
        db, project, section, source=source, label=label, activate=activate is not None, user=user
    )
    origin = f"копия v{source.version_no}" if source else "пустая"
    audit_log(
        db,
        user,
        EventType.ESTIMATE_VERSION,
        f"Смета {version.display_number} (площадка «{section.name}»): создана версия ({origin})"
        + (", сделана активной" if version.is_active else ""),
        object_type="estimate",
        object_id=version.id,
    )
    flash(
        request,
        f"Создана версия v{version.version_no}"
        + (" — она активна." if version.is_active else " (неактивная)."),
        "success",
    )
    return redirect(_version_url(project_id, section_id, version.version_no))


@router.post(
    "/sections/{section_id}/estimate/versions/activate", dependencies=[Depends(verify_csrf)]
)
def version_activate(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int = Form(...),
    back_version_no: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    project, section = _load_section(db, project_id, section_id)
    if project is None or section is None:
        return redirect(_project_url(project_id))
    version = versions.get_version(db, section, version_no)
    back_no = _opt_id(back_version_no) or version_no
    if version is None:
        return redirect(_version_url(project_id, section_id, back_no))
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(_version_url(project_id, section_id, back_no))
    if version.is_active:
        flash(request, f"Версия v{version_no} уже активна.", "info")
        return redirect(_version_url(project_id, section_id, back_no))
    versions.activate_version(db, project, version)
    audit_log(
        db,
        user,
        EventType.ESTIMATE_VERSION,
        f"Смета {version.display_number} (площадка «{section.name}»): версия сделана активной",
        object_type="estimate",
        object_id=version.id,
    )
    flash(request, f"Активная версия — v{version_no}. Бронь пересчитана.", "success")
    return redirect(_version_url(project_id, section_id, version_no))


@router.get("/sections/{section_id}/estimate/v/{version_no}")
def version_page(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        flash(request, "Версия сметы не найдена.", "warning")
        return redirect(_project_url(project_id))
    project, section, estimate = loaded
    from app.documents import builder

    previews = {
        v.id: versions.preview_activation(db, project, v)
        for v in section.versions
        if not v.is_active and not project.is_archived
    }
    totals_by_version = {v.id: service.totals(db, v, project) for v in section.versions}
    return render(
        request,
        "estimates/estimate.html",
        {
            "page_title": f"Смета {estimate.display_number} · {section.name}",
            "project": project,
            "section": section,
            "estimate": estimate,
            "versions": section.versions,
            "sections_count": len(versions.list_sections(db, project)),
            "previews": previews,
            "totals_by_version": totals_by_version,
            "base": _version_url(project_id, section_id, version_no),
            "groups": service.grouped_lines(estimate),
            "totals": service.totals(db, estimate, project),
            "doc_statuses": builder.estimate_status(db, project, estimate),
            "editable": not project.is_archived and not estimate.is_locked,
        },
        db=db,
        user=user,
    )


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/lock", dependencies=[Depends(verify_csrf)]
)
def version_lock(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, section, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(base)
    versions.lock_version(db, estimate, user)
    audit_log(
        db,
        user,
        EventType.ESTIMATE_VERSION,
        f"Смета {estimate.display_number} (площадка «{section.name}»): версия заморожена",
        object_type="estimate",
        object_id=estimate.id,
    )
    flash(request, f"Версия v{version_no} заморожена.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/unlock", dependencies=[Depends(verify_csrf)]
)
def version_unlock(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, section, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if project.is_archived:
        flash(request, "Архивный проект только для просмотра.", "info")
        return redirect(base)
    versions.unlock_version(db, estimate)
    audit_log(
        db,
        user,
        EventType.ESTIMATE_VERSION,
        f"Смета {estimate.display_number} (площадка «{section.name}»): версия разморожена",
        object_type="estimate",
        object_id=estimate.id,
    )
    flash(request, f"Версия v{version_no} разморожена.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/label", dependencies=[Depends(verify_csrf)]
)
def version_label(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    label: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    versions.set_version_label(db, estimate, label)
    flash(request, "Комментарий версии сохранён.", "success")
    return redirect(base)


# --- Строки версии -------------------------------------------------------------


@router.get("/sections/{section_id}/estimate/v/{version_no}/add")
def add_picker(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    q: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    only: str | None = None,
    barcode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, section, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)

    filters = eq_service.ModelFilters(
        query=q,
        category_id=_opt_id(category_id),
        subcategory_id=_opt_id(subcategory_id),
        archived=False,
    )
    kits = kit_service.list_kits(db)
    kit_in_estimate = {ln.kit_id for ln in estimate.lines if ln.kit_id is not None}
    model_in_estimate: dict[int, int] = {}
    for ln in estimate.lines:
        if ln.model_id is not None:
            model_in_estimate[ln.model_id] = model_in_estimate.get(ln.model_id, 0) + ln.quantity
    # Сколько модели в активных сметах всех площадок проекта (бронь проекта).
    model_in_project: dict[int, int] = {}
    for active in service.active_versions(db, project):
        for ln in active.lines:
            if ln.model_id is not None and not ln.is_custom:
                model_in_project[ln.model_id] = model_in_project.get(ln.model_id, 0) + ln.quantity

    scanned_model_id = None
    if barcode:
        item = item_service.find_by_barcode(db, barcode)
        if item is None:
            flash(request, f"Штрих-код «{barcode.strip()}» не найден.", "warning")
            models = eq_service.list_models(db, filters)
        else:
            models = [item.model]
            kits = []
            scanned_model_id = item.model_id
    else:
        models = eq_service.list_models(db, filters)

    only_added = only == "added"
    if only_added:
        models = [m for m in models if m.id in model_in_estimate]
        kits = [k for k in kits if k.id in kit_in_estimate]

    availability = {}
    kit_availability = {}
    if project.start_date and project.end_date:
        for m in models:
            availability[m.id] = compute_availability(
                db, m, project.start_date, project.end_date, exclude_project_id=project.id
            )
        for k in kits:
            kit_availability[k.id] = compute_kit_availability(
                db, k.id, project.start_date, project.end_date, exclude_project_id=project.id
            )

    return render(
        request,
        "estimates/add.html",
        {
            "page_title": "Добавить оборудование",
            "project": project,
            "section": section,
            "estimate": estimate,
            "base": base,
            "multi_section": len(versions.list_sections(db, project)) > 1,
            "models": models,
            "kits": kits,
            "kit_in_estimate": kit_in_estimate,
            "model_in_estimate": model_in_estimate,
            "model_in_project": model_in_project,
            "only_added": only_added,
            "availability": availability,
            "kit_availability": kit_availability,
            "categories": cat_service.list_categories(db),
            "filters": filters,
            "q": q or "",
            "scanned_model_id": scanned_model_id,
            "AccountingType": AccountingType,
        },
        db=db,
        user=user,
    )


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/add", dependencies=[Depends(verify_csrf)]
)
async def add_submit(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    mode: str = Form("merge"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)

    form = await request.form()
    merge = mode != "separate"
    added = 0
    errors: list[str] = []
    for key in form:
        if key.startswith("select_"):
            model_id = int(key.split("_", 1)[1])
            model = eq_service.get_model(db, model_id)
            if model is None:
                continue
            qty = _int(form.get(f"qty_{model_id}"), 1)
            disc = _pct(form.get(f"disc_{model_id}"))
            service.add_model(db, estimate, project, model, qty, merge=merge, discount_percent=disc)
            added += 1
        elif key.startswith("selectkit_"):
            kit_id = int(key.split("_", 1)[1])
            kit = kit_service.get_kit(db, kit_id)
            if kit is None:
                continue
            try:
                if service.add_kit_line(db, estimate, project, kit) is not None:
                    added += 1
            except service.EstimateError as exc:
                errors.append(str(exc))
    if added:
        _audit(db, user, estimate, f"добавлено позиций — {added}")
    for err in errors:
        flash(request, err, "danger")
    flash(request, f"Добавлено позиций: {added}.", "success" if added else "warning")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/custom", dependencies=[Depends(verify_csrf)]
)
def add_custom(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    name: str = Form(...),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    coefficient: str = Form("1"),
    discount_percent: str = Form("0"),
    comment: str | None = Form(None),
    skip_packing: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    service.add_custom_line(
        db,
        estimate,
        project,
        CustomLineInput(
            name=name,
            quantity=_int(quantity, 1),
            unit_price=_dec(unit_price),
            coefficient=_dec(coefficient, "1"),
            discount_percent=_pct(discount_percent),
            comment=(comment.strip() if comment else None),
            add_to_packing=skip_packing is None,
        ),
    )
    _audit(db, user, estimate, f"добавлена произвольная строка «{name.strip()}»")
    flash(request, "Произвольная строка добавлена.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/lines/{line_id}",
    dependencies=[Depends(verify_csrf)],
)
def update_line(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    line_id: int,
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    coefficient: str = Form("1"),
    discount_percent: str = Form("0"),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        service.update_line(
            db,
            line,
            LineUpdate(
                quantity=_int(quantity, 1),
                unit_price=_dec(unit_price),
                coefficient=_dec(coefficient, "1"),
                discount_percent=_pct(discount_percent),
                comment=(comment.strip() if comment else None),
            ),
        )
        if not line.is_custom:
            service.sync_reservations(db, project)
        _audit(db, user, estimate, f"изменена строка «{line.name}»")
        flash(request, "Строка обновлена.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/lines/{line_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_line(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        was_warehouse = not line.is_custom
        line_name = line.name
        service.delete_line(db, line)
        if was_warehouse:
            service.sync_reservations(db, project)
        _audit(db, user, estimate, f"удалена строка «{line_name}»")
        flash(request, "Строка удалена.", "info")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/lines/{line_id}/move",
    dependencies=[Depends(verify_csrf)],
)
def move_line(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    line_id: int,
    direction: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        service.move_line(db, estimate, line, 1 if direction > 0 else -1)
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/lines/{line_id}/reset-coefficient",
    dependencies=[Depends(verify_csrf)],
)
def reset_line_coefficient(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    line_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    line = next((ln for ln in estimate.lines if ln.id == line_id), None)
    if line is not None:
        service.reset_line_coefficient(db, line)
        _audit(db, user, estimate, f"строке «{line.name}» возвращён коэффициент сметы")
        flash(request, "Коэффициент строки сброшен к коэффициенту сметы.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/discount", dependencies=[Depends(verify_csrf)]
)
def set_discount(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    discount_percent: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    service.set_discount(db, estimate, _dec(discount_percent))
    _audit(db, user, estimate, f"скидка {estimate.discount_percent}%")
    flash(request, "Скидка обновлена.", "success")
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/coefficient",
    dependencies=[Depends(verify_csrf)],
)
def set_coefficient(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    rental_coefficient: str = Form("1"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Коэффициент сметы: применяется ко всем строкам без ручной корректировки."""
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    try:
        changed = service.set_coefficient(db, estimate, _dec(rental_coefficient, "0"))
    except service.EstimateError as exc:
        flash(request, str(exc), "danger")
        return redirect(base)
    _audit(
        db,
        user,
        estimate,
        f"коэффициент сметы {estimate.rental_coefficient} (обновлено строк — {changed})",
    )
    flash(
        request,
        f"Коэффициент сметы {estimate.rental_coefficient} применён (строк обновлено: {changed}).",
        "success",
    )
    return redirect(base)


@router.post(
    "/sections/{section_id}/estimate/v/{version_no}/hide-prices",
    dependencies=[Depends(verify_csrf)],
)
def set_hide_prices(
    request: Request,
    project_id: int,
    section_id: int,
    version_no: int,
    hide_prices: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """«Скрыть стоимость оборудования» — только для документов (PDF)."""
    loaded = _load_version(db, project_id, section_id, version_no)
    if loaded is None:
        return redirect(_project_url(project_id))
    project, _, estimate = loaded
    base = _version_url(project_id, section_id, version_no)
    if _deny_edit(request, project, estimate):
        return redirect(base)
    hide = hide_prices is not None
    service.set_hide_prices(db, estimate, hide)
    _audit(
        db,
        user,
        estimate,
        f"стоимость оборудования в документах {'скрыта' if hide else 'показана'}",
    )
    flash(
        request,
        "В документах стоимость позиций будет скрыта."
        if hide
        else "В документах стоимость позиций будет показана.",
        "success",
    )
    return redirect(base)
