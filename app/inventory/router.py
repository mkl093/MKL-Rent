"""Маршруты раздела «Склад» (ТЗ §6–§12, §21, §25)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.enums import AccountingType, ItemStatus, KitWeightMode, PackingType
from app.inventory.schemas import (
    EquipmentItemInput,
    EquipmentModelCreate,
    EquipmentModelUpdate,
    KitInput,
    PackingRuleInput,
)
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.projects.availability import compute_availability, compute_planboard, occupancy_detail
from app.templating import flash
from app.utils.images import ImageError, delete_photo, save_model_photo

router = APIRouter(prefix="/inventory", tags=["inventory"])


# --- Парсинг полей формы ------------------------------------------------


def _dec(value: str | None, default: str = "0") -> Decimal:
    try:
        return Decimal((value or default).replace(",", ".").strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float((value or "").replace(",", ".").strip()))
    except (ValueError, AttributeError):
        return default


def _str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _opt_id(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _kit_input(
    name: str, description: str | None, weight_mode: str | None, weight_value: str | None
) -> KitInput:
    """Собрать данные комплекта из формы (вес — необязателен)."""
    try:
        mode = KitWeightMode(weight_mode or "")
    except ValueError:
        mode = KitWeightMode.CONTENT
    raw = _str(weight_value)
    return KitInput(
        name=name,
        description=_str(description),
        weight_mode=mode,
        weight_value=_dec(raw) if raw else None,
    )


def _date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _today() -> date:
    from app.database import utcnow
    from app.utils.timezone import to_local

    return to_local(utcnow()).date()


def _packing_from_form(
    has_packing: str | None,
    packing_type: str | None,
    empty_weight_kg: str | None,
    p_length: str | None,
    p_width: str | None,
    p_height: str | None,
    capacity: str | None,
) -> PackingRuleInput | None:
    if not has_packing:
        return None
    return PackingRuleInput(
        packing_type=PackingType(packing_type or "case"),
        empty_weight_kg=_dec(empty_weight_kg),
        length_mm=_int(p_length),
        width_mm=_int(p_width),
        height_mm=_int(p_height),
        capacity=max(1, _int(capacity, 1)),
    )


# --- Список склада (ТЗ §25) ---------------------------------------------


@router.get("")
def index(
    request: Request,
    q: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    accounting_type: str | None = None,
    has_packing: str | None = None,
    archived: int = 0,
    sort: str | None = None,
    view: str | None = None,
    avail_start: str | None = None,
    avail_end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    # Последний выбор сортировки запоминается (ТЗ §25).
    if sort:
        request.session["inventory_sort"] = sort
    sort = request.session.get("inventory_sort", "category")

    # Режим отображения: список или дерево (запоминается).
    if view in ("list", "tree"):
        request.session["inventory_view"] = view
    view = request.session.get("inventory_view", "list")

    # Период проверки доступности запоминается в сессии (ТЗ §15).
    if avail_start is not None or avail_end is not None:
        request.session["inventory_avail"] = {"start": avail_start or "", "end": avail_end or ""}
    saved = request.session.get("inventory_avail", {})
    start = _date(saved.get("start"))
    end = _date(saved.get("end"))
    if start and end and start > end:
        start, end = end, start

    filters = eq_service.ModelFilters(
        query=q,
        category_id=_opt_id(category_id),
        subcategory_id=_opt_id(subcategory_id),
        accounting_type=AccountingType(accounting_type) if accounting_type else None,
        has_packing=(None if has_packing in (None, "") else has_packing == "1"),
        archived=bool(archived),
        sort=sort,
    )
    models = eq_service.list_models(db, filters)
    stock = {m.id: eq_service.stock_quantity(db, m) for m in models}

    # Три состояния исправного оборудования (ТЗ §15): доступно (зелёный),
    # зарезервировано (жёлтый), в работе (красный). Считаем на выбранный период,
    # а без него — на сегодня (текущее состояние склада).
    avail_is_period = bool(start and end)
    ref_start, ref_end = (start, end) if avail_is_period else (_today(), _today())
    availability = {m.id: compute_availability(db, m, ref_start, ref_end) for m in models}

    # Перечень занимающих проектов (бронь + в работе) — только там, где занятость
    # есть; раскрывается по клику на цифру в списке.
    occupancy = {
        m.id: occupancy_detail(db, m.id, ref_start, ref_end)
        for m in models
        if availability[m.id].reserved_other > 0
    }

    categories = cat_service.list_categories(db)
    tree = _build_tree(categories, models) if view == "tree" else None

    return render(
        request,
        "inventory/list.html",
        {
            "page_title": "Склад",
            "models": models,
            "stock": stock,
            "availability": availability,
            "occupancy": occupancy,
            "avail_is_period": avail_is_period,
            "avail_start": saved.get("start", ""),
            "avail_end": saved.get("end", ""),
            "categories": categories,
            "filters": filters,
            "AccountingType": AccountingType,
            "q": q or "",
            "view": view,
            "tree": tree,
        },
        db=db,
        user=user,
    )


# --- Planboard: календарь занятости склада по дням (ТЗ §15.2) -----------

_PLANBOARD_SPANS = (7, 14, 31)


@router.get("/planboard")
def planboard(
    request: Request,
    q: str | None = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    start: str | None = None,
    span: int = 31,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if span not in _PLANBOARD_SPANS:
        span = 31
    start_date = _date(start) or _today()
    end_date = start_date + timedelta(days=span - 1)

    filters = eq_service.ModelFilters(
        query=q,
        category_id=_opt_id(category_id),
        subcategory_id=_opt_id(subcategory_id),
        archived=False,
        sort="category",
    )
    models = eq_service.list_models(db, filters)
    days, rows = compute_planboard(db, [m.id for m in models], start_date, end_date)

    return render(
        request,
        "inventory/planboard.html",
        {
            "page_title": "Календарь склада",
            "models": models,
            "days": days,
            "rows": rows,
            "start_date": start_date,
            "end_date": end_date,
            "span": span,
            "spans": _PLANBOARD_SPANS,
            "prev_start": start_date - timedelta(days=span),
            "next_start": start_date + timedelta(days=span),
            "today": _today(),
            "categories": cat_service.list_categories(db),
            "filters": filters,
            "q": q or "",
        },
        db=db,
        user=user,
    )


@router.get("/planboard/cell")
def planboard_cell(
    request: Request,
    model_id: int,
    d: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """HTMX-фрагмент: проекты, занявшие модель в конкретный день."""
    model = eq_service.get_model(db, model_id)
    day = _date(d)
    entries = occupancy_detail(db, model_id, day, day) if (model and day) else []
    return render(
        request,
        "inventory/_planboard_cell.html",
        {"model": model, "day": day, "entries": entries},
        db=db,
        user=user,
    )


def _build_tree(categories, models):
    """Сгруппировать модели в дерево Категория → Подкатегория → Модели (ТЗ §6)."""
    by_cat: dict[int, dict[int | None, list]] = {}
    for m in models:
        by_cat.setdefault(m.category_id, {}).setdefault(m.subcategory_id, []).append(m)

    tree = []
    for cat in categories:
        groups = by_cat.get(cat.id)
        if not groups:
            continue
        subgroups = [
            {"name": sub.name, "models": groups[sub.id]}
            for sub in cat.subcategories
            if sub.id in groups
        ]
        node = {"name": cat.name, "subgroups": subgroups, "direct": groups.get(None, [])}
        tree.append(node)
    return tree


# --- Глобальный поиск по штрих-коду (ТЗ §21.4) --------------------------


@router.get("/scan")
def scan_form(
    request: Request,
    barcode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    found = item_service.find_by_barcode(db, barcode) if barcode else None
    if barcode and found:
        return redirect(f"/inventory/items/{found.id}")
    return render(
        request,
        "inventory/scan.html",
        {"page_title": "Поиск по штрих-коду", "barcode": barcode, "not_found": bool(barcode)},
        db=db,
        user=user,
    )


# --- Комплекты (структура «Комплект») -----------------------------------


@router.get("/kits")
def kits_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kits = kit_service.list_kits(db)
    counts = {k.id: kit_service.item_count(db, k.id) for k in kits}
    return render(
        request,
        "inventory/kits.html",
        {
            "page_title": "Комплекты",
            "kits": kits,
            "counts": counts,
            "KitWeightMode": KitWeightMode,
        },
        db=db,
        user=user,
    )


@router.post("/kits", dependencies=[Depends(verify_csrf)])
def kit_create(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    weight_mode: str | None = Form(None),
    weight_value: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if not _str(name):
        flash(request, "Укажите название комплекта.", "danger")
        return redirect("/inventory/kits")
    kit = kit_service.create_kit(db, _kit_input(name, description, weight_mode, weight_value))
    audit_log(
        db,
        user,
        EventType.KIT_MANAGE,
        f"Создан комплект «{kit.name}»",
        object_type="kit",
        object_id=kit.id,
    )
    flash(request, "Комплект создан.", "success")
    return redirect(f"/inventory/kits/{kit.id}")


@router.get("/kits/{kit_id}")
def kit_detail(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit is None:
        flash(request, "Комплект не найден.", "danger")
        return redirect("/inventory/kits")
    return render(
        request,
        "inventory/kit_detail.html",
        {
            "page_title": kit.name,
            "kit": kit,
            "groups": kit_service.content_groups(kit),
            "content_weight": kit_service.content_weight(kit),
            "total_weight": kit_service.total_weight(kit),
            "ItemStatus": ItemStatus,
            "KitWeightMode": KitWeightMode,
        },
        db=db,
        user=user,
    )


@router.post("/kits/{kit_id}", dependencies=[Depends(verify_csrf)])
def kit_update(
    request: Request,
    kit_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    weight_mode: str | None = Form(None),
    weight_value: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit and _str(name):
        kit_service.update_kit(db, kit, _kit_input(name, description, weight_mode, weight_value))
        flash(request, "Комплект сохранён.", "success")
    return redirect(f"/inventory/kits/{kit_id}")


@router.post("/kits/{kit_id}/delete", dependencies=[Depends(verify_csrf)])
def kit_delete(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit:
        try:
            name = kit.name
            kit_service.delete_kit(db, kit)
            audit_log(
                db,
                user,
                EventType.KIT_MANAGE,
                f"Удалён комплект «{name}»",
                object_type="kit",
                object_id=kit_id,
            )
            flash(request, "Комплект удалён (единицы возвращены на склад).", "success")
            return redirect("/inventory/kits")
        except kit_service.InUse as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/kits/{kit_id}")


@router.get("/kits/{kit_id}/add")
def kit_add_picker(
    request: Request,
    kit_id: int,
    q: str | None = None,
    category_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit is None:
        return redirect("/inventory/kits")
    items = kit_service.free_items(db, query=_str(q), category_id=_opt_id(category_id))
    return render(
        request,
        "inventory/kit_add.html",
        {
            "page_title": f"Наполнение комплекта: {kit.name}",
            "kit": kit,
            "items": items,
            "categories": cat_service.list_categories(db),
            "category_id": _opt_id(category_id),
            "q": q or "",
            "ItemStatus": ItemStatus,
        },
        db=db,
        user=user,
    )


@router.post("/kits/{kit_id}/add", dependencies=[Depends(verify_csrf)])
async def kit_add_submit(
    request: Request,
    kit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit is None:
        return redirect("/inventory/kits")
    form = await request.form()
    item_ids = [int(key.split("_", 1)[1]) for key in form if key.startswith("select_")]
    added = kit_service.add_items(db, kit, item_ids)
    if added:
        audit_log(
            db,
            user,
            EventType.KIT_MANAGE,
            f"Комплект «{kit.name}»: добавлено единиц — {added}",
            object_type="kit",
            object_id=kit.id,
        )
    flash(request, f"Добавлено единиц: {added}.", "success" if added else "warning")
    return redirect(f"/inventory/kits/{kit_id}")


@router.post("/kits/{kit_id}/items/{item_id}/remove", dependencies=[Depends(verify_csrf)])
def kit_remove_item(
    request: Request,
    kit_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    kit = kit_service.get_kit(db, kit_id)
    if kit:
        item = kit_service.remove_item(db, kit, item_id)
        if item is not None:
            audit_log(
                db,
                user,
                EventType.KIT_MANAGE,
                f"Комплект «{kit.name}»: единица возвращена на склад",
                object_type="kit",
                object_id=kit.id,
            )
            flash(request, "Единица возвращена в свободный сток.", "info")
    return redirect(f"/inventory/kits/{kit_id}")


# --- Категории (ТЗ §6.1) ------------------------------------------------


@router.get("/categories")
def categories_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "inventory/categories.html",
        {"page_title": "Категории", "categories": cat_service.list_categories(db)},
        db=db,
        user=user,
    )


@router.post("/categories", dependencies=[Depends(verify_csrf)])
def category_create(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if _str(name):
        cat_service.create_category(db, name)
        flash(request, "Категория добавлена.", "success")
    return redirect("/inventory/categories")


@router.post("/categories/{category_id}/subcategories", dependencies=[Depends(verify_csrf)])
def subcategory_create(
    request: Request,
    category_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    category = db.get(cat_service.Category, category_id)
    if category and _str(name):
        cat_service.create_subcategory(db, category, name)
        flash(request, "Подкатегория добавлена.", "success")
    return redirect("/inventory/categories")


@router.post("/categories/{category_id}/delete", dependencies=[Depends(verify_csrf)])
def category_delete(
    request: Request,
    category_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    category = db.get(cat_service.Category, category_id)
    if category:
        try:
            cat_service.delete_category(db, category)
            flash(request, "Категория удалена.", "success")
        except cat_service.InUse as exc:
            flash(request, str(exc), "danger")
    return redirect("/inventory/categories")


@router.post("/subcategories/{subcategory_id}/delete", dependencies=[Depends(verify_csrf)])
def subcategory_delete(
    request: Request,
    subcategory_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    sub = db.get(cat_service.Subcategory, subcategory_id)
    if sub:
        try:
            cat_service.delete_subcategory(db, sub)
            flash(request, "Подкатегория удалена.", "success")
        except cat_service.InUse as exc:
            flash(request, str(exc), "danger")
    return redirect("/inventory/categories")


# --- Модели оборудования (ТЗ §7) ----------------------------------------


@router.get("/models/new")
def model_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(
        request,
        "inventory/model_form.html",
        {
            "page_title": "Новая модель",
            "model": None,
            "categories": cat_service.list_categories(db),
            "AccountingType": AccountingType,
            "PackingType": PackingType,
        },
        db=db,
        user=user,
    )


@router.post("/models", dependencies=[Depends(verify_csrf)])
async def model_create(
    request: Request,
    name: str = Form(...),
    category_id: int = Form(...),
    accounting_type: str = Form(...),
    weight_kg: str = Form("0"),
    length_mm: str = Form("0"),
    width_mm: str = Form("0"),
    height_mm: str = Form("0"),
    base_price_eur: str = Form("0"),
    total_quantity: str = Form("0"),
    subcategory_id: str | None = Form(None),
    manufacturer: str | None = Form(None),
    internal_sku: str | None = Form(None),
    description: str | None = Form(None),
    note: str | None = Form(None),
    has_packing: str | None = Form(None),
    packing_type: str | None = Form(None),
    empty_weight_kg: str | None = Form(None),
    p_length: str | None = Form(None),
    p_width: str | None = Form(None),
    p_height: str | None = Form(None),
    capacity: str | None = Form(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    data = EquipmentModelCreate(
        category_id=category_id,
        name=name,
        accounting_type=AccountingType(accounting_type),
        weight_kg=_dec(weight_kg),
        length_mm=_int(length_mm),
        width_mm=_int(width_mm),
        height_mm=_int(height_mm),
        base_price_eur=_dec(base_price_eur),
        total_quantity=_int(total_quantity),
        subcategory_id=_opt_id(subcategory_id),
        manufacturer=_str(manufacturer),
        internal_sku=_str(internal_sku),
        description=_str(description),
        note=_str(note),
        packing=_packing_from_form(
            has_packing, packing_type, empty_weight_kg, p_length, p_width, p_height, capacity
        ),
    )
    model = eq_service.create_model(db, data)
    await _maybe_save_photo(request, db, model, photo)
    audit_log(
        db,
        user,
        EventType.INVENTORY_MODEL,
        f"Создана модель «{model.name}»",
        object_type="equipment_model",
        object_id=model.id,
    )
    flash(request, "Модель создана.", "success")
    return redirect(f"/inventory/models/{model.id}")


@router.get("/models/{model_id}")
def model_detail(
    request: Request,
    model_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model is None:
        flash(request, "Модель не найдена.", "danger")
        return redirect("/inventory")
    # Поединичный учёт для всех типов: показываем единицы и разбивку по статусам.
    context = {
        "page_title": model.name,
        "model": model,
        "stock": eq_service.stock_quantity(db, model),
        "in_kits": eq_service.kit_count(db, model.id),
        "active": eq_service.active_count(db, model.id),
        "items": item_service.list_items(db, model.id),
        "status_counts": eq_service.serial_status_counts(db, model.id),
        "ItemStatus": ItemStatus,
    }
    # У количественной модели дополнительно — быстрый остаток и его история (ТЗ §10).
    if not model.is_serial:
        context["quantity_history"] = eq_service.quantity_history(db, model)
    return render(request, "inventory/model_detail.html", context, db=db, user=user)


@router.get("/models/{model_id}/edit")
def model_edit(
    request: Request,
    model_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model is None:
        return redirect("/inventory")
    return render(
        request,
        "inventory/model_form.html",
        {
            "page_title": f"Редактирование: {model.name}",
            "model": model,
            "categories": cat_service.list_categories(db),
            "AccountingType": AccountingType,
            "PackingType": PackingType,
        },
        db=db,
        user=user,
    )


@router.post("/models/{model_id}", dependencies=[Depends(verify_csrf)])
async def model_update(
    request: Request,
    model_id: int,
    name: str = Form(...),
    category_id: int = Form(...),
    weight_kg: str = Form("0"),
    length_mm: str = Form("0"),
    width_mm: str = Form("0"),
    height_mm: str = Form("0"),
    base_price_eur: str = Form("0"),
    total_quantity: str = Form("0"),
    subcategory_id: str | None = Form(None),
    manufacturer: str | None = Form(None),
    internal_sku: str | None = Form(None),
    description: str | None = Form(None),
    note: str | None = Form(None),
    has_packing: str | None = Form(None),
    packing_type: str | None = Form(None),
    empty_weight_kg: str | None = Form(None),
    p_length: str | None = Form(None),
    p_width: str | None = Form(None),
    p_height: str | None = Form(None),
    capacity: str | None = Form(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model is None:
        return redirect("/inventory")
    data = EquipmentModelUpdate(
        category_id=category_id,
        name=name,
        weight_kg=_dec(weight_kg),
        length_mm=_int(length_mm),
        width_mm=_int(width_mm),
        height_mm=_int(height_mm),
        base_price_eur=_dec(base_price_eur),
        total_quantity=_int(total_quantity),
        subcategory_id=_opt_id(subcategory_id),
        manufacturer=_str(manufacturer),
        internal_sku=_str(internal_sku),
        description=_str(description),
        note=_str(note),
        packing=_packing_from_form(
            has_packing, packing_type, empty_weight_kg, p_length, p_width, p_height, capacity
        ),
    )
    eq_service.update_model(db, model, data)
    await _maybe_save_photo(request, db, model, photo)
    flash(request, "Модель сохранена.", "success")
    return redirect(f"/inventory/models/{model.id}")


@router.post("/models/{model_id}/archive", dependencies=[Depends(verify_csrf)])
def model_archive(
    request: Request,
    model_id: int,
    archived: int = Form(1),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model:
        eq_service.archive_model(db, model, bool(archived))
        audit_log(
            db,
            user,
            EventType.INVENTORY_MODEL,
            ("Архивирована" if archived else "Возвращена из архива") + f" модель «{model.name}»",
            object_type="equipment_model",
            object_id=model.id,
        )
        flash(
            request, "Модель архивирована." if archived else "Модель возвращена из архива.", "info"
        )
    return redirect(f"/inventory/models/{model_id}")


@router.post("/models/{model_id}/delete", dependencies=[Depends(verify_csrf)])
def model_delete(
    request: Request,
    model_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model:
        try:
            delete_photo(model.photo_path)
            eq_service.delete_model(db, model)
            flash(request, "Модель удалена.", "success")
            return redirect("/inventory")
        except cat_service.InUse as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/models/{model_id}")


@router.post("/models/{model_id}/photo/delete", dependencies=[Depends(verify_csrf)])
def model_photo_delete(
    request: Request,
    model_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model and model.photo_path:
        delete_photo(model.photo_path)
        model.photo_path = None
        db.commit()
        flash(request, "Фото удалено.", "info")
    return redirect(f"/inventory/models/{model_id}")


async def _maybe_save_photo(request: Request, db: Session, model, photo: UploadFile | None) -> None:
    if photo is None or not photo.filename:
        return
    raw = await photo.read()
    try:
        rel = save_model_photo(raw)
    except ImageError as exc:
        flash(request, f"Фото не загружено: {exc}", "warning")
        return
    delete_photo(model.photo_path)
    model.photo_path = rel
    db.commit()


# --- Количественный остаток (ТЗ §10) ------------------------------------


@router.post("/models/{model_id}/quantity", dependencies=[Depends(verify_csrf)])
def quantity_adjust(
    request: Request,
    model_id: int,
    new_quantity: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model:
        try:
            old_qty = model.total_quantity
            eq_service.adjust_quantity(db, model, _int(new_quantity), user.id, _str(comment))
            audit_log(
                db,
                user,
                EventType.INVENTORY_QTY,
                f"Остаток «{model.name}»",
                object_type="equipment_model",
                object_id=model.id,
                old_value=old_qty,
                new_value=model.total_quantity,
            )
            flash(request, "Остаток обновлён.", "success")
        except eq_service.InventoryError as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/models/{model_id}")


# --- Посерийные экземпляры (ТЗ §8.2, §9) --------------------------------


@router.post("/models/{model_id}/items", dependencies=[Depends(verify_csrf)])
def item_create(
    request: Request,
    model_id: int,
    barcode: str | None = Form(None),
    serial_number: str | None = Form(None),
    inventory_number: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    model = eq_service.get_model(db, model_id)
    if model:
        try:
            item_service.create_item(
                db,
                model,
                EquipmentItemInput(
                    barcode=_str(barcode),
                    serial_number=_str(serial_number),
                    inventory_number=_str(inventory_number),
                    comment=_str(comment),
                ),
                user.id,
            )
            flash(request, "Единица добавлена.", "success")
        except item_service.InventoryError as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/models/{model_id}")


@router.get("/items/{item_id}")
def item_detail(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    item = item_service.get_item(db, item_id)
    if item is None:
        flash(request, "Экземпляр не найден.", "danger")
        return redirect("/inventory")
    return render(
        request,
        "inventory/item_detail.html",
        {"page_title": item.barcode, "item": item, "ItemStatus": ItemStatus},
        db=db,
        user=user,
    )


@router.post("/items/{item_id}", dependencies=[Depends(verify_csrf)])
def item_update(
    request: Request,
    item_id: int,
    barcode: str | None = Form(None),
    serial_number: str | None = Form(None),
    inventory_number: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    item = item_service.get_item(db, item_id)
    if item:
        try:
            item_service.update_item(
                db,
                item,
                EquipmentItemInput(
                    barcode=_str(barcode),
                    serial_number=_str(serial_number),
                    inventory_number=_str(inventory_number),
                    comment=_str(comment),
                ),
            )
            flash(request, "Экземпляр сохранён.", "success")
        except item_service.InventoryError as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/items/{item_id}")


@router.post("/items/{item_id}/status", dependencies=[Depends(verify_csrf)])
def item_status(
    request: Request,
    item_id: int,
    new_status: str = Form(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    item = item_service.get_item(db, item_id)
    if item:
        old_status = item.status
        item_service.change_status(db, item, ItemStatus(new_status), user.id, _str(comment))
        audit_log(
            db,
            user,
            EventType.INVENTORY_ITEM_STATUS,
            f"Статус экземпляра {item.barcode}",
            object_type="equipment_item",
            object_id=item.id,
            old_value=old_status.label,
            new_value=item.status.label,
        )
        flash(request, "Статус изменён.", "success")
    return redirect(f"/inventory/items/{item_id}")


@router.post("/items/{item_id}/delete", dependencies=[Depends(verify_csrf)])
def item_delete(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    item = item_service.get_item(db, item_id)
    if item is None:
        return redirect("/inventory")
    model_id = item.model_id
    try:
        item_service.delete_item(db, item)
        flash(request, "Экземпляр удалён.", "success")
    except item_service.InUse as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/inventory/items/{item_id}")
    return redirect(f"/inventory/models/{model_id}")
