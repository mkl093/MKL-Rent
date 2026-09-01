"""Перечисления складского домена (ТЗ §8, §9, §12).

StrEnum: значения совпадают со строками, хранимыми в БД (см. values_callable
в app/inventory/models.py), поэтому server_default миграции и ORM согласованы.
"""

from __future__ import annotations

import enum


class AccountingType(enum.StrEnum):
    """Тип учёта модели (неизменяем после создания — ТЗ §7.3, §40.1)."""

    QUANTITY = "quantity"  # количественный: экземпляры и штрих-коды есть, но серийный номер не обязателен
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

    ACTIVE = "active"  # активно — доступно к аренде
    REPAIR = "repair"  # в ремонте
    DEFECT = "defect"  # есть дефект (в комментарии — какой)
    RETIRED = "retired"  # списано

    @property
    def label(self) -> str:
        return {
            "active": "Активно",
            "repair": "В ремонте",
            "defect": "Есть дефект",
            "retired": "Списано",
        }[self.value]

    @property
    def badge(self) -> str:
        return {"active": "success", "repair": "warning", "defect": "danger", "retired": "dark"}[
            self.value
        ]

    @property
    def is_available(self) -> bool:
        """Доступна ли единица к аренде по одному статусу (без учёта исключений).

        Для DEFECT единица может быть отмечена доступной несмотря на дефект —
        см. EquipmentItem.is_usable, который учитывает это исключение.
        """
        return self == ItemStatus.ACTIVE


class KitWeightMode(enum.StrEnum):
    """Как считается вес комплекта для packing (структура «Комплект»)."""

    CONTENT = "content"  # только сумма веса содержимого (значение веса не задаётся)
    PACKAGING = "packaging"  # содержимое + вес упаковки/кейса
    TOTAL = "total"  # фиксированный общий вес (содержимое не учитывается)

    @property
    def label(self) -> str:
        return {
            "content": "По содержимому",
            "packaging": "Содержимое + вес упаковки",
            "total": "Фиксированный общий вес",
        }[self.value]

    @property
    def needs_value(self) -> bool:
        """Требуется ли числовое значение веса для режима."""
        return self != KitWeightMode.CONTENT
