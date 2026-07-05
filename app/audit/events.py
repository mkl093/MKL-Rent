"""Типы событий журнала действий (ТЗ §29)."""

from __future__ import annotations

import enum


class EventType(enum.StrEnum):
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_STATUS = "project_status"
    PROJECT_BOOK = "project_book"
    PROJECT_COPY = "project_copy"
    PROJECT_DELETE = "project_delete"
    ESTIMATE_CHANGE = "estimate_change"
    PACKING_CREATE = "packing_create"
    PACKING_SYNC = "packing_sync"
    PACKING_STATUS = "packing_status"
    PACKING_SCAN = "packing_scan"
    PACKING_SCAN_UNDO = "packing_scan_undo"
    INVENTORY_MODEL = "inventory_model"
    INVENTORY_QTY = "inventory_qty"
    INVENTORY_ITEM_STATUS = "inventory_item_status"
    DOCUMENT_GENERATE = "document_generate"
    USER_MANAGE = "user_manage"

    @property
    def label(self) -> str:
        return {
            "project_create": "Создан проект",
            "project_update": "Изменён проект",
            "project_status": "Смена статуса проекта",
            "project_book": "Бронирование",
            "project_copy": "Копирование проекта",
            "project_delete": "Удалён проект",
            "estimate_change": "Изменение сметы",
            "packing_create": "Создан packing-лист",
            "packing_sync": "Синхронизация packing-листа",
            "packing_status": "Статус packing-листа",
            "packing_scan": "Комплектация (сканирование/добавление)",
            "packing_scan_undo": "Отмена сканирования",
            "inventory_model": "Модель оборудования",
            "inventory_qty": "Изменение остатка",
            "inventory_item_status": "Статус экземпляра",
            "document_generate": "Генерация PDF",
            "user_manage": "Управление пользователями",
        }[self.value]
