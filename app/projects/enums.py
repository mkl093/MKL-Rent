"""Статусы проекта (ТЗ §13.4)."""

from __future__ import annotations

import enum


class ProjectStatus(enum.StrEnum):
    DRAFT = "draft"  # черновик — не резервирует
    BOOKED = "booked"  # забронирован — резервирует оборудование
    COMPLETED = "completed"  # завершён — освобождает бронь, в архиве
    CANCELLED = "cancelled"  # отменён — не резервирует, в архиве

    @property
    def label(self) -> str:
        return {
            "draft": "Черновик",
            "booked": "Забронирован",
            "completed": "Завершён",
            "cancelled": "Отменён",
        }[self.value]

    @property
    def badge(self) -> str:
        """CSS-класс Bootstrap для бейджа статуса."""
        return {
            "draft": "secondary",
            "booked": "success",
            "completed": "primary",
            "cancelled": "dark",
        }[self.value]

    @property
    def is_archived(self) -> bool:
        """Завершённые и отменённые скрываются в архиве (ТЗ §13.6)."""
        return self in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED)

    @property
    def reserves(self) -> bool:
        """Резервирует ли оборудование (ТЗ §13.4)."""
        return self == ProjectStatus.BOOKED
