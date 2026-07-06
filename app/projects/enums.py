"""Статусы проекта (ТЗ §13.4)."""

from __future__ import annotations

import enum


class ProjectStatus(enum.StrEnum):
    DRAFT = "draft"  # черновик — не резервирует
    BOOKED = "booked"  # забронирован — резервирует оборудование
    SHIPPED = "shipped"  # отгружено — оборудование выдано, продолжает резервировать
    COMPLETED = "completed"  # завершён — освобождает бронь, в архиве
    CANCELLED = "cancelled"  # отменён — не резервирует, в архиве

    @property
    def label(self) -> str:
        return {
            "draft": "Черновик",
            "booked": "Забронирован",
            "shipped": "Отгружено",
            "completed": "Завершён",
            "cancelled": "Отменён",
        }[self.value]

    @property
    def badge(self) -> str:
        """CSS-класс Bootstrap для бейджа статуса."""
        return {
            "draft": "secondary",
            "booked": "success",
            "shipped": "info",
            "completed": "primary",
            "cancelled": "dark",
        }[self.value]

    @property
    def is_archived(self) -> bool:
        """Завершённые и отменённые скрываются в архиве (ТЗ §13.6)."""
        return self in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED)

    @property
    def reserves(self) -> bool:
        """Резервирует ли оборудование: забронировано и отгружено (ТЗ §13.4)."""
        return self in (ProjectStatus.BOOKED, ProjectStatus.SHIPPED)


# Статусы, резервирующие оборудование (влияют на доступность).
RESERVING_STATUSES = [ProjectStatus.BOOKED, ProjectStatus.SHIPPED]
