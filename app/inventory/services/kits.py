"""Сервис комплектов (структура «Комплект»).

Комплект — кейс с фиксированной комплектацией из конкретных единиц (EquipmentItem).
Помещённая в комплект единица исключается из свободного стока своей модели; при
извлечении — возвращается. Комплект — бронируемая позиция (смета/packing).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.inventory.enums import KitWeightMode
from app.inventory.models import EquipmentItem, EquipmentModel, Kit
from app.inventory.schemas import KitInput
from app.inventory.services.categories import InUse, InventoryError
from app.projects.models import ProjectReservation


def list_kits(db: Session, *, archived: bool = False) -> list[Kit]:
    stmt = select(Kit).where(Kit.is_archived.is_(archived)).order_by(Kit.name)
    return list(db.execute(stmt).scalars().all())


def get_kit(db: Session, kit_id: int) -> Kit | None:
    stmt = (
        select(Kit)
        .options(selectinload(Kit.items).selectinload(EquipmentItem.model))
        .where(Kit.id == kit_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def item_count(db: Session, kit_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(EquipmentItem).where(EquipmentItem.kit_id == kit_id)
        )
        or 0
    )


def _weight_value_for(data: KitInput) -> Decimal | None:
    """Значение веса хранится только для режимов, где оно требуется."""
    if data.weight_mode == KitWeightMode.CONTENT:
        return None
    return data.weight_value


def create_kit(db: Session, data: KitInput) -> Kit:
    kit = Kit(
        name=data.name.strip(),
        description=(data.description or None),
        weight_mode=data.weight_mode,
        weight_value=_weight_value_for(data),
    )
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return kit


def update_kit(db: Session, kit: Kit, data: KitInput) -> Kit:
    kit.name = data.name.strip()
    kit.description = data.description or None
    kit.weight_mode = data.weight_mode
    kit.weight_value = _weight_value_for(data)
    db.commit()
    db.refresh(kit)
    return kit


def _is_booked(db: Session, kit_id: int) -> bool:
    return (
        db.scalar(select(ProjectReservation.id).where(ProjectReservation.kit_id == kit_id))
        is not None
    )


def delete_kit(db: Session, kit: Kit) -> None:
    """Удалить комплект: сперва вернуть единицы в свободный сток; нельзя, если забронирован."""
    if _is_booked(db, kit.id):
        raise InUse("Комплект используется в проектах — удаление недоступно")
    for item in list(kit.items):
        item.kit_id = None
    db.delete(kit)
    db.commit()


def add_items(db: Session, kit: Kit, item_ids: list[int]) -> int:
    """Поместить свободные единицы в комплект (вычесть из свободного стока)."""
    if not item_ids:
        return 0
    items = (
        db.execute(
            select(EquipmentItem).where(
                EquipmentItem.id.in_(item_ids),
                EquipmentItem.kit_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        item.kit_id = kit.id
    if items:
        db.commit()
    return len(items)


def remove_item(db: Session, kit: Kit, item_id: int) -> EquipmentItem | None:
    """Извлечь единицу из комплекта (вернуть в свободный сток модели)."""
    item = db.execute(
        select(EquipmentItem).where(EquipmentItem.id == item_id, EquipmentItem.kit_id == kit.id)
    ).scalar_one_or_none()
    if item is None:
        return None
    item.kit_id = None
    db.commit()
    return item


def free_items(
    db: Session,
    *,
    query: str | None = None,
    model_id: int | None = None,
    category_id: int | None = None,
) -> list[EquipmentItem]:
    """Свободные единицы (не в комплекте) для пикера добавления в комплект."""
    stmt = (
        select(EquipmentItem)
        .join(EquipmentModel, EquipmentModel.id == EquipmentItem.model_id)
        .options(selectinload(EquipmentItem.model))
        .where(EquipmentItem.kit_id.is_(None))
    )
    if model_id:
        stmt = stmt.where(EquipmentItem.model_id == model_id)
    if category_id:
        stmt = stmt.where(EquipmentModel.category_id == category_id)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            EquipmentModel.name.ilike(like)
            | EquipmentItem.barcode.ilike(like)
            | EquipmentItem.serial_number.ilike(like)
        )
    stmt = stmt.order_by(EquipmentModel.name, EquipmentItem.barcode)
    return list(db.execute(stmt).scalars().all())


@dataclass
class KitGroup:
    """Комплектация комплекта, сгруппированная по модели (для отображения)."""

    model_id: int
    model_name: str
    items: list[EquipmentItem]

    @property
    def count(self) -> int:
        return len(self.items)


def content_groups(kit: Kit) -> list[KitGroup]:
    """Содержимое комплекта, сгруппированное по модели."""
    groups: dict[int, KitGroup] = {}
    for item in sorted(kit.items, key=lambda it: (it.model.name if it.model else "", it.id)):
        g = groups.get(item.model_id)
        if g is None:
            g = KitGroup(
                model_id=item.model_id,
                model_name=item.model.name if item.model else "—",
                items=[],
            )
            groups[item.model_id] = g
        g.items.append(item)
    return sorted(groups.values(), key=lambda g: g.model_name)


def content_weight(kit: Kit) -> Decimal:
    """Суммарный вес содержимого комплекта (сумма веса единиц)."""
    total = Decimal("0")
    for item in kit.items:
        if item.model is not None:
            total += item.model.weight_kg
    return total


def total_weight(kit: Kit) -> Decimal:
    """Расчётный вес комплекта для packing (снимок), с учётом настройки веса.

    TOTAL — фиксированный общий вес (содержимое не учитывается);
    PACKAGING — вес содержимого + вес упаковки/кейса;
    CONTENT (или значение не задано) — только вес содержимого.
    """
    content = content_weight(kit)
    if kit.weight_value is not None:
        if kit.weight_mode == KitWeightMode.TOTAL:
            return kit.weight_value
        if kit.weight_mode == KitWeightMode.PACKAGING:
            return content + kit.weight_value
    return content


__all__ = [
    "InUse",
    "InventoryError",
    "KitGroup",
    "add_items",
    "content_groups",
    "content_weight",
    "create_kit",
    "delete_kit",
    "free_items",
    "get_kit",
    "item_count",
    "list_kits",
    "remove_item",
    "total_weight",
    "update_kit",
]
