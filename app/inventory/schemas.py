"""Pydantic-схемы склада."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.inventory.enums import AccountingType, KitWeightMode, PackingType


class PowerMixin(BaseModel):
    """Общая логика энергопотребления модели.

    При has_power оба значения (пиковое и номинальное) обязательны; иначе — обнуляются.
    """

    has_power: bool = False
    power_peak_w: int | None = Field(default=None, ge=0)
    power_nominal_w: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_power(self):
        if self.has_power:
            if self.power_peak_w is None or self.power_nominal_w is None:
                raise ValueError("Укажите пиковое и номинальное энергопотребление")
        else:
            self.power_peak_w = None
            self.power_nominal_w = None
        return self


class AccessoryQty(BaseModel):
    """Позиция комплектации модели: аксессуар и его количество."""

    accessory_id: int
    quantity: int = Field(default=1, ge=1)


class PackingRuleInput(BaseModel):
    packing_type: PackingType
    empty_weight_kg: Decimal = Field(default=Decimal("0"), ge=0)
    length_mm: int = Field(default=0, ge=0)
    width_mm: int = Field(default=0, ge=0)
    height_mm: int = Field(default=0, ge=0)
    capacity: int = Field(default=1, ge=1)


class EquipmentModelCreate(PowerMixin):
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
    country_of_origin: str | None = Field(default=None, max_length=255)
    internal_sku: str | None = Field(default=None, max_length=100)
    description: str | None = None
    note: str | None = None

    packing: PackingRuleInput | None = None
    accessories: list[AccessoryQty] = Field(default_factory=list)


class EquipmentModelUpdate(PowerMixin):
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
    country_of_origin: str | None = Field(default=None, max_length=255)
    internal_sku: str | None = Field(default=None, max_length=100)
    description: str | None = None
    note: str | None = None

    packing: PackingRuleInput | None = None
    accessories: list[AccessoryQty] = Field(default_factory=list)


class EquipmentItemInput(BaseModel):
    barcode: str | None = Field(default=None, max_length=128)
    serial_number: str | None = Field(default=None, max_length=255)
    inventory_number: str | None = Field(default=None, max_length=255)
    comment: str | None = None


class KitInput(BaseModel):
    """Данные комплекта (структура «Комплект»)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    weight_mode: KitWeightMode = KitWeightMode.CONTENT
    weight_value: Decimal | None = Field(default=None, ge=0)
