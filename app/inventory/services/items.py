"""Сервис посерийных экземпляров: штрих-коды, статусы, история (ТЗ §8.2, §9, §21)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import utcnow
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.models import EquipmentItem, EquipmentModel, EquipmentStatusHistory
from app.inventory.schemas import EquipmentItemInput
from app.inventory.services.categories import InUse, InventoryError


class DuplicateBarcode(InventoryError):
    """Штрих-код уже существует (ТЗ §21.1, §40.2)."""


def find_by_barcode(db: Session, barcode: str) -> EquipmentItem | None:
    """Глобальный поиск экземпляра по штрих-коду (ТЗ §21.4)."""
    stmt = (
        select(EquipmentItem)
        .options(
            selectinload(EquipmentItem.model),
            selectinload(EquipmentItem.status_history),
        )
        .where(EquipmentItem.barcode == barcode.strip())
    )
    return db.execute(stmt).scalar_one_or_none()


def get_item(db: Session, item_id: int) -> EquipmentItem | None:
    stmt = (
        select(EquipmentItem)
        .options(
            selectinload(EquipmentItem.model),
            selectinload(EquipmentItem.status_history),
        )
        .where(EquipmentItem.id == item_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_items(db: Session, model_id: int) -> list[EquipmentItem]:
    stmt = (
        select(EquipmentItem)
        .where(EquipmentItem.model_id == model_id)
        .order_by(EquipmentItem.barcode)
    )
    return list(db.execute(stmt).scalars().all())


def create_item(
    db: Session, model: EquipmentModel, data: EquipmentItemInput, user_id: int | None
) -> EquipmentItem:
    """Создать экземпляр посерийной модели с уникальным штрих-кодом (ТЗ §38)."""
    if model.accounting_type != AccountingType.SERIAL:
        raise InventoryError("Экземпляры есть только у посерийной модели")

    barcode = data.barcode.strip()
    if db.scalar(select(EquipmentItem.id).where(EquipmentItem.barcode == barcode)):
        raise DuplicateBarcode("Штрих-код уже используется")

    item = EquipmentItem(
        model_id=model.id,
        barcode=barcode,
        status=ItemStatus.ACTIVE,
        serial_number=(data.serial_number or None),
        inventory_number=(data.inventory_number or None),
        comment=(data.comment or None),
    )
    item.status_history.append(
        EquipmentStatusHistory(
            changed_at=utcnow(),
            user_id=user_id,
            old_status=None,
            new_status=ItemStatus.ACTIVE,
            comment="Создание экземпляра",
        )
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:  # гонка на уникальном индексе (ТЗ §38)
        db.rollback()
        raise DuplicateBarcode("Штрих-код уже используется") from exc
    db.refresh(item)
    return item


def update_item(db: Session, item: EquipmentItem, data: EquipmentItemInput) -> EquipmentItem:
    barcode = data.barcode.strip()
    if barcode != item.barcode and db.scalar(
        select(EquipmentItem.id).where(EquipmentItem.barcode == barcode)
    ):
        raise DuplicateBarcode("Штрих-код уже используется")
    item.barcode = barcode
    item.serial_number = data.serial_number or None
    item.inventory_number = data.inventory_number or None
    item.comment = data.comment or None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateBarcode("Штрих-код уже используется") from exc
    db.refresh(item)
    return item


def change_status(
    db: Session,
    item: EquipmentItem,
    new_status: ItemStatus,
    user_id: int | None,
    comment: str | None = None,
) -> EquipmentItem:
    """Сменить статус экземпляра с записью истории (ТЗ §9)."""
    if new_status == item.status:
        return item
    old = item.status
    item.status = new_status
    item.status_history.append(
        EquipmentStatusHistory(
            changed_at=utcnow(),
            user_id=user_id,
            old_status=old,
            new_status=new_status,
            comment=(comment or None),
        )
    )
    db.commit()
    db.refresh(item)
    return item


def is_item_used(db: Session, item: EquipmentItem) -> bool:
    """Использовался ли экземпляр в packing-листах (расширим на Этапе 5, ТЗ §9)."""
    return False


def delete_item(db: Session, item: EquipmentItem) -> None:
    """Удалить можно только неиспользованный экземпляр; иначе — статус «Списано» (ТЗ §9)."""
    if is_item_used(db, item):
        raise InUse("Экземпляр использовался — переведите его в «Списано»")
    db.delete(item)
    db.commit()
