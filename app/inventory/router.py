"""Маршруты раздела «Склад» (ТЗ §6–§12, §21, §25)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.inventory.enums import AccountingType, ItemStatus, PackingType
from app.inventory.schemas import (
    EquipmentItemInput,
    EquipmentModelCreate,
    EquipmentModelUpdate,
    PackingRuleInput,
)
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
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
    category_id: int | None = None,
    subcategory_id: int | None = None,
    accounting_type: str | None = None,
    has_packing: str | None = None,
    archived: int = 0,
    sort: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    # Последний выбор сортировки запоминается (ТЗ §25).
    if sort:
        request.session["inventory_sort"] = sort
    sort = request.session.get("inventory_sort", "category")

    filters = eq_service.ModelFilters(
        query=q,
        category_id=category_id,
        subcategory_id=subcategory_id,
        accounting_type=AccountingType(accounting_type) if accounting_type else None,
        has_packing=(None if has_packing in (None, "") else has_packing == "1"),
        archived=bool(archived),
        sort=sort,
    )
    models = eq_service.list_models(db, filters)
    stock = {m.id: eq_service.stock_quantity(db, m) for m in models}
    return render(
        request,
        "inventory/list.html",
        {
            "page_title": "Склад",
            "models": models,
            "stock": stock,
            "categories": cat_service.list_categories(db),
            "filters": filters,
            "AccountingType": AccountingType,
            "q": q or "",
        },
        db=db,
        user=user,
    )


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
    context = {
        "page_title": model.name,
        "model": model,
        "stock": eq_service.stock_quantity(db, model),
        "ItemStatus": ItemStatus,
    }
    if model.is_serial:
        context["items"] = item_service.list_items(db, model.id)
        context["status_counts"] = eq_service.serial_status_counts(db, model.id)
    else:
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
            eq_service.adjust_quantity(db, model, _int(new_quantity), user.id, _str(comment))
            flash(request, "Остаток обновлён.", "success")
        except eq_service.InventoryError as exc:
            flash(request, str(exc), "danger")
    return redirect(f"/inventory/models/{model_id}")


# --- Посерийные экземпляры (ТЗ §8.2, §9) --------------------------------


@router.post("/models/{model_id}/items", dependencies=[Depends(verify_csrf)])
def item_create(
    request: Request,
    model_id: int,
    barcode: str = Form(...),
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
                    barcode=barcode,
                    serial_number=_str(serial_number),
                    inventory_number=_str(inventory_number),
                    comment=_str(comment),
                ),
                user.id,
            )
            flash(request, "Экземпляр добавлен.", "success")
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
    barcode: str = Form(...),
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
                    barcode=barcode,
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
        item_service.change_status(db, item, ItemStatus(new_status), user.id, _str(comment))
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
