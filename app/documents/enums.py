"""Перечисления документов (ТЗ §26)."""

from __future__ import annotations

import enum


class DocumentType(enum.StrEnum):
    ESTIMATE = "estimate"  # PDF-смета (A4 книжная)
    PACKING = "packing"  # PDF packing-листа (A4 альбомная)

    @property
    def label(self) -> str:
        return {"estimate": "Смета", "packing": "Packing-лист"}[self.value]


class Language(enum.StrEnum):
    RU = "ru"
    EN = "en"

    @property
    def label(self) -> str:
        return {"ru": "Русский", "en": "English"}[self.value]
