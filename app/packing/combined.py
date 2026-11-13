"""Общий packing-лист проекта: сводка листов всех стендов (только чтение).

Строки складских моделей разных стендов сливаются по ``model_id``: план, факт и
«в упаковке» суммируются, штрих-коды объединяются, распределение по стендам видно
в ``by_section``. Упаковки, вес и объём — **сумма расчётов строк-источников**
(``LineCalc``), а не пересчёт от суммарного количества: кейсы стендов физически
разные. Комплекты, комплекты аксессуаров и дополнительные позиции не сливаются и
помечены своим стендом.

``CombinedLine`` повторяет атрибуты ``PackingLine``, которые читают шаблоны
документов и carnet, поэтому лист стенда и общий лист выводятся одними шаблонами.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.packing.calc import (
    CategoryTotal,
    LineCalc,
    PackingTotals,
    compute_category_breakdown,
    compute_line,
    compute_totals,
    volume,
    weight,
)
from app.packing.enums import PackingStatus
from app.packing.models import PackingLine, PackingList, PackingSerialItem
from app.packing.service import AccessoryGroup, accessory_totals_for_lines, list_packings
from app.projects.models import Project


@dataclass
class SectionShare:
    """Доля стенда в строке общего листа."""

    section_id: int
    section_name: str
    packing_id: int
    planned: int
    fact: int


def sum_calcs(calcs: list[LineCalc]) -> LineCalc:
    """Сумма расчётов строк (упаковки и вес считаются по каждой строке отдельно)."""
    return LineCalc(
        planned=sum(c.planned for c in calcs),
        fact=sum(c.fact for c in calcs),
        packed=sum(c.packed for c in calcs),
        unpacked=sum(c.unpacked for c in calcs),
        packages=sum(c.packages for c in calcs),
        equipment_weight=weight(sum((c.equipment_weight for c in calcs), Decimal("0"))),
        packaging_weight=weight(sum((c.packaging_weight for c in calcs), Decimal("0"))),
        total_weight=weight(sum((c.total_weight for c in calcs), Decimal("0"))),
        equipment_volume=volume(sum((c.equipment_volume for c in calcs), Decimal("0"))),
        package_volume=volume(sum((c.package_volume for c in calcs), Decimal("0"))),
        total_volume=volume(sum((c.total_volume for c in calcs), Decimal("0"))),
        power_peak_w=sum(c.power_peak_w for c in calcs),
        power_nominal_w=sum(c.power_nominal_w for c in calcs),
    )


@dataclass
class CombinedLine:
    """Строка общего листа: одна или несколько строк листов стендов."""

    sources: list[PackingLine]
    by_section: list[SectionShare]
    merged: bool  # строка складской модели (сливается по model_id)
    position: int = 0  # порядок стенда первой строки-источника
    calc: LineCalc | None = None  # сумма LineCalc источников (см. combine_lines)

    # --- атрибуты PackingLine (по первой строке-источнику) ---------------------
    def __getattr__(self, name: str):
        # Снимки модели (имя, категория, габариты, упаковка, питание) у строк
        # одной модели совпадают — берём у первой строки.
        if name in _FIRST_SOURCE_ATTRS:
            return getattr(self.sources[0], name)
        raise AttributeError(name)

    @property
    def planned_quantity(self) -> int:
        return sum(ln.planned_quantity for ln in self.sources)

    @property
    def fact_quantity(self) -> int:
        return sum(ln.fact_quantity for ln in self.sources)

    @property
    def packed_quantity(self) -> int:
        return sum(ln.packed_quantity for ln in self.sources)

    @property
    def serial_items(self) -> list[PackingSerialItem]:
        return [si for ln in self.sources for si in ln.serial_items]

    @property
    def shown_serial_items(self) -> list[PackingSerialItem]:
        # «Подтверждённые, иначе все» — по каждому стенду отдельно: заготовки
        # стенда B не должны пропасть из-за того, что стенд A уже отсканирован.
        return [si for ln in self.sources for si in ln.shown_serial_items]

    @property
    def comment(self) -> str | None:
        comments = [
            (share.section_name, ln.comment)
            for share, ln in zip(self.by_section, self.sources, strict=True)
            if ln.comment
        ]
        if not comments:
            return None
        if len(self.sources) == 1:
            return comments[0][1]
        return "; ".join(f"{name}: {text}" for name, text in comments)

    def stands_lines(self, plan_fact: bool = False) -> list[str]:
        """Распределение по стендам, строка на стенд. Для моделей — «A: 4» по факту
        (по плану, пока факта нет) или «A: 4 / 3» (план / факт) при ``plan_fact``;
        для комплектов и дополнительных позиций — только имя стенда."""
        if not self.merged:
            return [self.by_section[0].section_name]
        if plan_fact:
            return [f"{s.section_name}: {s.planned} / {s.fact}" for s in self.by_section]
        use_fact = self.fact_quantity > 0
        return [f"{s.section_name}: {s.fact if use_fact else s.planned}" for s in self.by_section]

    @property
    def stands_text(self) -> str:
        """«A: 4 · B: 2» — распределение по стендам одной строкой."""
        return " · ".join(self.stands_lines())


_FIRST_SOURCE_ATTRS = frozenset(
    {
        "id",
        "model_id",
        "kit_id",
        "accessory_kit_id",
        "is_custom",
        "is_serial",
        "is_kit",
        "is_accessory_kit",
        "name",
        "category_id",
        "category_name",
        "subcategory_name",
        "sort_order",
        "unit_weight_kg",
        "length_mm",
        "width_mm",
        "height_mm",
        "has_power",
        "power_peak_w",
        "power_nominal_w",
        "has_packing",
        "pack_capacity",
        "pack_empty_weight_kg",
        "pack_length_mm",
        "pack_width_mm",
        "pack_height_mm",
    }
)


def _share(packing: PackingList, line: PackingLine) -> SectionShare:
    return SectionShare(
        section_id=packing.section_id,
        section_name=packing.section.name,
        packing_id=packing.id,
        planned=line.planned_quantity,
        fact=line.fact_quantity,
    )


def combine_lines(packings: list[PackingList]) -> list[CombinedLine]:
    """Строки общего листа в порядке вывода (категория → подкатегория → имя → стенд)."""
    by_model: dict[int, CombinedLine] = {}
    result: list[CombinedLine] = []
    for pos, packing in enumerate(packings):
        for line in sorted(packing.lines, key=lambda ln: (ln.sort_order, ln.id)):
            mergeable = (
                line.model_id is not None
                and not line.is_custom
                and line.kit_id is None
                and line.accessory_kit_id is None
            )
            if mergeable and line.model_id in by_model:
                combined = by_model[line.model_id]
                combined.sources.append(line)
                combined.by_section.append(_share(packing, line))
                continue
            combined = CombinedLine(
                sources=[line],
                by_section=[_share(packing, line)],
                merged=mergeable,
                position=pos,
            )
            if mergeable:
                by_model[line.model_id] = combined
            result.append(combined)
    for combined in result:
        combined.calc = sum_calcs([compute_line(ln) for ln in combined.sources])
    return sorted(
        result,
        key=lambda c: (
            c.is_custom,
            c.category_name or "",
            c.subcategory_name or "",
            c.name.casefold(),
            c.position,
            c.id,
        ),
    )


def combined_status(packings: list[PackingList]) -> PackingStatus:
    """Статус проекта по листам: все скомплектованы / ни один не начат / комплектуется."""
    statuses = {pl.status for pl in packings}
    if statuses == {PackingStatus.PICKED}:
        return PackingStatus.PICKED
    if not statuses or statuses == {PackingStatus.NOT_STARTED}:
        return PackingStatus.NOT_STARTED
    return PackingStatus.PICKING


@dataclass
class CombinedPacking:
    packings: list[PackingList]
    lines: list[CombinedLine]
    totals: PackingTotals
    breakdown: list[CategoryTotal]
    accessories: list[AccessoryGroup]
    shortages: list[tuple[str, str]]  # (стенд, комментарий о недостаче)
    status: PackingStatus

    @property
    def planned(self) -> int:
        return sum(c.planned_quantity for c in self.lines)

    @property
    def fact(self) -> int:
        return sum(c.fact_quantity for c in self.lines)

    @property
    def source_lines(self) -> list[PackingLine]:
        """Строки листов стендов в порядке общего листа."""
        return [ln for c in self.lines for ln in c.sources]


def build(
    db: Session,
    packings: list[PackingList],
    **breakdown_labels: str,
) -> CombinedPacking:
    """Общий лист по заданным листам стендов. ``breakdown_labels`` — локализованные
    метки групп разбивки (см. ``compute_category_breakdown``)."""
    lines = combine_lines(packings)
    source = [ln for c in lines for ln in c.sources]
    # Итоги и разбивка — сумма по строкам листов стендов (= сумма LineCalc источников).
    return CombinedPacking(
        packings=packings,
        lines=lines,
        totals=compute_totals(source),
        breakdown=compute_category_breakdown(source, **breakdown_labels),
        accessories=accessory_totals_for_lines(db, source),
        shortages=[
            (pl.section.name, pl.shortage_comment) for pl in packings if pl.shortage_comment
        ],
        status=combined_status(packings),
    )


def combined_packing(db: Session, project: Project, **breakdown_labels: str) -> CombinedPacking:
    """Общий packing-лист проекта по листам всех стендов."""
    return build(db, list_packings(db, project), **breakdown_labels)
