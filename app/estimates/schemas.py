"""Pydantic-схемы сметы."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class AddModelItem(BaseModel):
    model_id: int
    quantity: int = Field(ge=1)


class CustomLineInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    coefficient: Decimal = Field(default=Decimal("1"), ge=0)
    comment: str | None = None


class LineUpdate(BaseModel):
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)
    coefficient: Decimal = Field(ge=0)
    comment: str | None = None
