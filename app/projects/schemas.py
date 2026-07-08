"""Pydantic-схемы проектов."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


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
