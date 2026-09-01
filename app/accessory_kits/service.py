"""Бизнес-логика комплекта аксессуаров: содержимое, вес, шаблоны, резерв склада.

Комплект аксессуаров существует в рамках проекта (см. докстринг models.py).
Вес/габариты редактируются на протяжении всей сборки packing-листа, поэтому,
в отличие от прочих снимков в проекте, при изменении содержимого/настроек веса
они «проталкиваются» в уже существующую строку packing-листа (см.
``_push_to_packing_line``) — иначе цифры в packing-листе быстро разойдутся
с фактическим содержимым кейса.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.accessory_kits.models import (
    AccessoryKit,
    AccessoryKitLine,
    AccessoryKitTemplate,
    AccessoryKitTemplateLine,
)
from app.accessory_kits.schemas import (
    AccessoryKitInput,
    AccessoryKitTemplateInput,
    CustomAccessoryKitLine,
    TemplateCustomLine,
)
from app.inventory.enums import KitWeightMode
from app.inventory.models import EquipmentModel
from app.inventory.services.items import _sql_normalized_barcode, normalize_barcode
from app.projects.models import Project


class AccessoryKitError(Exception):
    """Ошибка домена комплекта аксессуаров."""


class InUse(AccessoryKitError):
    """Комплект используется в смете/packing-листе — действие недоступно."""


class DuplicateBarcode(AccessoryKitError):
    """Штрих-код уже используется другим комплектом аксессуаров."""


# --- Комплекты проекта ---------------------------------------------------


def list_kits(db: Session, project: Project) -> list[AccessoryKit]:
    stmt = (
        select(AccessoryKit)
        .options(selectinload(AccessoryKit.lines))
        .where(AccessoryKit.project_id == project.id)
        .order_by(AccessoryKit.sort_order, AccessoryKit.id)
    )
    return list(db.execute(stmt).scalars().all())


def get_kit(db: Session, project: Project, kit_id: int) -> AccessoryKit | None:
    """Комплект этого проекта (защита от чужого/произвольного kit_id)."""
    stmt = (
        select(AccessoryKit)
        .options(selectinload(AccessoryKit.lines))
        .where(AccessoryKit.id == kit_id, AccessoryKit.project_id == project.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def get(db: Session, kit_id: int) -> AccessoryKit | None:
    """Комплект по id без проверки проекта — для внутренней синхронизации со сметой."""
    stmt = (
        select(AccessoryKit).options(selectinload(AccessoryKit.lines)).where(AccessoryKit.id == kit_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def find_by_barcode(db: Session, barcode: str) -> AccessoryKit | None:
    """Комплект по штрих-коду кейса — для сканирования на погрузке/возврате (ТЗ §22, §56.3).

    Штрих-код уникален глобально (см. AccessoryKit.barcode), поэтому поиск не
    требует привязки к проекту — вызывающий код сам проверяет, что найденный
    комплект относится к текущему packing-листу/листу приёмки.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return None
    stmt = (
        select(AccessoryKit)
        .options(selectinload(AccessoryKit.lines))
        .where(_sql_normalized_barcode(AccessoryKit.barcode) == normalize_barcode(barcode))
    )
    return db.execute(stmt).scalar_one_or_none()


def _check_barcode(db: Session, barcode: str | None, *, exclude_id: int | None = None) -> None:
    if not barcode:
        return
    stmt = select(AccessoryKit.id).where(
        _sql_normalized_barcode(AccessoryKit.barcode) == normalize_barcode(barcode)
    )
    if exclude_id is not None:
        stmt = stmt.where(AccessoryKit.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise DuplicateBarcode("Штрих-код уже используется другим комплектом аксессуаров")


def _weight_value_for(data: AccessoryKitInput) -> Decimal | None:
    if data.weight_mode == KitWeightMode.CONTENT:
        return None
    return data.weight_value


def create_kit(db: Session, project: Project, data: AccessoryKitInput) -> AccessoryKit:
    barcode = (data.barcode or "").strip() or None
    _check_barcode(db, barcode)
    kit = AccessoryKit(
        project_id=project.id,
        name=data.name.strip(),
        barcode=barcode,
        weight_mode=data.weight_mode,
        weight_value=_weight_value_for(data),
        length_mm=data.length_mm,
        width_mm=data.width_mm,
        height_mm=data.height_mm,
        comment=(data.comment or None),
        sort_order=max((k.sort_order for k in list_kits(db, project)), default=0) + 1,
    )
    db.add(kit)
    try:
        db.commit()
    except IntegrityError as exc:  # гонка на уникальном индексе штрих-кода
        db.rollback()
        raise DuplicateBarcode("Штрих-код уже используется другим комплектом аксессуаров") from exc
    db.refresh(kit)
    return kit


def update_kit(db: Session, project: Project, kit: AccessoryKit, data: AccessoryKitInput) -> None:
    barcode = (data.barcode or "").strip() or None
    _check_barcode(db, barcode, exclude_id=kit.id)
    kit.name = data.name.strip()
    kit.barcode = barcode
    kit.weight_mode = data.weight_mode
    kit.weight_value = _weight_value_for(data)
    kit.length_mm = data.length_mm
    kit.width_mm = data.width_mm
    kit.height_mm = data.height_mm
    kit.comment = data.comment or None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateBarcode("Штрих-код уже используется другим комплектом аксессуаров") from exc
    _push_to_packing_line(db, project, kit)


def is_in_estimate(db: Session, kit_id: int) -> bool:
    from app.estimates.models import EstimateLine

    return (
        db.scalar(select(EstimateLine.id).where(EstimateLine.accessory_kit_id == kit_id)) is not None
    )


def delete_kit(db: Session, project: Project, kit: AccessoryKit) -> None:
    if is_in_estimate(db, kit.id):
        raise InUse("Комплект аксессуаров добавлен в смету — сначала уберите его из сметы")
    db.delete(kit)
    db.commit()
    _resync_reservations(db, project)


def move_kit(db: Session, project: Project, kit: AccessoryKit, direction: int) -> None:
    kits = list_kits(db, project)
    kits.sort(key=lambda k: (k.sort_order, k.id))
    idx = kits.index(kit)
    swap = idx + direction
    if 0 <= swap < len(kits):
        other = kits[swap]
        kit.sort_order, other.sort_order = other.sort_order, kit.sort_order
        db.commit()


# --- Содержимое ------------------------------------------------------------


def add_model_line(
    db: Session, project: Project, kit: AccessoryKit, model: EquipmentModel, quantity: int
) -> AccessoryKitLine:
    """Добавить складскую модель в содержимое — количественно, без привязки к единицам.

    Если позиция этой модели уже есть, увеличиваем количество, а не задваиваем строку.
    """
    quantity = max(1, quantity)
    existing = next((ln for ln in kit.lines if not ln.is_custom and ln.model_id == model.id), None)
    if existing is not None:
        existing.quantity += quantity
        db.commit()
    else:
        line = AccessoryKitLine(
            accessory_kit_id=kit.id,
            model_id=model.id,
            is_custom=False,
            name=model.name,
            unit_weight_kg=model.weight_kg,
            quantity=quantity,
            sort_order=max((ln.sort_order for ln in kit.lines), default=0) + 1,
        )
        kit.lines.append(line)
        db.commit()
        db.refresh(kit)
    _push_to_packing_line(db, project, kit)
    _resync_reservations(db, project)
    return next(ln for ln in kit.lines if not ln.is_custom and ln.model_id == model.id)


def add_custom_line(
    db: Session, project: Project, kit: AccessoryKit, data: CustomAccessoryKitLine
) -> AccessoryKitLine:
    line = AccessoryKitLine(
        accessory_kit_id=kit.id,
        model_id=None,
        is_custom=True,
        name=data.name.strip(),
        unit_weight_kg=data.unit_weight_kg,
        quantity=data.quantity,
        comment=(data.comment or None),
        sort_order=max((ln.sort_order for ln in kit.lines), default=0) + 1,
    )
    kit.lines.append(line)
    db.commit()
    db.refresh(line)
    _push_to_packing_line(db, project, kit)
    return line


def update_line(
    db: Session,
    project: Project,
    kit: AccessoryKit,
    line: AccessoryKitLine,
    *,
    quantity: int,
    comment: str | None,
    name: str | None = None,
    unit_weight_kg: Decimal | None = None,
) -> None:
    line.quantity = max(1, quantity)
    line.comment = comment or None
    if line.is_custom:
        if name is not None and name.strip():
            line.name = name.strip()
        if unit_weight_kg is not None:
            line.unit_weight_kg = max(Decimal("0"), unit_weight_kg)
    db.commit()
    _push_to_packing_line(db, project, kit)
    if not line.is_custom:
        _resync_reservations(db, project)


def delete_line(db: Session, project: Project, kit: AccessoryKit, line: AccessoryKitLine) -> None:
    was_stock = not line.is_custom
    kit.lines.remove(line)
    db.delete(line)
    db.commit()
    _push_to_packing_line(db, project, kit)
    if was_stock:
        _resync_reservations(db, project)


def move_line(db: Session, kit: AccessoryKit, line: AccessoryKitLine, direction: int) -> None:
    lines = sorted(kit.lines, key=lambda ln: (ln.sort_order, ln.id))
    idx = lines.index(line)
    swap = idx + direction
    if 0 <= swap < len(lines):
        other = lines[swap]
        line.sort_order, other.sort_order = other.sort_order, line.sort_order
        db.commit()


# --- Вес --------------------------------------------------------------------


def content_weight(kit: AccessoryKit) -> Decimal:
    total = Decimal("0")
    for line in kit.lines:
        total += line.unit_weight_kg * line.quantity
    return total


def total_weight(kit: AccessoryKit) -> Decimal:
    """Расчётный вес комплекта для packing, с учётом настройки веса (как у Kit).

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


def _push_to_packing_line(db: Session, project: Project, kit: AccessoryKit) -> None:
    """Обновить снимок веса/габаритов в уже существующей строке packing-листа.

    Содержимое кабелярки обычно достраивается уже после того, как строка в
    packing-листе создана (packing — это и есть место сборки) — если не
    протолкнуть новый вес в снимок строки, цифры в packing-листе (и итоговый
    вес проекта) быстро разойдутся с фактическим содержимым кейса.
    """
    from app.packing.models import PackingLine

    line = db.execute(
        select(PackingLine).where(PackingLine.accessory_kit_id == kit.id)
    ).scalar_one_or_none()
    if line is None:
        return
    line.unit_weight_kg = total_weight(kit)
    line.length_mm = kit.length_mm
    line.width_mm = kit.width_mm
    line.height_mm = kit.height_mm
    db.commit()


def _resync_reservations(db: Session, project: Project) -> None:
    """Пересчитать резерв склада проекта — содержимое кабелярок учитывается наравне со сметой.

    Смета создаётся при первом обращении (get_or_create_estimate), даже если
    пользователь ещё не открывал вкладку «Смета» — резерв под содержимое
    кабелярки должен работать независимо от того, когда именно это произойдёт,
    иначе кабели рискуют быть задвоенными между проектами до первого визита в смету.
    """
    from app.estimates.service import get_or_create_estimate, sync_reservations

    estimate = get_or_create_estimate(db, project)
    sync_reservations(db, project, estimate)


# --- Шаблоны -----------------------------------------------------------------


def list_templates(db: Session) -> list[AccessoryKitTemplate]:
    stmt = (
        select(AccessoryKitTemplate)
        .options(selectinload(AccessoryKitTemplate.lines))
        .order_by(AccessoryKitTemplate.name)
    )
    return list(db.execute(stmt).scalars().all())


def get_template(db: Session, template_id: int) -> AccessoryKitTemplate | None:
    stmt = (
        select(AccessoryKitTemplate)
        .options(selectinload(AccessoryKitTemplate.lines))
        .where(AccessoryKitTemplate.id == template_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_template(db: Session, data: AccessoryKitTemplateInput) -> AccessoryKitTemplate:
    template = AccessoryKitTemplate(name=data.name.strip(), comment=(data.comment or None))
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def update_template(
    db: Session, template: AccessoryKitTemplate, data: AccessoryKitTemplateInput
) -> None:
    template.name = data.name.strip()
    template.comment = data.comment or None
    db.commit()


def delete_template(db: Session, template: AccessoryKitTemplate) -> None:
    db.delete(template)
    db.commit()


def add_template_model_line(
    db: Session, template: AccessoryKitTemplate, model: EquipmentModel, quantity: int
) -> AccessoryKitTemplateLine:
    existing = next(
        (ln for ln in template.lines if not ln.is_custom and ln.model_id == model.id), None
    )
    if existing is not None:
        existing.quantity += max(1, quantity)
        db.commit()
        return existing
    line = AccessoryKitTemplateLine(
        template_id=template.id,
        model_id=model.id,
        is_custom=False,
        name=model.name,
        quantity=max(1, quantity),
        sort_order=max((ln.sort_order for ln in template.lines), default=0) + 1,
    )
    template.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def add_template_custom_line(
    db: Session, template: AccessoryKitTemplate, data: TemplateCustomLine
) -> AccessoryKitTemplateLine:
    line = AccessoryKitTemplateLine(
        template_id=template.id,
        model_id=None,
        is_custom=True,
        name=data.name.strip(),
        quantity=data.quantity,
        comment=(data.comment or None),
        sort_order=max((ln.sort_order for ln in template.lines), default=0) + 1,
    )
    template.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def delete_template_line(
    db: Session, template: AccessoryKitTemplate, line: AccessoryKitTemplateLine
) -> None:
    template.lines.remove(line)
    db.delete(line)
    db.commit()


def create_from_template(
    db: Session, project: Project, template: AccessoryKitTemplate, *, name: str | None = None
) -> AccessoryKit:
    """Создать комплект аксессуаров проекта, скопировав состав шаблона (снимком).

    Штрих-код и вес/габариты — физические атрибуты конкретного кейса, из шаблона
    не переносятся, задаются на месте.
    """
    kit = create_kit(
        db,
        project,
        AccessoryKitInput(name=(name or template.name)),
    )
    for tline in sorted(template.lines, key=lambda ln: (ln.sort_order, ln.id)):
        model = db.get(EquipmentModel, tline.model_id) if tline.model_id else None
        line = AccessoryKitLine(
            accessory_kit_id=kit.id,
            model_id=tline.model_id,
            is_custom=tline.is_custom,
            name=tline.name,
            unit_weight_kg=(model.weight_kg if model else Decimal("0")),
            quantity=tline.quantity,
            comment=tline.comment,
            sort_order=tline.sort_order,
        )
        kit.lines.append(line)
    db.commit()
    db.refresh(kit)
    if any(not ln.is_custom for ln in kit.lines):
        _resync_reservations(db, project)
    return kit


@dataclass
class AccessoryKitSummary:
    """Сводка по комплекту для списков (вес, число позиций, наличие в смете)."""

    kit: AccessoryKit
    item_count: int
    weight_kg: Decimal
    in_estimate: bool


def summaries(db: Session, project: Project) -> list[AccessoryKitSummary]:
    from app.estimates.models import EstimateLine

    kits = list_kits(db, project)
    in_estimate_ids: set[int] = set()
    for eid in db.execute(
        select(EstimateLine.accessory_kit_id).where(
            EstimateLine.accessory_kit_id.in_([k.id for k in kits])
        )
    ).scalars():
        if eid is not None:
            in_estimate_ids.add(eid)
    return [
        AccessoryKitSummary(
            kit=k,
            item_count=len(k.lines),
            weight_kg=total_weight(k),
            in_estimate=k.id in in_estimate_ids,
        )
        for k in kits
    ]
