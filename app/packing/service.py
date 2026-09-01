"""Бизнес-логика packing-листа (ТЗ §17–§20)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.estimates.models import EstimateLine
from app.estimates.service import add_model as estimate_add_model
from app.estimates.service import get_estimate
from app.estimates.service import sync_reservations as estimate_sync_reservations
from app.inventory.enums import AccountingType
from app.inventory.models import EquipmentItem, EquipmentModel, Kit
from app.inventory.services import kits as kit_service
from app.inventory.services.items import _sql_normalized_barcode, normalize_barcode
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.packing.calc import (
    CategoryTotal,
    PackingTotals,
    compute_category_breakdown,
    compute_totals,
)
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


class SyncConfirmRequired(PackingError):
    """Синхронизация удалит строки с уже собранным фактом — нужно подтверждение."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"Требуется подтверждение удаления: {', '.join(names)}")


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


KIT_GROUP_NAME = "Комплекты"


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


def _estimate_kit_ids(db: Session, project: Project) -> list[int]:
    """Комплекты, добавленные в смету (структура «Комплект»)."""
    estimate = get_estimate(db, project)
    if estimate is None:
        return []
    return [line.kit_id for line in estimate.lines if line.kit_id is not None]


def _estimate_custom_lines(db: Session, project: Project) -> list[EstimateLine]:
    """Произвольные строки сметы, переносимые в packing-лист (ТЗ §16.5, §17.9).

    Строка с отключённым чекбоксом «Не добавлять в паккинг лист» (add_to_packing=False)
    в переносе не участвует.
    """
    estimate = get_estimate(db, project)
    if estimate is None:
        return []
    return [ln for ln in estimate.lines if ln.is_custom and ln.add_to_packing]


def _new_line_from_kit(kit: Kit, sort_order: int) -> PackingLine:
    """Строка packing-листа для комплекта: название + снимок веса комплектации.

    Перечень комплектации отображается «живьём» по kit_id (см. packing.router).
    """
    return PackingLine(
        model_id=None,
        kit_id=kit.id,
        is_custom=False,
        is_serial=False,
        name=kit.name,
        category_id=None,
        category_name=KIT_GROUP_NAME,
        subcategory_name=None,
        planned_quantity=1,
        quantity=1,
        packed_quantity=0,
        sort_order=sort_order,
        unit_weight_kg=kit_service.total_weight(kit),
        length_mm=0,
        width_mm=0,
        height_mm=0,
        has_packing=False,
    )


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
        has_power=model.has_power,
        power_peak_w=model.power_peak_w or 0,
        power_nominal_w=model.power_nominal_w or 0,
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


def _new_line_from_estimate_custom(est_line: EstimateLine, sort_order: int) -> PackingLine:
    """Строка packing-листа из произвольной строки сметы (ТЗ §17.9).

    Вес и габариты не переносятся из сметы (там их нет) — правятся в самом
    packing-листе, как и у обычной дополнительной позиции.
    """
    return PackingLine(
        model_id=None,
        estimate_line_id=est_line.id,
        is_custom=True,
        is_serial=False,
        is_manual=False,
        name=est_line.name,
        planned_quantity=est_line.quantity,
        quantity=est_line.quantity,
        packed_quantity=0,
        unit_weight_kg=Decimal("0"),
        length_mm=0,
        width_mm=0,
        height_mm=0,
        has_packing=False,
        comment=est_line.comment,
        sort_order=sort_order,
    )


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
    # Комплекты сметы — отдельными строками с перечнем комплектации (ТЗ §17, «Комплект»).
    for kit_id in _estimate_kit_ids(db, project):
        kit = kit_service.get_kit(db, kit_id)
        if kit is None:
            continue
        packing.lines.append(_new_line_from_kit(kit, sort_order))
        sort_order += 1
    # Произвольные строки сметы — суб-аренда и подобное (ТЗ §17.9).
    for est_line in _estimate_custom_lines(db, project):
        packing.lines.append(_new_line_from_estimate_custom(est_line, sort_order))
        sort_order += 1
    db.commit()
    db.refresh(packing)
    return packing


# --- Синхронизация со сметой (ТЗ §17.2) ---------------------------------


@dataclass
class Discrepancy:
    model_id: int | None
    name: str
    estimate_quantity: int
    planned_quantity: int
    is_kit: bool = False
    is_custom: bool = False


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
        if model_id not in estimate_qty and not line.is_manual and line.planned_quantity != 0:
            result.append(Discrepancy(model_id, line.name, 0, line.planned_quantity))

    # Комплекты: расхождение состава сметы и packing-листа («Комплект»).
    est_kits = set(_estimate_kit_ids(db, project))
    packing_kits = {ln.kit_id: ln for ln in packing.lines if ln.kit_id is not None}
    for kit_id in est_kits:
        if kit_id not in packing_kits:
            kit = kit_service.get_kit(db, kit_id)
            if kit is not None:
                result.append(Discrepancy(None, kit.name, 1, 0, is_kit=True))
    for kit_id, line in packing_kits.items():
        if kit_id not in est_kits and not line.is_manual:
            result.append(Discrepancy(None, line.name, 0, 1, is_kit=True))

    # Произвольные строки сметы — перенос/обновление/удаление в packing (ТЗ §17.9).
    custom_lines = _estimate_custom_lines(db, project)
    custom_by_est_id = {
        ln.estimate_line_id: ln
        for ln in packing.lines
        if ln.is_custom and ln.estimate_line_id is not None
    }
    current_est_ids = {ln.id for ln in custom_lines}
    for est_line in custom_lines:
        line = custom_by_est_id.get(est_line.id)
        planned = line.planned_quantity if line else 0
        if line is None or planned != est_line.quantity:
            result.append(
                Discrepancy(None, est_line.name, est_line.quantity, planned, is_custom=True)
            )
    for est_id, line in custom_by_est_id.items():
        if est_id not in current_est_ids:
            result.append(Discrepancy(None, line.name, 0, line.planned_quantity, is_custom=True))
    return result


def sync_removals(db: Session, project: Project, packing: PackingList) -> list[PackingLine]:
    """Строки, которые синхронизация удалит (пропавшие из сметы модели/произвольные)."""
    estimate_qty = _estimate_warehouse_quantities(db, project)
    by_model = {ln.model_id: ln for ln in packing.lines if not ln.is_custom and ln.model_id}
    to_delete = [
        line
        for model_id, line in by_model.items()
        if model_id not in estimate_qty and not line.is_manual
    ]

    current_est_ids = {ln.id for ln in _estimate_custom_lines(db, project)}
    to_delete += [
        ln
        for ln in packing.lines
        if ln.is_custom and ln.estimate_line_id is not None and ln.estimate_line_id not in current_est_ids
    ]
    return to_delete


def apply_sync(db: Session, project: Project, packing: PackingList, *, confirm_delete: bool = False) -> None:
    """Применить синхронизацию: план = смета; добавить новые, удалить пропавшие (ТЗ §17.2).

    Строки, добавленные в packing вручную (is_manual), синхронизация не удаляет.
    Если среди удаляемых есть строки с уже собранным фактом, требуется явное
    подтверждение (confirm_delete) — иначе бросается SyncConfirmRequired.
    """
    estimate_qty = _estimate_warehouse_quantities(db, project)
    by_model = {ln.model_id: ln for ln in packing.lines if not ln.is_custom and ln.model_id}
    next_sort = max((ln.sort_order for ln in packing.lines), default=0) + 1

    to_delete = sync_removals(db, project, packing)
    if not confirm_delete:
        with_fact = [ln for ln in to_delete if ln.fact_quantity > 0]
        if with_fact:
            raise SyncConfirmRequired([ln.name for ln in with_fact])

    for model_id, est_qty in estimate_qty.items():
        line = by_model.get(model_id)
        if line is None:
            model = db.get(EquipmentModel, model_id)
            if model is not None:
                packing.lines.append(_new_line_from_model(model, est_qty, next_sort))
                next_sort += 1
        else:
            line.planned_quantity = est_qty
    for line in to_delete:
        db.delete(line)

    # Комплекты: добавить новые, убрать отсутствующие в смете («Комплект»).
    est_kits = set(_estimate_kit_ids(db, project))
    by_kit = {ln.kit_id: ln for ln in packing.lines if ln.kit_id is not None}
    for kit_id in est_kits:
        if kit_id not in by_kit:
            kit = kit_service.get_kit(db, kit_id)
            if kit is not None:
                packing.lines.append(_new_line_from_kit(kit, next_sort))
                next_sort += 1
    for kit_id, line in by_kit.items():
        if kit_id not in est_kits and not line.is_manual:
            db.delete(line)

    # Произвольные строки сметы: добавить новые, обновить существующие (ТЗ §17.9).
    custom_by_est_id = {
        ln.estimate_line_id: ln
        for ln in packing.lines
        if ln.is_custom and ln.estimate_line_id is not None
    }
    for est_line in _estimate_custom_lines(db, project):
        line = custom_by_est_id.get(est_line.id)
        if line is None:
            packing.lines.append(_new_line_from_estimate_custom(est_line, next_sort))
            next_sort += 1
        else:
            line.name = est_line.name
            line.comment = est_line.comment
            line.planned_quantity = est_line.quantity
            line.quantity = est_line.quantity

    db.commit()


# --- Обновление сметы по факту packing-листа (обратная синхронизация) ---


@dataclass
class EstimateSyncItem:
    """Расхождение количества строки сметы с фактом packing-листа."""

    estimate_line_id: int | None
    model_id: int | None
    name: str
    estimate_quantity: int
    fact_quantity: int
    is_new: bool = False

    @property
    def key(self) -> str:
        """Стабильный идентификатор позиции для чекбоксов выбора в форме подтверждения."""
        return f"new:{self.model_id}" if self.is_new else f"line:{self.estimate_line_id}"


def estimate_discrepancies(
    db: Session, project: Project, packing: PackingList
) -> list[EstimateSyncItem]:
    """Расхождения количества в смете с фактом packing-листа.

    В отличие от discrepancies() (смета → план packing-листа), здесь источник —
    факт: сколько реально отсканировано/указано (line.fact_quantity). Складские
    модели сравниваются по сумме факта всех их строк; произвольные — только те,
    что созданы из сметы (estimate_line_id). Комплекты не участвуют — их
    количество в смете всегда 1 («Комплект»). Позиции сметы без соответствующей
    строки в packing-листе не считаются расхождением — packing может быть ещё
    не собран.
    """
    estimate = get_estimate(db, project)
    if estimate is None:
        return []

    fact_by_model: dict[int, int] = {}
    for ln in packing.lines:
        if not ln.is_custom and ln.model_id is not None:
            fact_by_model[ln.model_id] = fact_by_model.get(ln.model_id, 0) + ln.fact_quantity

    est_by_model = {
        ln.model_id: ln
        for ln in estimate.lines
        if not ln.is_custom and not ln.is_kit and ln.model_id is not None
    }

    result: list[EstimateSyncItem] = []
    for model_id, fact in fact_by_model.items():
        line = est_by_model.get(model_id)
        if line is None:
            if fact > 0:
                model = db.get(EquipmentModel, model_id)
                if model is not None:
                    result.append(EstimateSyncItem(None, model_id, model.name, 0, fact, is_new=True))
        elif line.quantity != fact:
            result.append(EstimateSyncItem(line.id, model_id, line.name, line.quantity, fact))

    custom_by_est_id = {
        ln.estimate_line_id: ln
        for ln in packing.lines
        if ln.is_custom and ln.estimate_line_id is not None
    }
    for est_line in estimate.lines:
        if not est_line.is_custom:
            continue
        pl = custom_by_est_id.get(est_line.id)
        if pl is not None and est_line.quantity != pl.fact_quantity:
            result.append(
                EstimateSyncItem(est_line.id, None, est_line.name, est_line.quantity, pl.fact_quantity)
            )

    return result


def apply_estimate_sync(
    db: Session, project: Project, packing: PackingList, selected_keys: set[str]
) -> list[EstimateSyncItem]:
    """Обновить количества сметы по факту packing-листа — только для отмеченных позиций.

    Заменяет количество расходящихся строк сметы фактом; для моделей, добавленных
    в packing-лист (вручную или сканом) сверх сметы, создаёт новые строки сметы.
    Позиции сметы без соответствующей строки в packing-листе не трогает. Экспорт
    в смету идёт не всегда по всем позициям — применяются только те, чей
    EstimateSyncItem.key присутствует в selected_keys (отмечены пользователем
    в форме подтверждения).
    """
    items = [it for it in estimate_discrepancies(db, project, packing) if it.key in selected_keys]
    if not items:
        return items

    estimate = get_estimate(db, project)
    if estimate is None:
        return items

    by_id = {ln.id: ln for ln in estimate.lines}
    for item in items:
        if item.is_new:
            model = db.get(EquipmentModel, item.model_id)
            if model is not None:
                estimate_add_model(db, estimate, project, model, item.fact_quantity, merge=False)
        else:
            line = by_id.get(item.estimate_line_id)
            if line is not None:
                line.quantity = item.fact_quantity

    db.commit()
    estimate_sync_reservations(db, project, estimate)
    return items


# --- Строки -------------------------------------------------------------


def add_model(
    db: Session, packing: PackingList, model: EquipmentModel, quantity: int
) -> PackingLine:
    """Добавить складскую модель в packing-лист вручную — как в смете.

    Если строка этой модели уже есть, увеличиваем план (и факт у количественных),
    чтобы не задваивать строки и не сломать сканирование серийных (ТЗ §17.7, §22).
    Для новой строки берётся тот же снимок модели, что и в create_from_estimate.
    """
    quantity = max(1, quantity)
    existing = next(
        (ln for ln in packing.lines if not ln.is_custom and ln.model_id == model.id), None
    )
    if existing is not None:
        existing.planned_quantity += quantity
        if not existing.is_serial:
            existing.quantity += quantity
            if existing.has_packing:
                existing.packed_quantity += quantity
        db.commit()
        return existing

    sort_order = max((ln.sort_order for ln in packing.lines), default=0) + 1
    line = _new_line_from_model(model, quantity, sort_order)
    line.is_manual = True
    packing.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def add_kit(db: Session, packing: PackingList, kit: Kit) -> PackingLine | None:
    """Добавить комплект в packing-лист вручную (структура «Комплект»).

    Комплект — одна позиция; повторно тот же комплект не добавляется.
    """
    if any(ln.kit_id == kit.id for ln in packing.lines):
        return None
    sort_order = max((ln.sort_order for ln in packing.lines), default=0) + 1
    line = _new_line_from_kit(kit, sort_order)
    line.is_manual = True
    packing.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def get_line(db: Session, packing: PackingList, line_id: int) -> PackingLine | None:
    return next((ln for ln in packing.lines if ln.id == line_id), None)


def update_quantity_line(
    db: Session,
    line: PackingLine,
    fact_quantity: int,
    packed_quantity: int,
    comment: str | None,
    *,
    unit_weight_kg: Decimal | None = None,
    length_mm: int | None = None,
    width_mm: int | None = None,
    height_mm: int | None = None,
) -> None:
    """Обновить количественную строку: факт и распределение (ТЗ §17.6, §18).

    Вес/габариты правятся только у дополнительных позиций (ТЗ §17.9) — у складских
    моделей это неизменный снимок модели.
    """
    if line.is_serial:
        raise PackingError("Серийная строка комплектуется экземплярами")
    line.quantity = max(0, fact_quantity)
    line.packed_quantity = max(0, min(packed_quantity, line.quantity))
    line.comment = comment or None
    if line.is_custom:
        if unit_weight_kg is not None:
            line.unit_weight_kg = max(Decimal("0"), unit_weight_kg)
        if length_mm is not None:
            line.length_mm = max(0, length_mm)
        if width_mm is not None:
            line.width_mm = max(0, width_mm)
        if height_mm is not None:
            line.height_mm = max(0, height_mm)
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
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return SerialResult.NOT_FOUND
    if item.model_id != line.model_id:
        return SerialResult.WRONG_MODEL
    if not item.is_usable:
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


class RemoveResult(enum.Enum):
    OK = "ok"
    NOT_IN_PACKING = "not_in_packing"  # штрих-код есть в базе, но не в этом листе
    NOT_FOUND = "not_found"


@dataclass
class RemoveOutcome:
    result: RemoveResult
    barcode: str
    line_id: int | None = None
    model_name: str | None = None
    planned: int = 0
    fact: int = 0

    @property
    def ok(self) -> bool:
        return self.result == RemoveResult.OK


def remove_by_barcode(db: Session, packing: PackingList, barcode: str) -> RemoveOutcome:
    """Убрать конкретный экземпляр из packing-листа по его штрих-коду (независимо от строки)."""
    barcode = barcode.strip()
    item = db.execute(
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return RemoveOutcome(RemoveResult.NOT_FOUND, barcode)

    line = next((L for L in packing.lines if any(si.item_id == item.id for si in L.serial_items)), None)
    if line is None:
        return RemoveOutcome(RemoveResult.NOT_IN_PACKING, barcode, model_name=item.model.name)

    si = next(s for s in line.serial_items if s.item_id == item.id)
    remove_serial_item(db, line, si.id)
    db.refresh(line)
    return RemoveOutcome(
        RemoveResult.OK,
        barcode,
        line_id=line.id,
        model_name=line.name,
        planned=line.planned_quantity,
        fact=line.fact_quantity,
    )


# --- Сканирование (ТЗ §22) ----------------------------------------------


@dataclass
class ScanOutcome:
    result: SerialResult
    barcode: str
    line_id: int | None = None
    serial_item_id: int | None = None
    model_name: str | None = None
    planned: int = 0
    fact: int = 0

    @property
    def ok(self) -> bool:
        """Экземпляр фактически добавлен (OK или подтверждённый сверх плана)."""
        return self.result == SerialResult.OK or self.serial_item_id is not None


def scan(
    db: Session, packing: PackingList, barcode: str, *, allow_over: bool = False
) -> ScanOutcome:
    """Отсканировать штрих-код и сразу засчитать экземпляр в нужную строку (ТЗ §22).

    Строка определяется по модели экземпляра — независимо от того, серийная она
    или количественная: количественное оборудование тоже можно сканировать (по
    решению пользователя), просто у него факт — число (line.quantity), а не
    список экземпляров. Экземпляр в любом случае фиксируется в
    packing_serial_items — штрих-код/S/N нужен для carnet даже у количественных
    моделей. Проверки и вставка — в транзакции; уникальный индекс (строка,
    экземпляр) защищает от конкурентного/повторного добавления.
    """
    barcode = barcode.strip()
    item = db.execute(
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return ScanOutcome(SerialResult.NOT_FOUND, barcode)

    line = next((ln for ln in packing.lines if not ln.is_custom and ln.model_id == item.model_id), None)
    if line is None:
        return ScanOutcome(SerialResult.WRONG_MODEL, barcode, model_name=item.model.name)

    if not item.is_usable:
        return ScanOutcome(
            SerialResult.BLOCKED,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )

    # Уже в этом packing-листе (в любой строке) — повторное добавление (ТЗ §22).
    already = any(si.item_id == item.id for L in packing.lines for si in L.serial_items)
    if already:
        return ScanOutcome(
            SerialResult.DUPLICATE,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )

    over = (line.fact_quantity + 1) > line.planned_quantity
    if over and not allow_over:
        return ScanOutcome(
            SerialResult.OVER_PLAN,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )

    si = PackingSerialItem(item_id=item.id, barcode=item.barcode, serial_number=item.serial_number)
    line.serial_items.append(si)
    if not line.is_serial:
        line.quantity += 1
    if line.has_packing:
        line.packed_quantity += 1
    try:
        db.commit()
    except IntegrityError:  # конкурентное добавление того же экземпляра (ТЗ §22)
        db.rollback()
        db.refresh(line)
        return ScanOutcome(
            SerialResult.DUPLICATE,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )
    db.refresh(line)
    return ScanOutcome(
        SerialResult.OVER_PLAN if over else SerialResult.OK,
        barcode,
        line_id=line.id,
        serial_item_id=si.id,
        model_name=line.name,
        planned=line.planned_quantity,
        fact=line.fact_quantity,
    )


def scan_add_new_model(db: Session, packing: PackingList, barcode: str) -> ScanOutcome:
    """Подтверждённое (или массовое) добавление новой строки плана по сканированию (ТЗ §22).

    Вызывается, когда пользователь сканирует экземпляр модели, которой ещё нет
    в packing-листе — как для серийного, так и для количественного оборудования
    (сканирование количественного разрешено пользователем, см. чат). Для новой
    строки план=факт=1 — add_model() для количественной модели уже выставляет
    факт=план по умолчанию, поэтому счётчик здесь не наращиваем повторно, только
    фиксируем экземпляр в packing_serial_items (штрих-код нужен для carnet). Для
    строки, уже существующей на момент вызова (гонка параллельных сканов), факт
    наращиваем на 1, как и в scan().
    """
    barcode = barcode.strip()
    item = db.execute(
        select(EquipmentItem)
        .where(_sql_normalized_barcode(EquipmentItem.barcode) == normalize_barcode(barcode))
        .order_by(EquipmentItem.id)
        .limit(1)
    ).scalars().first()
    if item is None:
        return ScanOutcome(SerialResult.NOT_FOUND, barcode)
    if not item.is_usable:
        return ScanOutcome(SerialResult.BLOCKED, barcode, model_name=item.model.name)

    already = any(si.item_id == item.id for L in packing.lines for si in L.serial_items)
    if already:
        line = next(L for L in packing.lines if any(si.item_id == item.id for si in L.serial_items))
        return ScanOutcome(
            SerialResult.DUPLICATE,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )

    line = next((ln for ln in packing.lines if not ln.is_custom and ln.model_id == item.model_id), None)
    is_new_line = line is None
    if is_new_line:
        line = add_model(db, packing, item.model, 1)

    si = PackingSerialItem(item_id=item.id, barcode=item.barcode, serial_number=item.serial_number)
    line.serial_items.append(si)
    if line.is_serial:
        if line.has_packing:
            line.packed_quantity += 1
    elif not is_new_line:
        line.quantity += 1
        if line.has_packing:
            line.packed_quantity += 1
    try:
        db.commit()
    except IntegrityError:  # конкурентное добавление того же экземпляра (ТЗ §22)
        db.rollback()
        db.refresh(line)
        return ScanOutcome(
            SerialResult.DUPLICATE,
            barcode,
            line_id=line.id,
            model_name=line.name,
            planned=line.planned_quantity,
            fact=line.fact_quantity,
        )
    db.refresh(line)
    return ScanOutcome(
        SerialResult.OK,
        barcode,
        line_id=line.id,
        serial_item_id=si.id,
        model_name=line.name,
        planned=line.planned_quantity,
        fact=line.fact_quantity,
    )


def add_custom_line(db: Session, packing: PackingList, data: CustomPackingLine) -> PackingLine:
    """Дополнительная позиция: вес/объём учитываются, бронь — нет (ТЗ §17.9)."""
    sort_order = max((ln.sort_order for ln in packing.lines), default=0) + 1
    line = PackingLine(
        packing_list_id=packing.id,
        model_id=None,
        is_custom=True,
        is_serial=False,
        is_manual=True,
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


def category_breakdown(db: Session, packing: PackingList) -> list[CategoryTotal]:
    """Разбивка веса/объёма/энергопотребления по категориям (для packing-листа)."""
    return compute_category_breakdown(packing.lines)


@dataclass
class AccessoryGroup:
    """Аксессуары одной категории с суммарными количествами (для packing-листа)."""

    category_name: str
    items: list[tuple[str, int]]  # (название аксессуара, суммарное количество)
    total: int


def accessory_totals(db: Session, packing: PackingList) -> list[AccessoryGroup]:
    """Живой подсчёт аксессуаров комплектации по packing-листу.

    Для строки-модели: количество аксессуара × факт строки. Для строки-комплекта —
    аксессуары моделей каждой единицы содержимого × факт. Суммируется по всему листу
    и группируется по категориям справочника. Дополнительные позиции — без аксессуаров.
    """
    agg: dict[int, int] = {}
    meta: dict[int, tuple[str, str, int]] = {}  # accessory_id -> (категория, имя, sort)

    def _add(model: EquipmentModel | None, mult: int) -> None:
        if model is None or mult <= 0:
            return
        for ma in model.accessories:
            acc = ma.accessory
            agg[acc.id] = agg.get(acc.id, 0) + mult * ma.quantity
            meta[acc.id] = (acc.category.name, acc.name, acc.sort_order)

    for line in packing.lines:
        if line.is_custom:
            continue
        fact = line.fact_quantity
        if fact <= 0:
            continue
        if line.kit_id is not None:
            kit = kit_service.get_kit(db, line.kit_id)
            if kit is None:
                continue
            # Аксессуары моделей единиц внутри комплекта (структура «Комплект»).
            for item in kit.items:
                _add(item.model, fact)
            continue
        if line.model_id is None:
            continue
        _add(db.get(EquipmentModel, line.model_id), fact)

    by_cat: dict[str, list[tuple[str, int, int]]] = {}
    for accessory_id, qty in agg.items():
        category_name, name, sort = meta[accessory_id]
        by_cat.setdefault(category_name, []).append((name, qty, sort))

    groups: list[AccessoryGroup] = []
    for category_name in sorted(by_cat):
        items = sorted(by_cat[category_name], key=lambda t: (t[2], t[0]))
        groups.append(
            AccessoryGroup(
                category_name=category_name,
                items=[(name, qty) for name, qty, _ in items],
                total=sum(qty for _, qty, _ in items),
            )
        )
    return groups


def project_has_packing(db: Session, project_id: int) -> bool:
    return db.scalar(select(PackingList.id).where(PackingList.project_id == project_id)) is not None
