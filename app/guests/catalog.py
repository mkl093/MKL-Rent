"""Каталог оборудования для гостевого портала.

Видимость моделей ограничена общим списком категорий, скрытых от гостей
(Category.hidden_from_guests) — настройка одна на всех гостевых клиентов.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.inventory.models import Category, EquipmentModel
from app.inventory.services.equipment import stock_quantity
from app.projects.availability import compute_availability


def visible_models(db: Session, query: str | None = None) -> list[EquipmentModel]:
    """Модели, доступные гостям: не в архиве, категория не скрыта."""
    stmt = (
        select(EquipmentModel)
        .join(EquipmentModel.category)
        .options(selectinload(EquipmentModel.category))
        .where(
            EquipmentModel.is_archived.is_(False),
            Category.hidden_from_guests.is_(False),
        )
        .order_by(EquipmentModel.name)
    )
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(EquipmentModel.name.ilike(like) | EquipmentModel.manufacturer.ilike(like))
    return list(db.execute(stmt).scalars().all())


def get_visible_model(db: Session, model_id: int) -> EquipmentModel | None:
    """Модель по id, если она видна гостям (иначе None — как будто не существует)."""
    model = db.get(EquipmentModel, model_id)
    if model is None or model.is_archived or model.category.hidden_from_guests:
        return None
    return model


def total_stock(db: Session, model: EquipmentModel) -> int:
    """Общее количество модели на складе (та же цифра, что видит персонал)."""
    return stock_quantity(db, model)


def available_for_period(db: Session, model: EquipmentModel, start: date, end: date) -> int:
    """Свободный остаток модели на период (уровень 2 — та же логика, что у персонала).

    При перебронировании (дефиците) available может уйти в минус — персоналу это
    показывают как есть (величина дефицита), но гостю отрицательное число не
    нужно и не должно ничего раскрывать про перебронь: 0 читается как «занято».
    """
    return max(0, compute_availability(db, model, start, end).available)
