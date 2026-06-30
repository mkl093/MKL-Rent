"""Статусы packing-листа (ТЗ §17.4)."""

from __future__ import annotations

import enum


class PackingStatus(enum.StrEnum):
    NOT_STARTED = "not_started"  # не начат
    PICKING = "picking"  # комплектуется
    PICKED = "picked"  # скомплектован

    @property
    def label(self) -> str:
        return {
            "not_started": "Не начат",
            "picking": "Комплектуется",
            "picked": "Скомплектован",
        }[self.value]

    @property
    def badge(self) -> str:
        return {"not_started": "secondary", "picking": "warning", "picked": "success"}[self.value]
