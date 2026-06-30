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
    unpacked = fact - packed

    pkgs = packages_count(packed, line.has_packing, line.pack_capacity)

    equip_weight = Decimal(fact) * line.unit_weight_kg
    pack_weight = Decimal(pkgs) * line.pack_empty_weight_kg

    unit_vol = unit_volume_m3(line.length_mm, line.width_mm, line.height_mm)
    pack_unit_vol = unit_volume_m3(line.pack_length_mm, line.pack_width_mm, line.pack_height_mm)

    equip_volume = Decimal(unpacked) * unit_vol
    package_volume = Decimal(pkgs) * pack_unit_vol

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
    )


@dataclass
class PackingTotals:
    total_weight: Decimal
    total_volume: Decimal


def compute_totals(lines: list[PackingLine]) -> PackingTotals:
    """Итоговые вес и объём проекта (ТЗ §19, §20)."""
    tw = Decimal("0")
    tv = Decimal("0")
    for line in lines:
        c = compute_line(line)
        tw += c.total_weight
        tv += c.total_volume
    return PackingTotals(total_weight=weight(tw), total_volume=volume(tv))
