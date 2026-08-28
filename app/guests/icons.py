"""Минималистичные генерируемые значки-заглушки для списков гостевого портала.

Реальное фото модели грузится только на карточке модели (см. model_photo в
app/guests/router.py) — на списках (плитка категорий, таблица) оно вообще не
запрашивается: у большого склада это были сотни запросов файлов на одну
страницу и заметное торможение загрузки. Вместо фото — цветной монограммный
значок (не эмодзи), детерминированный по названию: одно и то же название
всегда даёт один и тот же значок.
"""

from __future__ import annotations

import hashlib

from app.templating import templates

_PALETTE = (
    "#1f6f78",  # teal — акцент портала
    "#3f6fb9",
    "#3f8f5f",
    "#b9862c",
    "#7c5cb0",
    "#b2543f",
)


def monogram_color(text: str) -> str:
    """Детерминированный цвет значка по тексту (стабилен между перезапусками сервера)."""
    digest = hashlib.md5(text.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def monogram_letters(text: str) -> str:
    """1–2 буквы для значка по первым буквам слов названия."""
    words = [w for w in text.strip().split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


templates.env.globals["guest_monogram_color"] = monogram_color
templates.env.globals["guest_monogram_letters"] = monogram_letters
