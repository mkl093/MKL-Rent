"""Расчёты распределения packing-листа по машинам (ТЗ: транспорт).

Разделение упакованной позиции между машинами физически разбивает кейс: число
упаковок на каждую машину считается независимо (ceil), поэтому суммарный вес
по машинам может немного превышать вес того же оборудования в общем
packing-листе — это ожидаемое поведение, а не ошибка округления.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.packing.calc import LineCalc, compute_part, volume, weight
from app.packing.models import PackingLine
from app.transport.models import ProjectVehicle, TransportAssignment


def allocate(
    line: PackingLine, ordered_assignments: list[TransportAssignment]
) -> dict[int, LineCalc]:
    """Разложить строку по назначениям в заданном порядке — сначала упакованные единицы.

    ``ordered_assignments`` должны быть отсортированы по порядку, в котором
    машины добавлены в проект (см. transport.service.board). Возвращает расчёт
    на каждое назначение (ключ — id назначения).
    """
    packed_pool = min(line.packed_quantity, line.fact_quantity)
    result: dict[int, LineCalc] = {}
    for a in ordered_assignments:
        packed_here = min(a.quantity, packed_pool)
        packed_pool -= packed_here
        result[a.id] = compute_part(line, a.quantity, packed_here)
    return result


@dataclass
class VehicleTotals:
    weight: Decimal
    volume: Decimal
    positions: int
    max_weight_kg: Decimal
    overload: bool
    overload_kg: Decimal


def vehicle_totals(project_vehicle: ProjectVehicle, calcs: list[LineCalc]) -> VehicleTotals:
    total_weight = weight(sum((c.total_weight for c in calcs), Decimal("0")))
    total_volume = volume(sum((c.total_volume for c in calcs), Decimal("0")))
    overload_kg = max(Decimal("0"), total_weight - project_vehicle.max_weight_kg)
    return VehicleTotals(
        weight=total_weight,
        volume=total_volume,
        positions=len(calcs),
        max_weight_kg=project_vehicle.max_weight_kg,
        overload=overload_kg > 0,
        overload_kg=overload_kg,
    )
