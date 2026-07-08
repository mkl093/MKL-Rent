"""Pydantic-схемы склада."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.inventory.enums import AccountingType, PackingType


class PackingRuleInput(BaseModel):
    packing_type: PackingType
    empty_weight_kg: Decimal = Field(default=Decimal("0"), ge=0)
    length_mm: int = Field(default=0, ge=0)
    width_mm: int = Field(default=0, ge=0)
    height_mm: int = Field(default=0, ge=0)
    capacity: int = Field(default=1, ge=1)


class EquipmentModelCreate(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=255)
    accounting_type: AccountingType
    weight_kg: Decimal = Field(default=Decimal("0"), ge=0)
    length_mm: int = Field(default=0, ge=0)
    width_mm: int = Field(default=0, ge=0)
    height_mm: int = Field(default=0, ge=0)
    base_price_eur: Decimal = Field(default=Decimal("0"), ge=0)
    total_quantity: int = Field(default=0, ge=0)

    subcategory_id: int | None = None
    manufacturer: str | None = Field(default=None, max_length=255)
    internal_sku: str | None = Field(default=None, max_length=100)
    description: str | None = None
    note: str | None = None

    packing: PackingRuleInput | None = None


class EquipmentModelUpdate(BaseModel):
    """Обновление модели. Тип учёта не входит — он неизменяем (ТЗ §7.3)."""

    category_id: int
    name: str = Field(min_length=1, max_length=255)
    weight_kg: Decimal = Field(default=Decimal("0"), ge=0)
    length_mm: int = Field(default=0, ge=0)
    width_mm: int = Field(default=0, ge=0)
    height_mm: int = Field(default=0, ge=0)
    base_price_eur: Decimal = Field(default=Decimal("0"), ge=0)
    total_quantity: int = Field(default=0, ge=0)

    subcategory_id: int | None = None
    manufacturer: str | None = Field(default=None, max_length=255)
    internal_sku: str | None = Field(default=None, max_length=100)
    description: str | None = None
    note: str | None = None

    packing: PackingRuleInput | None = None


class EquipmentItemInput(BaseModel):
    barcode: str | None = Field(default=None, max_length=128)
    serial_number: str | None = Field(default=None, max_length=255)
    inventory_number: str | None = Field(default=None, max_length=255)
    comment: str | None = None


class KitInput(BaseModel):
    """Данные комплекта (структура «Комплект»)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
