"""Бизнес-логика packing-листа (ТЗ §17–§20)."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.estimates.service import get_estimate
from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.models import EquipmentItem, EquipmentModel
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.packing.calc import PackingTotals, compute_totals
from app.packing.enums import PackingStatus
from app.packing.models import PackingLine, PackingList, PackingSerialItem
from app.packing.schemas import CustomPackingLine
from app.projects.models import Project


class PackingError(Exception):
    """Ошибка домена packing-листа."""


class AlreadyExists(PackingError):
    pass


class UndercompleteError(PackingError):
    """Перевод в «Скомплектован» при недокомплекте без подтверждения (ТЗ §17.4)."""


def _current_year() -> int:
    from app.database import utcnow
    from app.utils.timezone import to_local

    return to_local(utcnow()).year


def get_packing(db: Session, project: Project) -> PackingList | None:
    stmt = (
        select(PackingList)
        .options(selectinload(PackingList.lines).selectinload(PackingLine.serial_items))
        .where(PackingList.project_id == project.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def _estimate_warehouse_quantities(db: Session, project: Project) -> dict[int, int]:
    """Складские модели сметы, агрегированные по модели (ТЗ §17.1)."""
    estimate = get_estimate(db, project)
    result: dict[int, int] = {}
    if estimate is None:
        return result
    for line in estimate.lines:
        if not line.is_custom and line.model_id is not None:
            result[line.model_id] = result.get(line.model_id, 0) + line.quantity
    return result


def _new_line_from_model(model: EquipmentModel, planned: int, sort_order: int) -> PackingLine:
    is_serial = model.accounting_type == AccountingType.SERIAL
    pk = model.packing
    line = PackingLine(
        model_id=model.id,
        is_custom=False,
        is_serial=is_serial,
        name=model.name,
        category_id=model.category_id,
        category_name=model.category.name if model.category else None,
        subcategory_name=model.subcategory.name if model.subcategory else None,
        planned_quantity=planned,
        # Количественные: факт по умолчанию = план; серийные — экземпляры назначаются.
        quantity=0 if is_serial else planned,
        sort_order=sort_order,
        unit_weight_kg=model.weight_kg,
        length_mm=model.length_mm,
        width_mm=model.width_mm,
        height_mm=model.height_mm,
        has_packing=pk is not None,
        pack_capacity=pk.capacity if pk else 1,
        pack_empty_weight_kg=pk.empty_weight_kg if pk else 0,
        pack_length_mm=pk.length_mm if pk else 0,
        pack_width_mm=pk.width_mm if pk else 0,
        pack_height_mm=pk.height_mm if pk else 0,
    )
    # При наличии упаковки всё количество по умолчанию упаковано (ТЗ §18).
    line.packed_quantity = planned if (pk is not None and not is_serial) else 0
    return line


def create_from_estimate(db: Session, project: Project) -> PackingList:
    """Создать packing-лист из текущей сметы (ТЗ §17.1)."""
    if get_packing(db, project) is not None:
        raise AlreadyExists("Packing-лист уже создан")

    quantities = _estimate_warehouse_quantities(db, project)
    packing = PackingList(
        project_id=project.id,
        number=next_number(db, DocType.PACKING, _current_year()),
        status=PackingStatus.NOT_STARTED,
    )
    db.add(packing)
    db.flush()

    sort_order = 1
    for model_id, planned in quantities.items():
        model = db.get(EquipmentModel, model_id)
        if model is None:
            continue
        packing.lines.append(_new_line_from_model(model, planned, sort_order))
        sort_order += 1
    db.commit()
    db.refresh(packing)
    return packing


# --- Синхронизация со сметой (ТЗ §17.2) ---------------------------------


@dataclass
class Discrepancy:
    model_id: int
    name: str
    estimate_quantity: int
    planned_quantity: int


def discrepancies(db: Session, project: Project, packing: PackingList) -> list[Discrepancy]:
    """Расхождения плана packing-листа с текущей сметой (ТЗ §17.2)."""
    estimate_qty = _estimate_warehouse_quantities(db, project)
    by_model = {ln.model_id: ln for ln in packing.lines if not ln.is_custom and ln.model_id}
    result: list[Discrepancy] = []
    for model_id, est_qty in estimate_qty.items():
        line = by_model.get(model_id)
        planned = line.planned_quantity if line else 0
        if planned != est_qty:
            name = line.name if line else (db.get(EquipmentModel, model_id).name)
            result.append(Discrepancy(model_id, name, est_qty, planned))
    for model_id, line in by_model.items():
        if model_id not in estimate_qty and line.planned_quantity != 0:
            result.append(Discrepancy(model_id, line.name, 0, line.planned_quantity))
    return result


def apply_sync(db: Session, project: Project, packing: PackingList) -> None:
    """Применить синхронизацию: план = смета; добавить новые модели (ТЗ §17.2)."""
    estimate_qty = _estimate_warehouse_quantities(db, project)
    by_model = {ln.model_id: ln for ln in packing.lines if not ln.is_custom and ln.model_id}
    next_sort = max((ln.sort_order for ln in packing.lines), default=0) + 1

    for model_id, est_qty in estimate_qty.items():
        line = by_model.get(model_id)
        if line is None:
            model = db.get(EquipmentModel, model_id)
            if model is not None:
                packing.lines.append(_new_line_from_model(model, est_qty, next_sort))
                next_sort += 1
        else:
            line.planned_quantity = est_qty
    for model_id, line in by_model.items():
        if model_id not in estimate_qty:
            line.planned_quantity = 0
    db.commit()


# --- Строки -------------------------------------------------------------


def get_line(db: Session, packing: PackingList, line_id: int) -> PackingLine | None:
    return next((ln for ln in packing.lines if ln.id == line_id), None)


def update_quantity_line(
    db: Session, line: PackingLine, fact_quantity: int, packed_quantity: int, comment: str | None
) -> None:
    """Обновить количественную строку: факт и распределение (ТЗ §17.6, §18)."""
    if line.is_serial:
        raise PackingError("Серийная строка комплектуется экземплярами")
    line.quantity = max(0, fact_quantity)
    line.packed_quantity = max(0, min(packed_quantity, line.quantity))
    line.comment = comment or None
    db.commit()


def set_distribution(db: Session, line: PackingLine, packed_quantity: int) -> None:
    """Распределение в упаковке / без упаковки (ТЗ §18)."""
    line.packed_quantity = max(0, min(packed_quantity, line.fact_quantity))
    db.commit()


class SerialResult(enum.Enum):
    OK = "ok"
    OVER_PLAN = "over_plan"  # добавлено сверх плана (ТЗ §17.8)
    DUPLICATE = "duplicate"  # уже в листе (ТЗ §22)
    WRONG_MODEL = "wrong_model"
    BLOCKED = "blocked"  # списан/в ремонте (ТЗ §22)
    NOT_FOUND = "not_found"


def add_serial_item(
    db: Session, line: PackingLine, barcode: str, *, allow_over: bool = False
) -> SerialResult:
    """Назначить экземпляр в серийную строку по штрих-коду (ТЗ §17.7, §17.8, §22)."""
    if not line.is_serial:
        raise PackingError("Эта строка не серийная")

    item = db.execute(
        select(EquipmentItem).where(EquipmentItem.barcode == barcode.strip())
    ).scalar_one_or_none()
    if item is None:
        return SerialResult.NOT_FOUND
    if item.model_id != line.model_id:
        return SerialResult.WRONG_MODEL
    if item.status != ItemStatus.ACTIVE:
        return SerialResult.BLOCKED
    if any(si.item_id == item.id for si in line.serial_items):
        return SerialResult.DUPLICATE

    over = (line.fact_quantity + 1) > line.planned_quantity
    if over and not allow_over:
        return SerialResult.OVER_PLAN

    line.serial_items.append(
        PackingSerialItem(item_id=item.id, barcode=item.barcode, serial_number=item.serial_number)
    )
    if line.has_packing:
        line.packed_quantity += 1  # по умолчанию упаковано (ТЗ §18)
    db.commit()
    return SerialResult.OVER_PLAN if over else SerialResult.OK


def remove_serial_item(db: Session, line: PackingLine, serial_item_id: int) -> None:
    si = next((s for s in line.serial_items if s.id == serial_item_id), None)
    if si is not None:
        line.serial_items.remove(si)
        line.packed_quantity = min(line.packed_quantity, line.fact_quantity)
        db.commit()


def add_custom_line(db: Session, packing: PackingList, data: CustomPackingLine) -> PackingLine:
    """Дополнительная позиция: вес/объём учитываются, бронь — нет (ТЗ §17.9)."""
    sort_order = max((ln.sort_order for ln in packing.lines), default=0) + 1
    line = PackingLine(
        packing_list_id=packing.id,
        model_id=None,
        is_custom=True,
        is_serial=False,
        name=data.name.strip(),
        planned_quantity=data.quantity,
        quantity=data.quantity,
        packed_quantity=0,
        unit_weight_kg=data.unit_weight_kg,
        length_mm=data.length_mm,
        width_mm=data.width_mm,
        height_mm=data.height_mm,
        has_packing=False,
        comment=(data.comment or None),
        sort_order=sort_order,
    )
    packing.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def delete_line(db: Session, line: PackingLine) -> None:
    db.delete(line)
    db.commit()


def move_line(db: Session, packing: PackingList, line: PackingLine, direction: int) -> None:
    """Перемещение строки в пределах категории/подкатегории (ТЗ §17.3)."""
    group = [
        ln
        for ln in packing.lines
        if ln.category_id == line.category_id and ln.subcategory_name == line.subcategory_name
    ]
    group.sort(key=lambda ln: (ln.sort_order, ln.id))
    idx = group.index(line)
    swap = idx + direction
    if 0 <= swap < len(group):
        other = group[swap]
        line.sort_order, other.sort_order = other.sort_order, line.sort_order
        db.commit()


# --- Статусы (ТЗ §17.4) -------------------------------------------------


def is_undercomplete(packing: PackingList) -> bool:
    return any(ln.fact_quantity < ln.planned_quantity for ln in packing.lines)


def set_status(
    db: Session,
    packing: PackingList,
    status: PackingStatus,
    *,
    shortage_comment: str | None = None,
    confirm_undercomplete: bool = False,
) -> None:
    """Сменить статус. Скомплектован при недокомплекте требует подтверждения и причины."""
    if status == PackingStatus.PICKED and is_undercomplete(packing):
        if not confirm_undercomplete or not (shortage_comment and shortage_comment.strip()):
            raise UndercompleteError("Недокомплект: требуется подтверждение и комментарий")
        packing.shortage_comment = shortage_comment.strip()
    elif status == PackingStatus.PICKED:
        packing.shortage_comment = None
    packing.status = status
    db.commit()


def totals(db: Session, packing: PackingList) -> PackingTotals:
    return compute_totals(packing.lines)


def project_has_packing(db: Session, project_id: int) -> bool:
    return db.scalar(select(PackingList.id).where(PackingList.project_id == project_id)) is not None
