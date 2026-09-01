"""Бизнес-логика сметы: строки, итоги, синхронизация брони (ТЗ §16, §15)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.accessory_kits.models import AccessoryKit, AccessoryKitLine
from app.database import utcnow
from app.estimates.models import Estimate, EstimateLine
from app.estimates.schemas import CustomLineInput, LineUpdate
from app.estimates.totals import EstimateTotals, compute_totals
from app.inventory.models import EquipmentModel, Kit
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.projects.models import Project, ProjectReservation


class EstimateError(Exception):
    """Ошибка домена сметы."""


def _current_year() -> int:
    from app.utils.timezone import to_local

    return to_local(utcnow()).year


def get_estimate(db: Session, project: Project) -> Estimate | None:
    stmt = (
        select(Estimate)
        .options(selectinload(Estimate.lines))
        .where(Estimate.project_id == project.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_or_create_estimate(db: Session, project: Project) -> Estimate:
    """Смета проекта (создаётся при первом обращении) с номером EST-YYYY-NNN (ТЗ §14)."""
    estimate = get_estimate(db, project)
    if estimate is not None:
        return estimate
    estimate = Estimate(
        project_id=project.id,
        number=next_number(db, DocType.ESTIMATE, _current_year()),
        discount_percent=Decimal("0"),
    )
    db.add(estimate)
    db.commit()
    db.refresh(estimate)
    return estimate


def _next_sort_order(estimate: Estimate) -> int:
    return (max((ln.sort_order for ln in estimate.lines), default=0)) + 1


def _clamp_percent(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value))


def add_model(
    db: Session,
    estimate: Estimate,
    project: Project,
    model: EquipmentModel,
    quantity: int,
    *,
    merge: bool = True,
    discount_percent: Decimal = Decimal("0"),
) -> EstimateLine:
    """Добавить складскую модель в смету.

    При merge=True и наличии строки той же модели — увеличить количество,
    иначе создать отдельную строку (ТЗ §16.2). Цена и коэффициент берутся из
    модели и проекта как снимок и далее редактируются в смете (ТЗ §16.3).
    """
    if merge:
        for line in estimate.lines:
            if not line.is_custom and line.model_id == model.id:
                line.quantity += quantity
                db.commit()
                sync_reservations(db, project, estimate)
                return line

    line = EstimateLine(
        estimate_id=estimate.id,
        model_id=model.id,
        is_custom=False,
        name=model.name,
        category_id=model.category_id,
        category_name=model.category.name if model.category else None,
        manufacturer=model.manufacturer,
        quantity=quantity,
        unit_price=model.base_price_eur,
        coefficient=project.rental_coefficient,
        discount_percent=_clamp_percent(discount_percent),
        sort_order=_next_sort_order(estimate),
    )
    estimate.lines.append(line)
    db.commit()
    db.refresh(estimate)
    sync_reservations(db, project, estimate)
    return line


KIT_GROUP_NAME = "Комплекты"
ACCESSORY_KIT_GROUP_NAME = "Комплекты аксессуаров"


def add_accessory_kit_line(
    db: Session, estimate: Estimate, project: Project, kit: AccessoryKit
) -> EstimateLine | None:
    """Добавить комплект аксессуаров в смету одной строкой — только название.

    Содержимое в смете не показывается (см. app.accessory_kits). Комплект
    аксессуаров существует в одном экземпляре проекта, количество всегда 1;
    цена — редактируемый снимок (по умолчанию 0), как и у складского Комплекта.
    """
    if any(ln.accessory_kit_id == kit.id for ln in estimate.lines):
        return None
    line = EstimateLine(
        estimate_id=estimate.id,
        accessory_kit_id=kit.id,
        is_custom=False,
        name=kit.name,
        quantity=1,
        unit_price=Decimal("0"),
        coefficient=Decimal("1"),
        sort_order=_next_sort_order(estimate),
    )
    estimate.lines.append(line)
    db.commit()
    db.refresh(estimate)
    return line


def add_kit_line(
    db: Session, estimate: Estimate, project: Project, kit: Kit
) -> EstimateLine | None:
    """Добавить комплект в смету одной строкой — только название (структура «Комплект»).

    Комплект существует в одном экземпляре, поэтому количество всегда 1 и повторно
    один и тот же комплект не добавляется. Цена — редактируемый снимок (по умолчанию 0).
    """
    if any(ln.kit_id == kit.id for ln in estimate.lines):
        return None
    line = EstimateLine(
        estimate_id=estimate.id,
        model_id=None,
        kit_id=kit.id,
        is_custom=False,
        name=kit.name,
        category_id=None,
        category_name=KIT_GROUP_NAME,
        manufacturer=None,
        quantity=1,
        unit_price=Decimal("0"),
        coefficient=project.rental_coefficient,
        discount_percent=Decimal("0"),
        sort_order=_next_sort_order(estimate),
    )
    estimate.lines.append(line)
    db.commit()
    db.refresh(estimate)
    sync_reservations(db, project, estimate)
    return line


def add_custom_line(
    db: Session, estimate: Estimate, project: Project, data: CustomLineInput
) -> EstimateLine:
    """Добавить произвольную строку (ТЗ §16.5): только стоимость/PDF, без брони."""
    line = EstimateLine(
        estimate_id=estimate.id,
        model_id=None,
        is_custom=True,
        name=data.name.strip(),
        category_id=None,
        category_name=None,
        manufacturer=None,
        quantity=data.quantity,
        unit_price=data.unit_price,
        coefficient=data.coefficient,
        discount_percent=_clamp_percent(data.discount_percent),
        comment=(data.comment or None),
        add_to_packing=data.add_to_packing,
        sort_order=_next_sort_order(estimate),
    )
    estimate.lines.append(line)
    db.commit()
    db.refresh(line)
    return line


def update_line(db: Session, line: EstimateLine, data: LineUpdate) -> EstimateLine:
    # Комплект — одна бронируемая позиция: количество всегда 1 («Комплект»).
    line.quantity = 1 if line.is_kit else data.quantity
    line.unit_price = data.unit_price
    line.coefficient = data.coefficient
    line.discount_percent = _clamp_percent(data.discount_percent)
    line.comment = data.comment or None
    db.commit()
    db.refresh(line)
    return line


def delete_line(db: Session, line: EstimateLine) -> None:
    db.delete(line)
    db.commit()


def move_line(db: Session, estimate: Estimate, line: EstimateLine, direction: int) -> None:
    """Переместить строку вверх/вниз в пределах своей категории (ТЗ §16.6)."""
    same_group = [
        ln
        for ln in estimate.lines
        if ln.is_custom == line.is_custom and ln.category_id == line.category_id
    ]
    same_group.sort(key=lambda ln: (ln.sort_order, ln.id))
    idx = same_group.index(line)
    swap = idx + direction
    if 0 <= swap < len(same_group):
        other = same_group[swap]
        line.sort_order, other.sort_order = other.sort_order, line.sort_order
        db.commit()


def set_discount(db: Session, estimate: Estimate, percent: Decimal) -> None:
    estimate.discount_percent = max(Decimal("0"), min(Decimal("100"), percent))
    db.commit()


def totals(db: Session, estimate: Estimate, project: Project) -> EstimateTotals:
    return compute_totals(
        [ln.line_total for ln in estimate.lines], estimate.discount_percent, project.vat
    )


@dataclass
class LineGroup:
    category_id: int | None
    category_name: str
    lines: list[EstimateLine]


def grouped_lines(
    estimate: Estimate,
    *,
    kit_label: str = KIT_GROUP_NAME,
    accessory_kit_label: str = ACCESSORY_KIT_GROUP_NAME,
    custom_label: str = "Прочее",
    no_category_label: str = "Без категории",
) -> list[LineGroup]:
    """Сгруппировать строки по категориям; комплекты — в ``kit_label``, комплекты
    аксессуаров — в ``accessory_kit_label``, произвольные — в ``custom_label``
    (ТЗ §16.6, структура «Комплект»).

    Метки по умолчанию — русские (для веб-интерфейса сметы); PDF-рендер (документы
    на en/de) передаёт локализованные метки явно (ТЗ §26.1). Группа определяется по
    ``line.is_kit``/``is_accessory_kit``/``is_custom``, а не по сохранённому тексту
    ``category_name`` — у строк-комплектов там снимок группового имени (см.
    add_kit_line/add_accessory_kit_line), который сам по себе не переводится.
    """
    groups: dict[object, LineGroup] = {}
    for line in sorted(estimate.lines, key=lambda ln: (ln.sort_order, ln.id)):
        if line.is_kit:
            key: object = "kit"
            name = kit_label
        elif line.is_accessory_kit:
            key = "accessory_kit"
            name = accessory_kit_label
        elif line.is_custom:
            key = None
            name = custom_label
        else:
            key = line.category_id
            name = line.category_name or no_category_label
        if key not in groups:
            cat_id = line.category_id if isinstance(key, int) else None
            groups[key] = LineGroup(category_id=cat_id, category_name=name, lines=[])
        groups[key].lines.append(line)

    # Комплекты — первыми, затем комплекты аксессуаров, категории по имени,
    # произвольные (custom) в конце.
    def _order(item: tuple[object, LineGroup]) -> tuple:
        key, g = item
        if key == "kit":
            return (0, "0")
        if key == "accessory_kit":
            return (0, "1")
        return (1 if g.category_id is None else 0, g.category_name)

    return [g for _, g in sorted(groups.items(), key=_order)]


def sync_reservations(db: Session, project: Project, estimate: Estimate) -> None:
    """Синхронизировать бронь проекта со складскими строками сметы (ТЗ §15).

    Бронь модели = сумма количеств по модели среди складских строк сметы плюс
    содержимое всех комплектов аксессуаров проекта, взятое со склада (кабели/
    коннекторы физически уезжают с проектом, даже если в смете это одна строка
    «Комплект аксессуаров» без раскрытия состава — см. app.accessory_kits).
    Бронь комплекта («Комплект») = 1 за каждую строку-комплект. Влияет на доступность
    только когда проект «Забронирован», поэтому синхронизировать можно всегда.
    """
    wanted_models: dict[int, int] = {}
    wanted_kits: set[int] = set()
    for line in estimate.lines:
        if line.is_custom:
            continue
        if line.kit_id is not None:
            wanted_kits.add(line.kit_id)
        elif line.model_id is not None:
            wanted_models[line.model_id] = wanted_models.get(line.model_id, 0) + line.quantity

    accessory_lines = (
        db.execute(
            select(AccessoryKitLine)
            .join(AccessoryKit, AccessoryKit.id == AccessoryKitLine.accessory_kit_id)
            .where(AccessoryKit.project_id == project.id, AccessoryKitLine.model_id.is_not(None))
        )
        .scalars()
        .all()
    )
    for aline in accessory_lines:
        wanted_models[aline.model_id] = wanted_models.get(aline.model_id, 0) + aline.quantity

    reservations = (
        db.execute(select(ProjectReservation).where(ProjectReservation.project_id == project.id))
        .scalars()
        .all()
    )
    existing_models = {res.model_id: res for res in reservations if res.model_id is not None}
    existing_kits = {res.kit_id: res for res in reservations if res.kit_id is not None}

    for model_id, qty in wanted_models.items():
        if model_id in existing_models:
            existing_models[model_id].quantity = qty
        else:
            db.add(ProjectReservation(project_id=project.id, model_id=model_id, quantity=qty))
    for model_id, res in existing_models.items():
        if model_id not in wanted_models:
            db.delete(res)

    for kit_id in wanted_kits:
        if kit_id not in existing_kits:
            db.add(ProjectReservation(project_id=project.id, kit_id=kit_id, quantity=1))
    for kit_id, res in existing_kits.items():
        if kit_id not in wanted_kits:
            db.delete(res)
    db.commit()


def copy_estimate(db: Session, source: Project, target: Project) -> Estimate:
    """Скопировать смету при копировании проекта (ТЗ §13.8)."""
    src = get_estimate(db, source)
    dst = get_or_create_estimate(db, target)
    if src is None:
        return dst
    dst.discount_percent = src.discount_percent
    for line in sorted(src.lines, key=lambda ln: (ln.sort_order, ln.id)):
        dst.lines.append(
            EstimateLine(
                model_id=line.model_id,
                kit_id=line.kit_id,
                is_custom=line.is_custom,
                name=line.name,
                category_id=line.category_id,
                category_name=line.category_name,
                manufacturer=line.manufacturer,
                quantity=line.quantity,
                unit_price=line.unit_price,
                coefficient=line.coefficient,
                discount_percent=line.discount_percent,
                comment=line.comment,
                sort_order=line.sort_order,
            )
        )
    db.commit()
    sync_reservations(db, target, dst)
    return dst
