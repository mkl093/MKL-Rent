"""Сервис справочника аксессуаров и комплектации моделей.

Каталог: категория → аксессуар (один уровень). Аксессуары назначаются моделям
с количеством (EquipmentModelAccessory) и суммируются в packing-листе.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.inventory.models import (
    Accessory,
    AccessoryCategory,
    EquipmentModelAccessory,
)
from app.inventory.services.categories import InUse


def list_accessory_categories(db: Session) -> list[AccessoryCategory]:
    stmt = (
        select(AccessoryCategory)
        .options(selectinload(AccessoryCategory.accessories))
        .order_by(AccessoryCategory.sort_order, AccessoryCategory.name)
    )
    return list(db.execute(stmt).scalars().all())


def existing_ids(db: Session, ids: list[int]) -> set[int]:
    """Множество существующих id аксессуаров из переданного списка."""
    if not ids:
        return set()
    return set(
        db.execute(select(Accessory.id).where(Accessory.id.in_(ids))).scalars().all()
    )


# --- Категории ----------------------------------------------------------


def create_category(db: Session, name: str, sort_order: int = 0) -> AccessoryCategory:
    category = AccessoryCategory(name=name.strip(), sort_order=sort_order)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def rename_category(db: Session, category: AccessoryCategory, name: str) -> AccessoryCategory:
    category.name = name.strip()
    db.commit()
    return category


def delete_category(db: Session, category: AccessoryCategory) -> None:
    """Удалить категорию. Запрещено, если её аксессуары есть в комплектации моделей."""
    count = db.scalar(
        select(func.count())
        .select_from(EquipmentModelAccessory)
        .join(Accessory, Accessory.id == EquipmentModelAccessory.accessory_id)
        .where(Accessory.category_id == category.id)
    )
    if count:
        raise InUse("Категория используется в комплектации моделей")
    db.delete(category)
    db.commit()


# --- Аксессуары ---------------------------------------------------------


def create_accessory(
    db: Session, category: AccessoryCategory, name: str, sort_order: int = 0
) -> Accessory:
    accessory = Accessory(category_id=category.id, name=name.strip(), sort_order=sort_order)
    db.add(accessory)
    db.commit()
    db.refresh(accessory)
    return accessory


def rename_accessory(db: Session, accessory: Accessory, name: str) -> Accessory:
    accessory.name = name.strip()
    db.commit()
    return accessory


def delete_accessory(db: Session, accessory: Accessory) -> None:
    """Удалить аксессуар. Запрещено, если он есть в комплектации какой-либо модели."""
    count = db.scalar(
        select(func.count())
        .select_from(EquipmentModelAccessory)
        .where(EquipmentModelAccessory.accessory_id == accessory.id)
    )
    if count:
        raise InUse("Аксессуар используется в комплектации моделей")
    db.delete(accessory)
    db.commit()
