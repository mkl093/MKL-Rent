"""Сервис моделей оборудования и количественных остатков (ТЗ §7, §10, §12)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import utcnow
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.models import (
    EquipmentItem,
    EquipmentModel,
    PackingRule,
    QuantityAdjustment,
)
from app.inventory.schemas import (
    EquipmentModelCreate,
    EquipmentModelUpdate,
    PackingRuleInput,
)
from app.inventory.services.categories import InUse, InventoryError


@dataclass
class ModelFilters:
    """Фильтры списка склада (ТЗ §25)."""

    query: str | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    accounting_type: AccountingType | None = None
    has_packing: bool | None = None
    archived: bool = False  # по умолчанию показываем активные
    sort: str = "category"  # category | name | manufacturer


def _apply_packing(model: EquipmentModel, data: PackingRuleInput | None) -> None:
    if data is None:
        model.packing = None
        return
    if model.packing is None:
        model.packing = PackingRule(model_id=model.id)
    model.packing.packing_type = data.packing_type
    model.packing.empty_weight_kg = data.empty_weight_kg
    model.packing.length_mm = data.length_mm
    model.packing.width_mm = data.width_mm
    model.packing.height_mm = data.height_mm
    model.packing.capacity = data.capacity


def create_model(db: Session, data: EquipmentModelCreate) -> EquipmentModel:
    model = EquipmentModel(
        category_id=data.category_id,
        name=data.name.strip(),
        accounting_type=data.accounting_type,
        weight_kg=data.weight_kg,
        length_mm=data.length_mm,
        width_mm=data.width_mm,
        height_mm=data.height_mm,
        base_price_eur=data.base_price_eur,
        # Количество хранится только у количественной модели (ТЗ §8.1).
        total_quantity=data.total_quantity
        if data.accounting_type == AccountingType.QUANTITY
        else 0,
        subcategory_id=data.subcategory_id,
        manufacturer=(data.manufacturer or None),
        internal_sku=(data.internal_sku or None),
        description=(data.description or None),
        note=(data.note or None),
    )
    _apply_packing(model, data.packing)
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def update_model(db: Session, model: EquipmentModel, data: EquipmentModelUpdate) -> EquipmentModel:
    """Обновить модель. Тип учёта не меняется (ТЗ §7.3, §40.1)."""
    model.category_id = data.category_id
    model.name = data.name.strip()
    model.weight_kg = data.weight_kg
    model.length_mm = data.length_mm
    model.width_mm = data.width_mm
    model.height_mm = data.height_mm
    model.base_price_eur = data.base_price_eur
    model.subcategory_id = data.subcategory_id
    model.manufacturer = data.manufacturer or None
    model.internal_sku = data.internal_sku or None
    model.description = data.description or None
    model.note = data.note or None
    # total_quantity редактируется отдельной операцией с историей (ТЗ §10),
    # но при первичном редактировании количественной модели допускаем синхронизацию.
    if model.accounting_type == AccountingType.QUANTITY:
        model.total_quantity = data.total_quantity
    _apply_packing(model, data.packing)
    db.commit()
    db.refresh(model)
    return model


def get_model(db: Session, model_id: int) -> EquipmentModel | None:
    stmt = (
        select(EquipmentModel)
        .options(
            selectinload(EquipmentModel.category),
            selectinload(EquipmentModel.subcategory),
            selectinload(EquipmentModel.packing),
        )
        .where(EquipmentModel.id == model_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def is_model_used(db: Session, model: EquipmentModel) -> bool:
    """Используется ли модель в проектах/сметах/packing.

    На Этапе 2 этих сущностей ещё нет — расширим на Этапах 3–5 (ТЗ §7.4).
    """
    return False


def archive_model(db: Session, model: EquipmentModel, archived: bool = True) -> None:
    model.is_archived = archived
    db.commit()


def delete_model(db: Session, model: EquipmentModel) -> None:
    """Удалить можно только неиспользованную модель; иначе — архивировать (ТЗ §7.4)."""
    if is_model_used(db, model):
        raise InUse("Модель используется — её можно только архивировать")
    db.delete(model)
    db.commit()


def adjust_quantity(
    db: Session,
    model: EquipmentModel,
    new_quantity: int,
    user_id: int | None,
    comment: str | None = None,
) -> QuantityAdjustment:
    """Изменить количественный остаток с записью истории в транзакции (ТЗ §10, §38)."""
    if model.accounting_type != AccountingType.QUANTITY:
        raise InventoryError("Остаток меняется только у количественной модели")
    if new_quantity < 0:
        raise InventoryError("Количество не может быть отрицательным")

    old = model.total_quantity
    adjustment = QuantityAdjustment(
        model_id=model.id,
        changed_at=utcnow(),
        user_id=user_id,
        old_quantity=old,
        new_quantity=new_quantity,
        delta=new_quantity - old,
        comment=(comment or None),
    )
    model.total_quantity = new_quantity
    db.add(adjustment)
    db.commit()
    db.refresh(adjustment)
    return adjustment


def quantity_history(db: Session, model: EquipmentModel) -> list[QuantityAdjustment]:
    stmt = (
        select(QuantityAdjustment)
        .where(QuantityAdjustment.model_id == model.id)
        .order_by(QuantityAdjustment.changed_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def serial_status_counts(db: Session, model_id: int) -> dict[ItemStatus, int]:
    """Количество экземпляров посерийной модели по статусам."""
    stmt = (
        select(EquipmentItem.status, func.count())
        .where(EquipmentItem.model_id == model_id)
        .group_by(EquipmentItem.status)
    )
    counts = {status: 0 for status in ItemStatus}
    for status, count in db.execute(stmt).all():
        counts[status] = count
    return counts


def stock_quantity(db: Session, model: EquipmentModel) -> int:
    """Общий остаток: поле для количественной, число экземпляров — для посерийной."""
    if model.accounting_type == AccountingType.QUANTITY:
        return model.total_quantity
    return (
        db.scalar(
            select(func.count())
            .select_from(EquipmentItem)
            .where(EquipmentItem.model_id == model.id)
        )
        or 0
    )


def list_models(db: Session, filters: ModelFilters) -> list[EquipmentModel]:
    """Список моделей с фильтрами и сортировкой (ТЗ §25).

    Сортировка по умолчанию: категория → название модели.
    """
    stmt = (
        select(EquipmentModel)
        .join(EquipmentModel.category)
        .options(
            selectinload(EquipmentModel.category),
            selectinload(EquipmentModel.subcategory),
            selectinload(EquipmentModel.packing),
        )
        .where(EquipmentModel.is_archived.is_(filters.archived))
    )

    if filters.query:
        like = f"%{filters.query.strip()}%"
        stmt = stmt.where(EquipmentModel.name.ilike(like) | EquipmentModel.manufacturer.ilike(like))
    if filters.category_id:
        stmt = stmt.where(EquipmentModel.category_id == filters.category_id)
    if filters.subcategory_id:
        stmt = stmt.where(EquipmentModel.subcategory_id == filters.subcategory_id)
    if filters.accounting_type:
        stmt = stmt.where(EquipmentModel.accounting_type == filters.accounting_type)
    if filters.has_packing is not None:
        if filters.has_packing:
            stmt = stmt.where(EquipmentModel.packing.has())
        else:
            stmt = stmt.where(~EquipmentModel.packing.has())

    from app.inventory.models import Category

    if filters.sort == "name":
        stmt = stmt.order_by(EquipmentModel.name)
    elif filters.sort == "manufacturer":
        stmt = stmt.order_by(EquipmentModel.manufacturer, EquipmentModel.name)
    else:  # category (по умолчанию): категория → название модели (ТЗ §25)
        stmt = stmt.order_by(Category.sort_order, Category.name, EquipmentModel.name)
    return list(db.execute(stmt).scalars().all())
