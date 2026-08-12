"""Сервис посерийных экземпляров: штрих-коды, статусы, история (ТЗ §8.2, §9, §21)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import utcnow
from app.inventory.enums import ItemStatus
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
    """Создать единицу оборудования (штрих-код опционален; уникален при наличии, ТЗ §38)."""
    barcode = (data.barcode or "").strip() or None
    if barcode and db.scalar(select(EquipmentItem.id).where(EquipmentItem.barcode == barcode)):
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
            comment="Создание единицы",
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


def barcode_sequence(start: str, count: int) -> list[str]:
    """Последовательные штрих-коды по стартовому образцу.

    Числовой суффикс инкрементируется с сохранением ширины (ведущие нули):
    ``NVPRO_001`` при count=10 → ``NVPRO_001`` … ``NVPRO_010``.
    Если в конце образца нет цифр — к нему добавляется числовой суффикс 1..count.
    Ширина растёт естественно при переполнении (``099`` → ``100``).
    """
    start = start.strip()
    match = re.search(r"(\d+)$", start)
    if match:
        prefix = start[: match.start()]
        first = int(match.group(1))
        width = len(match.group(1))
    else:
        prefix = start
        first = 1
        width = len(str(count))
    return [f"{prefix}{str(first + i).zfill(width)}" for i in range(count)]


def create_items_bulk(
    db: Session,
    model: EquipmentModel,
    count: int,
    start_barcode: str | None,
    user_id: int | None,
    comment: str | None = None,
) -> list[EquipmentItem]:
    """Массово создать N единиц. При заданном стартовом штрих-коде — с генерацией
    последовательных штрих-кодов; иначе — без штрих-кодов (ТЗ §8.2, §21.1, §38)."""
    count = max(1, count)
    start = (start_barcode or "").strip()
    barcodes: list[str | None]
    if start:
        barcodes = list(barcode_sequence(start, count))
        taken = set(
            db.execute(
                select(EquipmentItem.barcode).where(EquipmentItem.barcode.in_(barcodes))
            )
            .scalars()
            .all()
        )
        if taken:
            raise DuplicateBarcode(
                "Штрих-коды уже используются: " + ", ".join(sorted(taken))
            )
    else:
        barcodes = [None] * count

    items: list[EquipmentItem] = []
    for bc in barcodes:
        item = EquipmentItem(
            model_id=model.id,
            barcode=bc,
            status=ItemStatus.ACTIVE,
            comment=(comment or None),
        )
        item.status_history.append(
            EquipmentStatusHistory(
                changed_at=utcnow(),
                user_id=user_id,
                old_status=None,
                new_status=ItemStatus.ACTIVE,
                comment="Создание единицы",
            )
        )
        db.add(item)
        items.append(item)
    try:
        db.commit()
    except IntegrityError as exc:  # гонка на уникальном индексе (ТЗ §38)
        db.rollback()
        raise DuplicateBarcode("Штрих-код уже используется") from exc
    for item in items:
        db.refresh(item)
    return items


def create_units(db: Session, model: EquipmentModel, count: int) -> int:
    """Массово создать N единиц без штрих-кода (для количественного оборудования)."""
    count = max(0, count)
    for _ in range(count):
        db.add(EquipmentItem(model_id=model.id, barcode=None, status=ItemStatus.ACTIVE))
    if count:
        db.commit()
    return count


def update_item(db: Session, item: EquipmentItem, data: EquipmentItemInput) -> EquipmentItem:
    barcode = (data.barcode or "").strip() or None
    if (
        barcode
        and barcode != item.barcode
        and db.scalar(select(EquipmentItem.id).where(EquipmentItem.barcode == barcode))
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


def _apply_status(
    item: EquipmentItem,
    new_status: ItemStatus,
    user_id: int | None,
    comment: str | None,
) -> bool:
    """Сменить статус экземпляра в сессии без commit. Возвращает True, если статус изменился."""
    if new_status == item.status:
        return False
    old = item.status
    item.status = new_status
    # Разрешение «использовать несмотря на дефект» значимо только при DEFECT —
    # при любой смене статуса сбрасываем, чтобы новый дефект по умолчанию
    # считался недоступным, пока его явно не отметят как допустимый.
    item.usable_despite_defect = False
    item.status_history.append(
        EquipmentStatusHistory(
            changed_at=utcnow(),
            user_id=user_id,
            old_status=old,
            new_status=new_status,
            comment=(comment or None),
        )
    )
    return True


def change_status(
    db: Session,
    item: EquipmentItem,
    new_status: ItemStatus,
    user_id: int | None,
    comment: str | None = None,
) -> EquipmentItem:
    """Сменить статус экземпляра с записью истории (ТЗ §9)."""
    if _apply_status(item, new_status, user_id, comment):
        db.commit()
        db.refresh(item)
    return item


def list_items_by_ids(db: Session, model_id: int, item_ids: list[int]) -> list[EquipmentItem]:
    """Единицы данной модели по списку id (чужие модели игнорируются)."""
    if not item_ids:
        return []
    stmt = select(EquipmentItem).where(
        EquipmentItem.model_id == model_id, EquipmentItem.id.in_(item_ids)
    )
    return list(db.execute(stmt).scalars().all())


def item_label(item: EquipmentItem) -> str:
    return item.barcode or f"#{item.id}"


def bulk_change_status(
    db: Session,
    items: list[EquipmentItem],
    new_status: ItemStatus,
    user_id: int | None,
    comment: str | None = None,
    usable_despite_defect: bool = False,
) -> list[EquipmentItem]:
    """Массовая смена статуса нескольких экземпляров одним коммитом (ТЗ §9).

    Возвращает единицы, которые реально изменились: сменили статус либо (для
    статуса «Есть дефект») впервые получили отметку «несмотря на дефект» — иначе
    единицы, у которых менялся только этот флаг при уже совпадающем статусе,
    не попали бы в отчёт и журнал (были бы ошибочно показаны как «не изменено»).
    """
    affected = [item for item in items if _apply_status(item, new_status, user_id, comment)]
    if new_status == ItemStatus.DEFECT and usable_despite_defect:
        for item in items:
            if not item.usable_despite_defect:
                item.usable_despite_defect = True
                if item not in affected:
                    affected.append(item)
    if affected:
        db.commit()
        for item in items:
            db.refresh(item)
    return affected


def bulk_delete_items(db: Session, items: list[EquipmentItem]) -> tuple[list[str], list[str]]:
    """Удалить неиспользованные и не входящие в комплект единицы одним коммитом.

    Возвращает (метки удалённых, метки пропущенных)."""
    deleted: list[str] = []
    skipped: list[str] = []
    for item in items:
        if item.kit_id is not None or is_item_used(db, item):
            skipped.append(item_label(item))
            continue
        deleted.append(item_label(item))
        db.delete(item)
    if deleted:
        db.commit()
    return deleted, skipped


def set_usable_despite_defect(db: Session, item: EquipmentItem, value: bool) -> EquipmentItem:
    """Пометить дефектный экземпляр как пригодный к использованию несмотря на дефект.

    Значимо только при статусе «Есть дефект» — вне его игнорируется, т.к.
    флаг не влияет на доступность при других статусах (см. EquipmentItem.is_usable).
    """
    if item.status != ItemStatus.DEFECT:
        return item
    item.usable_despite_defect = value
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
