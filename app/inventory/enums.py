"""Перечисления складского домена (ТЗ §8, §9, §12).

StrEnum: значения совпадают со строками, хранимыми в БД (см. values_callable
в app/inventory/models.py), поэтому server_default миграции и ORM согласованы.
"""

from __future__ import annotations

import enum


class AccountingType(enum.StrEnum):
    """Тип учёта модели (неизменяем после создания — ТЗ §7.3, §40.1)."""

    QUANTITY = "quantity"  # количественный, без экземпляров и штрих-кодов
    SERIAL = "serial"  # посерийный, каждая единица — отдельный экземпляр

    @property
    def label(self) -> str:
        return {"quantity": "Количественный", "serial": "Посерийный"}[self.value]


class PackingType(enum.StrEnum):
    """Тип штатной упаковки (ТЗ §12)."""

    CASE = "case"  # кейс
    RACK = "rack"  # рэк

    @property
    def label(self) -> str:
        return {"case": "Кейс", "rack": "Рэк"}[self.value]

    @property
    def prefix(self) -> str:
        """Префикс автоназвания упаковки."""
        return {"case": "Кейс для", "rack": "Рэк для"}[self.value]


class ItemStatus(enum.StrEnum):
    """Статус физического экземпляра (ТЗ §9).

    «Забронировано» здесь не хранится — оно рассчитывается по проектам.
    """

    ACTIVE = "active"  # активно
    REPAIR = "repair"  # в ремонте
    RETIRED = "retired"  # списано

    @property
    def label(self) -> str:
        return {"active": "Активно", "repair": "В ремонте", "retired": "Списано"}[self.value]
