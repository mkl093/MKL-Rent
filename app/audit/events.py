"""Типы событий журнала действий (ТЗ §29)."""

from __future__ import annotations

import enum


class EventType(enum.StrEnum):
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_STATUS = "project_status"
    PROJECT_DATES = "project_dates"
    PROJECT_BOOK = "project_book"
    PROJECT_COPY = "project_copy"
    PROJECT_DELETE = "project_delete"
    ESTIMATE_CHANGE = "estimate_change"
    PACKING_CREATE = "packing_create"
    PACKING_ADD = "packing_add"
    PACKING_SYNC = "packing_sync"
    PACKING_STATUS = "packing_status"
    PACKING_SCAN = "packing_scan"
    PACKING_SCAN_UNDO = "packing_scan_undo"
    RETURN_CREATE = "return_create"
    RETURN_SCAN = "return_scan"
    RETURN_SCAN_UNDO = "return_scan_undo"
    RETURN_CONDITION = "return_condition"
    RETURN_SUBSTITUTE = "return_substitute"
    RETURN_ACCEPT_ALL = "return_accept_all"
    RETURN_STATUS = "return_status"
    INVENTORY_MODEL = "inventory_model"
    INVENTORY_QTY = "inventory_qty"
    INVENTORY_ITEM_STATUS = "inventory_item_status"
    INVENTORY_ITEM_DELETE = "inventory_item_delete"
    KIT_MANAGE = "kit_manage"
    DOCUMENT_GENERATE = "document_generate"
    USER_MANAGE = "user_manage"
    AUTH_LOGIN_BLOCKED = "auth_login_blocked"
    STAFF_MANAGE = "staff_manage"
    ASSIGNMENT_MANAGE = "assignment_manage"
    EQUIPMENT_MANUAL = "equipment_manual"
    EQUIPMENT_CERTIFICATE = "equipment_certificate"
    INVENTORY_IMPORT = "inventory_import"
    VEHICLE_MANAGE = "vehicle_manage"
    TRANSPORT_ASSIGN = "transport_assign"
    GUEST_MANAGE = "guest_manage"
    ACCESSORY_KIT_MANAGE = "accessory_kit_manage"

    @property
    def label(self) -> str:
        return {
            "project_create": "Создан проект",
            "project_update": "Изменён проект",
            "project_status": "Смена статуса проекта",
            "project_dates": "Даты отгрузки/возврата",
            "project_book": "Бронирование",
            "project_copy": "Копирование проекта",
            "project_delete": "Удалён проект",
            "estimate_change": "Изменение сметы",
            "packing_create": "Создан packing-лист",
            "packing_add": "Добавление оборудования в packing-лист",
            "packing_sync": "Синхронизация packing-листа",
            "packing_status": "Статус packing-листа",
            "packing_scan": "Комплектация (сканирование/добавление)",
            "packing_scan_undo": "Отмена сканирования",
            "return_create": "Оформлен возврат",
            "return_scan": "Приёмка (сканирование)",
            "return_scan_undo": "Отмена сканирования приёмки",
            "return_condition": "Состояние принятого экземпляра",
            "return_substitute": "Замена единицы при приёмке",
            "return_accept_all": "Приёмка пачкой",
            "return_status": "Статус приёмки",
            "inventory_model": "Модель оборудования",
            "inventory_qty": "Изменение остатка",
            "inventory_item_status": "Статус экземпляра",
            "inventory_item_delete": "Удаление экземпляров",
            "kit_manage": "Комплект",
            "document_generate": "Генерация PDF",
            "user_manage": "Управление пользователями",
            "auth_login_blocked": "Блокировка входа по IP",
            "staff_manage": "Управление персоналом",
            "assignment_manage": "Занятость сотрудника",
            "equipment_manual": "Мануал модели",
            "equipment_certificate": "Сертификат испытаний",
            "inventory_import": "Импорт из Excel",
            "vehicle_manage": "Справочник машин",
            "transport_assign": "Распределение по транспорту",
            "guest_manage": "Управление гостевым доступом",
            "accessory_kit_manage": "Комплект аксессуаров",
        }[self.value]
