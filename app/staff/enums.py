"""Перечисления календаря занятости персонала (ТЗ §54)."""

from __future__ import annotations

import enum


class AssignmentType(enum.StrEnum):
    """Тип занятости сотрудника."""

    PROJECT = "project"  # работа на проекте
    BUSY = "busy"  # занят (без проекта)
    VACATION = "vacation"  # отпуск
    SICK = "sick"  # больничный
    UNAVAILABLE = "unavailable"  # недоступен
    OTHER = "other"  # другое

    @property
    def label(self) -> str:
        return {
            "project": "Проект",
            "busy": "Занят",
            "vacation": "Отпуск",
            "sick": "Больничный",
            "unavailable": "Недоступен",
            "other": "Другое",
        }[self.value]

    @property
    def css(self) -> str:
        """CSS-класс блока в таймлайне (см. app/static/css/app.css)."""
        return f"sc-type-{self.value}"

    @property
    def icon(self) -> str:
        """Символ-маркер типа — чтобы не полагаться только на цвет."""
        return {
            "project": "▶",
            "busy": "●",
            "vacation": "☀",
            "sick": "⚕",
            "unavailable": "✕",
            "other": "…",
        }[self.value]


class AssignmentStatus(enum.StrEnum):
    """Статус записи занятости."""

    PLANNED = "planned"  # запланировано
    CONFIRMED = "confirmed"  # подтверждено
    CANCELLED = "cancelled"  # отменено

    @property
    def label(self) -> str:
        return {
            "planned": "Запланировано",
            "confirmed": "Подтверждено",
            "cancelled": "Отменено",
        }[self.value]

    @property
    def badge(self) -> str:
        return {
            "planned": "secondary",
            "confirmed": "success",
            "cancelled": "dark",
        }[self.value]

    @property
    def blocks(self) -> bool:
        """Участвует ли в проверке пересечений (отменённые — нет)."""
        return self is not AssignmentStatus.CANCELLED
