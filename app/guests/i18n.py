"""Двуязычный интерфейс гостевого портала (EN/RU, английский по умолчанию).

В проекте нет i18n-фреймворка (весь остальной интерфейс — только на русском),
а гостевых строк немного, поэтому — простой словарь без внешних зависимостей.
Выбор языка живёт в сессии (страница логина и внутри портала), не в БД.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

LANG_SESSION_KEY = "guest_lang"
DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")

STRINGS: dict[str, dict[str, str]] = {
    "login_title": {"en": "Sign in", "ru": "Вход"},
    "field_username": {"en": "Username", "ru": "Логин"},
    "field_password": {"en": "Password", "ru": "Пароль"},
    "sign_in": {"en": "Sign in", "ru": "Войти"},
    "login_foot_named": {
        "en": "Access provided by {company}. Contact your account manager for credentials.",
        "ru": "Доступ предоставляется компанией «{company}». "
        "За реквизитами обращайтесь к менеджеру.",
    },
    "login_foot_generic": {
        "en": "Access provided by staff. Contact your account manager for credentials.",
        "ru": "Доступ предоставляется персоналом. За реквизитами обращайтесь к менеджеру.",
    },
    "error_invalid_credentials": {
        "en": "Incorrect username or password.",
        "ru": "Неверный логин или пароль.",
    },
    "error_missing_credentials": {
        "en": "Please enter your username and password.",
        "ru": "Введите логин и пароль.",
    },
    "error_too_many_attempts": {
        "en": "Too many attempts. Please try again later.",
        "ru": "Слишком много попыток входа. Повторите позже.",
    },
    "error_account_disabled": {
        "en": "This account has been disabled.",
        "ru": "Учётная запись отключена.",
    },
    "catalog_title": {"en": "Equipment catalog", "ru": "Каталог оборудования"},
    "catalog_hint_view": {
        "en": "Stock overview — quantities reflect total units at the warehouse.",
        "ru": "Обзор склада — количество указано по всем единицам на складе.",
    },
    "catalog_hint_search": {
        "en": "Set dates to see the free quantity for that period.",
        "ru": "Укажите даты, чтобы увидеть свободный остаток на этот период.",
    },
    "search_placeholder": {
        "en": "Search by name or manufacturer…",
        "ru": "Поиск по названию или производителю…",
    },
    "date_from": {"en": "From", "ru": "С"},
    "date_to": {"en": "To", "ru": "По"},
    "check_availability": {"en": "Check availability", "ru": "Проверить наличие"},
    "search_button": {"en": "Search", "ru": "Найти"},
    "in_stock": {"en": "In stock", "ru": "На складе"},
    "no_results": {"en": "No equipment found.", "ru": "Оборудование не найдено."},
    "view_grid": {"en": "Grid", "ru": "Плитка"},
    "view_table": {"en": "Table", "ru": "Таблица"},
    "model_name": {"en": "Model", "ru": "Модель"},
    "back_to_catalog": {"en": "Back to catalog", "ru": "Назад в каталог"},
    "manufacturer": {"en": "Manufacturer", "ru": "Производитель"},
    "unit_weight": {"en": "Unit weight (case excl.)", "ru": "Вес единицы (без кейса)"},
    "power_nominal": {"en": "Power — nominal", "ru": "Потребление — номинал"},
    "power_peak": {"en": "Power — peak", "ru": "Потребление — пик"},
    "category": {"en": "Category", "ru": "Категория"},
    "total_at_warehouse": {"en": "Total at warehouse", "ru": "Всего на складе"},
    "available_for_period": {"en": "Available", "ru": "Свободно"},
    "log_out": {"en": "Log out", "ru": "Выйти"},
    "no_photo": {"en": "No photo", "ru": "Нет фото"},
}


def get_lang(request: Request) -> str:
    return request.session.get(LANG_SESSION_KEY, DEFAULT_LANG)


def set_lang(request: Request, lang: str) -> None:
    if lang in SUPPORTED_LANGS:
        request.session[LANG_SESSION_KEY] = lang


def date_input_locale(lang: str) -> str:
    """Локаль для атрибута lang нативного <input type="date">.

    Формат подсказки/календаря у type="date" браузер берёт из локали ОС, а не
    из языка страницы — без явного lang на самом инпуте он не следует выбору
    гостя (Chromium, впрочем, читает lang именно у элемента).
    """
    return "ru-RU" if lang == "ru" else "en-GB"


def translator(lang: str) -> Callable[..., str]:
    def t(key: str, **kwargs: str) -> str:
        entry = STRINGS.get(key)
        if entry is None:
            return key
        text = entry.get(lang, entry.get(DEFAULT_LANG, key))
        return text.format(**kwargs) if kwargs else text

    return t
