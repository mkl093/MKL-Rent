"""Pydantic-схемы проектов."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    shipped_date: date | None = None
    returned_date: date | None = None
    rental_coefficient: Decimal = Field(default=Decimal("1"), ge=0)
    vat: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    customer: str | None = Field(default=None, max_length=255)
    address: str | None = None
    comment: str | None = None
    # Цветовая маркировка в календаре занятости (ТЗ §54.3).
    color: str | None = None
    calendar_bar: bool = False

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        # Тихо отбрасываем некорректный hex вместо ошибки: color всегда приходит
        # либо из <input type="color">, либо из пресетов — свободного ввода нет,
        # поэтому строгая валидация здесь не нужна (см. _str()/_date() в router.py,
        # которые по тому же принципу молча приводят к дефолту, а не падают).
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        return value if _HEX_COLOR.match(value) else None
