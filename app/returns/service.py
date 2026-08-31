"""Бизнес-логика приёмки оборудования (ТЗ §56)."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.inventory.enums import ItemStatus
from app.inventory.services import items as item_service
from app.inventory.services.items import _sql_normalized_barcode, normalize_barcode
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.packing.service import get_packing
from app.projects.models import Project
from app.returns.enums import ReturnCondition, ReturnStatus
from app.returns.models import ReturnLine, ReturnList, ReturnSerialItem


class ReturnError(Exception):
    """Ошибка домена приёмки."""


class AlreadyExists(ReturnError):
    pass


class IncompleteError(ReturnError):
    """Перевод в «Принято» при недостаче без подтверждения (ТЗ §56.5)."""


def _current_year() -> int:
    from app.database import utcnow
    from app.utils.timezone import to_local

    return to_local(utcnow()).year


def get_return(db: Session, project: Project) -> ReturnList | None:
    stmt = (
        select(ReturnList)
        .options(selectinload(ReturnList.lines).selectinload(ReturnLine.serial_items))
        .where(ReturnList.project_id == project.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def project_return_received(db: Session, project_id: int) -> bool:
    """Есть ли у проекта лист приёмки в статусе «Принято» (для гейта §56.6)."""
    status = db.scalar(select(ReturnList.status).where(ReturnList.project_id == project_id))
    return status == ReturnStatus.RECEIVED


def project_has_return(db: Session, project_id: int) -> bool:
    return db.scalar(select(ReturnList.id).where(ReturnList.project_id == project_id)) is not None


def create_from_packing(db: Session, project: Project) -> ReturnList:
    """Оформить возврат: снимок строк packing-листа (ТЗ §56.1).

    Если packing-листа нет, лист приёмки создаётся пустым — приёмка сводится
    к простому подтверждению без построчной сверки, чтобы гейт §56.6 не
    блокировал проекты, где packing-лист не вели.
    """
    if get_return(db, project) is not None:
        raise AlreadyExists("Лист приёмки уже создан")

    ret = ReturnList(
        project_id=project.id,
        number=next_number(db, DocType.RETURN, _current_year()),
        status=ReturnStatus.NOT_STARTED,
    )
    db.add(ret)
    db.flush()

    packing = get_packing(db, project)
    if packing is not None:
        sort_order = 1
        for line in packing.lines:
            expected = line.fact_quantity
            if expected <= 0:
                continue
            ret_line = ReturnLine(
                model_id=line.model_id,
                kit_id=line.kit_id,
                is_custom=line.is_custom,
                is_serial=line.is_serial,
                name=line.name,
                category_id=line.category_id,
                category_name=line.category_name,
                subcategory_name=line.subcategory_name,
                expected_quantity=expected,
                returned_quantity=0,
                sort_order=sort_order,
            )
            if line.is_serial:
                for si in line.serial_items:
                    ret_line.serial_items.append(
                        ReturnSerialItem(
                            item_id=si.item_id,
                            barcode=si.barcode,
                            serial_number=si.serial_number,
                            is_returned=False,
                        )
                    )
            ret.lines.append(ret_line)
            sort_order += 1

    db.commit()
    db.refresh(ret)
    return ret


def get_line(db: Session, ret: ReturnList, line_id: int) -> ReturnLine | None:
    return next((ln for ln in ret.lines if ln.id == line_id), None)


def get_serial_item(db: Session, ret: ReturnList, serial_item_id: int) -> ReturnSerialItem | None:
    for ln in ret.lines:
        for si in ln.serial_items:
            if si.id == serial_item_id:
                return si
    return None


def update_quantity_line(
    db: Session, line: ReturnLine, returned_quantity: int, comment: str | None
) -> None:
    if line.is_serial:
        raise ReturnError("Серийная строка принимается сканированием")
    line.returned_quantity = max(0, returned_quantity)
    line.comment = comment or None
    db.commit()


def set_condition(
    db: Session, serial_item: ReturnSerialItem, condition: ReturnCondition, comment: str | None
) -> None:
    """Пометить состояние принятого экземпляра до завершения приёмки (ТЗ §56.4)."""
    serial_item.condition = condition
    serial_item.condition_comment = comment or None
    db.commit()


def accept_all(db: Session, line: ReturnLine) -> int:
    """Принять пачкой все ещё не возвращённые единицы серийной строки (ТЗ §56.3).

    Для оборудования, которое физически едет и разгружается одним блоком (рэк из
    нескольких модулей), избавляет от поштучного сканирования. Состояние — «ОК» по
    умолчанию, как и у обычного скана; идемпотентно — уже принятые не трогает.
    """
    if not line.is_serial:
        raise ReturnError("Пачкой принимаются только серийные строки")
    pending = [si for si in line.serial_items if not si.is_returned]
    for si in pending:
        si.is_returned = True
    db.commit()
    return len(pending)


class ScanResult(enum.Enum):
    OK = "ok"
    ALREADY = "already"  # уже принят
    SUBSTITUTE_CANDIDATE = "substitute_candidate"  # другая единица той же модели (ТЗ §56.3)
    NOT_IN_LIST = "not_in_list"  # штрихкод не выдавался по этому проекту (ТЗ §56.3)
    NOT_FOUND = "not_found"


@dataclass
class ScanOutcome:
    result: ScanResult
    barcode: str
    line_id: int | None = None
    serial_item_id: int | None = None
    model_name: str | None = None
    expected: int = 0
    fact: int = 0
    pending_serial_item_id: int | None = None
    pending_barcode: str | None = None

    @property
    def ok(self) -> bool:
        return self.result == ScanResult.OK


def scan(db: Session, ret: ReturnList, barcode: str) -> ScanOutcome:
    """Отметить экземпляр возвращённым по штрихкоду (ТЗ §56.3).

    В отличие от packing, строка и ожидаемый экземпляр уже существуют (снимок из
    packing-листа, ТЗ §56.1) — сканирование только переключает is_returned.
    Штрихкод, которого нет в листе, — предупреждение, не блокирует (ТЗ §56.3).
    """
    barcode = barcode.strip()
    from app.inventory.models import EquipmentItem

    item = db.execute(
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return ScanOutcome(ScanResult.NOT_FOUND, barcode)

    for line in ret.lines:
        for si in line.serial_items:
            if si.item_id == item.id:
                if si.is_returned:
                    return ScanOutcome(
                        ScanResult.ALREADY,
                        barcode,
                        line_id=line.id,
                        serial_item_id=si.id,
                        model_name=line.name,
                        expected=line.expected_quantity,
                        fact=line.fact_quantity,
                    )
                si.is_returned = True
                db.commit()
                db.refresh(line)
                return ScanOutcome(
                    ScanResult.OK,
                    barcode,
                    line_id=line.id,
                    serial_item_id=si.id,
                    model_name=line.name,
                    expected=line.expected_quantity,
                    fact=line.fact_quantity,
                )

    # Та же модель, но другой конкретный экземпляр: похоже на замену на погрузке —
    # физически уехал не тот, что был отсканирован в packing-лист (ТЗ §56.3).
    for line in ret.lines:
        if line.is_serial and line.model_id == item.model_id:
            pending = next((si for si in line.serial_items if not si.is_returned), None)
            if pending is not None:
                return ScanOutcome(
                    ScanResult.SUBSTITUTE_CANDIDATE,
                    barcode,
                    line_id=line.id,
                    model_name=line.name,
                    expected=line.expected_quantity,
                    fact=line.fact_quantity,
                    pending_serial_item_id=pending.id,
                    pending_barcode=pending.barcode,
                )

    return ScanOutcome(ScanResult.NOT_IN_LIST, barcode, model_name=item.model.name)


def confirm_substitute(
    db: Session, ret: ReturnList, pending_serial_item_id: int, barcode: str
) -> ScanOutcome:
    """Подтвердить замену: переподставить реально принятый экземпляр (ТЗ §56.3).

    Ожидаемая строка (`pending_serial_item_id`) остаётся физически «на складе» —
    её штрихкод/экземпляр просто заменяются на то, что реально приехало, план/факт
    остаются согласованы без ложной недостачи или дефекта у изначально ожидавшейся
    единицы (см. apply_item_statuses).
    """
    barcode = barcode.strip()
    from app.inventory.models import EquipmentItem

    pending = get_serial_item(db, ret, pending_serial_item_id)
    if pending is None or pending.is_returned:
        return ScanOutcome(ScanResult.NOT_IN_LIST, barcode)

    item = db.execute(
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return ScanOutcome(ScanResult.NOT_FOUND, barcode)

    old_barcode = pending.barcode
    pending.item_id = item.id
    pending.barcode = item.barcode
    pending.serial_number = item.serial_number
    pending.is_returned = True
    db.commit()
    line = pending.line
    db.refresh(line)
    return ScanOutcome(
        ScanResult.OK,
        item.barcode,
        line_id=line.id,
        serial_item_id=pending.id,
        model_name=line.name,
        expected=line.expected_quantity,
        fact=line.fact_quantity,
        pending_barcode=old_barcode,
    )


def undo_scan(db: Session, ret: ReturnList, serial_item_id: int) -> None:
    si = get_serial_item(db, ret, serial_item_id)
    if si is not None:
        si.is_returned = False
        si.condition = ReturnCondition.OK
        si.condition_comment = None
        db.commit()


def is_incomplete(ret: ReturnList) -> bool:
    return any(ln.fact_quantity < ln.expected_quantity for ln in ret.lines)


def set_status(
    db: Session,
    ret: ReturnList,
    status: ReturnStatus,
    *,
    user_id: int | None = None,
    project_number: str = "",
    shortage_comment: str | None = None,
    confirm_incomplete: bool = False,
) -> None:
    """Сменить статус листа приёмки (ТЗ §56.2, §56.5, §56.6)."""
    if status == ReturnStatus.RECEIVED and is_incomplete(ret):
        if not confirm_incomplete or not (shortage_comment and shortage_comment.strip()):
            raise IncompleteError("Недостача: требуется подтверждение и комментарий")
        ret.shortage_comment = shortage_comment.strip()
    elif status == ReturnStatus.RECEIVED:
        ret.shortage_comment = None

    ret.status = status
    if status == ReturnStatus.RECEIVED:
        apply_item_statuses(db, ret, user_id=user_id, project_number=project_number)
    db.commit()


def apply_item_statuses(
    db: Session, ret: ReturnList, *, user_id: int | None, project_number: str
) -> None:
    """Отразить результат приёмки в статусах экземпляров (ТЗ §56.4).

    Переиспользует item_service.change_status — пишет в историю статусов экземпляра.
    """
    from app.inventory.models import EquipmentItem

    for line in ret.lines:
        for si in line.serial_items:
            item = db.get(EquipmentItem, si.item_id)
            if item is None:
                continue
            if not si.is_returned:
                item_service.change_status(
                    db,
                    item,
                    ItemStatus.DEFECT,
                    user_id,
                    f"не возвращено по проекту {project_number}, требует решения",
                )
            elif si.condition == ReturnCondition.DEFECT:
                comment = f"Возврат по проекту {project_number}"
                if si.condition_comment:
                    comment += f": {si.condition_comment}"
                item_service.change_status(db, item, ItemStatus.DEFECT, user_id, comment)
            elif si.condition == ReturnCondition.REPAIR:
                comment = f"Возврат по проекту {project_number}"
                if si.condition_comment:
                    comment += f": {si.condition_comment}"
                item_service.change_status(db, item, ItemStatus.REPAIR, user_id, comment)
