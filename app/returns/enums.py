"""Статусы приёмки оборудования (ТЗ §56)."""

from __future__ import annotations

import enum


class ReturnStatus(enum.StrEnum):
    NOT_STARTED = "not_started"  # не начат
    RECEIVING = "receiving"  # принимается
    RECEIVED = "received"  # принято

    @property
    def label(self) -> str:
        return {
            "not_started": "Не начат",
            "receiving": "Принимается",
            "received": "Принято",
        }[self.value]

    @property
    def badge(self) -> str:
        return {"not_started": "secondary", "receiving": "warning", "received": "success"}[
            self.value
        ]


class ReturnCondition(enum.StrEnum):
    """Состояние единицы, отмеченное при приёмке (ТЗ §56.4).

    Отдельно от ItemStatus (§9) — здесь только исход осмотра при возврате;
    в ItemStatus отображается при завершении приёмки (apply_item_statuses).
    """

    OK = "ok"
    DEFECT = "defect"
    REPAIR = "repair"

    @property
    def label(self) -> str:
        return {"ok": "ОК", "defect": "Есть дефект", "repair": "В ремонт"}[self.value]

    @property
    def badge(self) -> str:
        return {"ok": "success", "defect": "danger", "repair": "warning"}[self.value]
