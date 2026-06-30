"""Pydantic-схемы packing-листа."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class CustomPackingLine(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    unit_weight_kg: Decimal = Field(default=Decimal("0"), ge=0)
    length_mm: int = Field(default=0, ge=0)
    width_mm: int = Field(default=0, ge=0)
    height_mm: int = Field(default=0, ge=0)
    comment: str | None = None
