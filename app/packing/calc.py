"""Расчёты packing-листа: упаковки, вес, объём, план/факт (ТЗ §12, §18–§20)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.packing.models import PackingLine

WEIGHT_Q = Decimal("0.1")  # вес — один знак после запятой (ТЗ §19)
VOLUME_Q = Decimal("0.001")  # объём в м³ (ТЗ §20)
_MM3_IN_M3 = Decimal("1000000000")


def weight(value: Decimal) -> Decimal:
    return Decimal(value).quantize(WEIGHT_Q, rounding=ROUND_HALF_UP)


def volume(value: Decimal) -> Decimal:
    return Decimal(value).quantize(VOLUME_Q, rounding=ROUND_HALF_UP)


def packages_count(packed_qty: int, has_packing: bool, capacity: int) -> int:
    """Количество упаковок = ceil(packed_qty / capacity) (ТЗ §12, §18)."""
    if not has_packing or capacity <= 0 or packed_qty <= 0:
        return 0
    return math.ceil(packed_qty / capacity)


def unit_volume_m3(length_mm: int, width_mm: int, height_mm: int) -> Decimal:
    """Объём единицы в м³ = (Д×Ш×В) / 1e9 (ТЗ §20)."""
    mm3 = Decimal(length_mm) * Decimal(width_mm) * Decimal(height_mm)
    return mm3 / _MM3_IN_M3


@dataclass
class LineCalc:
    planned: int
    fact: int
    packed: int
    unpacked: int
    packages: int
    equipment_weight: Decimal
    packaging_weight: Decimal
    total_weight: Decimal
    equipment_volume: Decimal  # объём оборудования без упаковки
    package_volume: Decimal  # внешний объём кейсов/рэков
    total_volume: Decimal
    power_peak_w: int  # суммарное пиковое энергопотребление строки (факт × единица)
    power_nominal_w: int  # суммарное номинальное энергопотребление строки

    @property
    def missing(self) -> int:
        return max(0, self.planned - self.fact)

    @property
    def over(self) -> int:
        return max(0, self.fact - self.planned)


def compute_line(line: PackingLine) -> LineCalc:
    """Полный расчёт строки (ТЗ §18–§20).

    Вес = всё оборудование × вес единицы + упаковки × вес пустой упаковки.
    Объём = объём НЕупакованного оборудования + внешний объём упаковок;
    упакованное оборудование учитывается только через объём упаковки (ТЗ §20).
    """
    fact = line.fact_quantity
    packed = min(line.packed_quantity, fact)
    return compute_part(line, fact, packed)


def compute_part(line: PackingLine, fact: int, packed: int) -> LineCalc:
    """Расчёт по части строки (fact/packed единиц) — для распределения по машинам.

    Та же формула, что и в compute_line, но количества берутся не из самой
    строки, а переданы явно: одна физическая строка packing-листа может быть
    разделена между несколькими машинами (ТЗ: распределение по транспорту).
    """
    unpacked = fact - packed

    pkgs = packages_count(packed, line.has_packing, line.pack_capacity)

    equip_weight = Decimal(fact) * line.unit_weight_kg
    pack_weight = Decimal(pkgs) * line.pack_empty_weight_kg

    unit_vol = unit_volume_m3(line.length_mm, line.width_mm, line.height_mm)
    pack_unit_vol = unit_volume_m3(line.pack_length_mm, line.pack_width_mm, line.pack_height_mm)

    equip_volume = Decimal(unpacked) * unit_vol
    package_volume = Decimal(pkgs) * pack_unit_vol

    peak_w = fact * line.power_peak_w if line.has_power else 0
    nominal_w = fact * line.power_nominal_w if line.has_power else 0

    return LineCalc(
        planned=line.planned_quantity,
        fact=fact,
        packed=packed,
        unpacked=unpacked,
        packages=pkgs,
        equipment_weight=weight(equip_weight),
        packaging_weight=weight(pack_weight),
        total_weight=weight(equip_weight + pack_weight),
        equipment_volume=volume(equip_volume),
        package_volume=volume(package_volume),
        total_volume=volume(equip_volume + package_volume),
        power_peak_w=peak_w,
        power_nominal_w=nominal_w,
    )


@dataclass
class PackingTotals:
    total_weight: Decimal
    total_volume: Decimal
    total_power_peak_w: int
    total_power_nominal_w: int


def compute_totals(lines: list[PackingLine]) -> PackingTotals:
    """Итоговые вес, объём и энергопотребление проекта (ТЗ §19, §20)."""
    tw = Decimal("0")
    tv = Decimal("0")
    peak = 0
    nominal = 0
    for line in lines:
        c = compute_line(line)
        tw += c.total_weight
        tv += c.total_volume
        peak += c.power_peak_w
        nominal += c.power_nominal_w
    return PackingTotals(
        total_weight=weight(tw),
        total_volume=volume(tv),
        total_power_peak_w=peak,
        total_power_nominal_w=nominal,
    )


@dataclass
class CategoryTotal:
    """Итоги по одной категории packing-листа (разбивка по категориям)."""

    category_name: str
    total_weight: Decimal
    total_volume: Decimal
    power_peak_w: int
    power_nominal_w: int


def compute_category_breakdown(
    lines: list[PackingLine],
    *,
    custom_label: str = "Дополнительно",
    kit_label: str = "Комплекты",
    accessory_kit_label: str = "Комплекты аксессуаров",
    no_category_label: str = "Без категории",
) -> list[CategoryTotal]:
    """Разбивка веса, объёма и энергопотребления по категориям.

    Дополнительные позиции (без категории) собираются в группу ``custom_label``,
    строки-комплекты — в ``kit_label``, строки-комплекты аксессуаров — в
    ``accessory_kit_label``. Порядок групп — по первому появлению строки, как при
    выводе документа. Метки по умолчанию — русские (для веб-интерфейса
    packing-листа); PDF-рендер (документы на en/de) передаёт локализованные метки
    явно (ТЗ §26.1). Группа определяется по ``line.is_kit``/``is_accessory_kit``,
    а не по сохранённому тексту ``category_name`` — там снимок группового имени
    (см. packing.service), который сам по себе не переводится.
    """
    order: list[str] = []
    acc: dict[str, dict] = {}
    for line in lines:
        if line.is_custom:
            key = custom_label
        elif line.is_kit:
            key = kit_label
        elif line.is_accessory_kit:
            key = accessory_kit_label
        else:
            key = line.category_name or no_category_label
        if key not in acc:
            order.append(key)
            acc[key] = {
                "weight": Decimal("0"),
                "volume": Decimal("0"),
                "peak": 0,
                "nominal": 0,
            }
        c = compute_line(line)
        acc[key]["weight"] += c.total_weight
        acc[key]["volume"] += c.total_volume
        acc[key]["peak"] += c.power_peak_w
        acc[key]["nominal"] += c.power_nominal_w
    return [
        CategoryTotal(
            category_name=key,
            total_weight=weight(acc[key]["weight"]),
            total_volume=volume(acc[key]["volume"]),
            power_peak_w=acc[key]["peak"],
            power_nominal_w=acc[key]["nominal"],
        )
        for key in order
    ]
