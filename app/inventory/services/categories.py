"""Сервис категорий и подкатегорий (ТЗ §6.1)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.inventory.models import Category, EquipmentModel, Subcategory


class InventoryError(Exception):
    """Базовая ошибка складского домена."""


class InUse(InventoryError):
    """Объект используется и не может быть удалён."""


def list_categories(db: Session) -> list[Category]:
    stmt = (
        select(Category)
        .options(selectinload(Category.subcategories))
        .order_by(Category.sort_order, Category.name)
    )
    return list(db.execute(stmt).scalars().all())


def create_category(db: Session, name: str, sort_order: int = 0) -> Category:
    category = Category(name=name.strip(), sort_order=sort_order)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def rename_category(db: Session, category: Category, name: str) -> Category:
    category.name = name.strip()
    db.commit()
    return category


def delete_category(db: Session, category: Category) -> None:
    """Удалить категорию. Запрещено, если на неё ссылаются модели."""
    count = db.scalar(
        select(func.count())
        .select_from(EquipmentModel)
        .where(EquipmentModel.category_id == category.id)
    )
    if count:
        raise InUse("Категория используется моделями оборудования")
    db.delete(category)
    db.commit()


def create_subcategory(
    db: Session, category: Category, name: str, sort_order: int = 0
) -> Subcategory:
    sub = Subcategory(category_id=category.id, name=name.strip(), sort_order=sort_order)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def delete_subcategory(db: Session, sub: Subcategory) -> None:
    count = db.scalar(
        select(func.count())
        .select_from(EquipmentModel)
        .where(EquipmentModel.subcategory_id == sub.id)
    )
    if count:
        raise InUse("Подкатегория используется моделями оборудования")
    db.delete(sub)
    db.commit()
