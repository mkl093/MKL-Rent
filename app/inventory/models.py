"""ORM-модели склада (ТЗ §6–§12).

Деньги/вес/габариты — Numeric/Integer, без float (ТЗ §45.4).
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.enums import AccountingType, ItemStatus, KitWeightMode, PackingType


def _enum_column(enum_cls: type, length: int) -> Enum:
    """Колонка-перечисление, хранящая .value (а не имя) для согласия с миграциями."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [member.value for member in e],
    )


class Category(Base, TimestampMixin):
    """Категория верхнего уровня (ТЗ §6.1)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    subcategories: Mapped[list[Subcategory]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="Subcategory.sort_order, Subcategory.name",
    )


class Subcategory(Base, TimestampMixin):
    """Подкатегория (второй и последний уровень — ТЗ §6.1)."""

    __tablename__ = "subcategories"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_subcategory_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    category: Mapped[Category] = relationship(back_populates="subcategories")


class EquipmentModel(Base, TimestampMixin):
    """Модель оборудования (ТЗ §7)."""

    __tablename__ = "equipment_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Обязательные поля (ТЗ §7.1).
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    accounting_type: Mapped[AccountingType] = mapped_column(
        _enum_column(AccountingType, 20), nullable=False
    )
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=Decimal("0"), nullable=False)
    length_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    base_price_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    # Для количественной модели — общий остаток на складе (ТЗ §7.1, §10).
    total_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Необязательные поля (ТЗ §7.2).
    subcategory_id: Mapped[int | None] = mapped_column(
        ForeignKey("subcategories.id"), nullable=True, index=True
    )
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    country_of_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    internal_sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Расположение на складе — свободный текст, задаётся вручную. В packing-лист
    # и смету не выводится (справочная информация только для склада).
    storage_location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Энергопотребление (опционально). При has_power оба значения обязательны.
    has_power: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    power_peak_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power_nominal_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Архив (ТЗ §7.4).
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    category: Mapped[Category] = relationship()
    subcategory: Mapped[Subcategory | None] = relationship()
    packing: Mapped[PackingRule | None] = relationship(
        back_populates="model", cascade="all, delete-orphan", uselist=False
    )
    items: Mapped[list[EquipmentItem]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )
    # Комплектация модели — набор аксессуаров с количеством (справочник аксессуаров).
    accessories: Mapped[list[EquipmentModelAccessory]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="EquipmentModelAccessory.id",
    )

    @property
    def is_serial(self) -> bool:
        return self.accounting_type == AccountingType.SERIAL

    @property
    def has_packing(self) -> bool:
        return self.packing is not None


class PackingRule(Base, TimestampMixin):
    """Штатный кейс/рэк модели — одно правило на модель (ТЗ §12)."""

    __tablename__ = "packing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    packing_type: Mapped[PackingType] = mapped_column(_enum_column(PackingType, 10), nullable=False)
    empty_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    length_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    model: Mapped[EquipmentModel] = relationship(back_populates="packing")

    def name_for(self, model_name: str) -> str:
        """Автоназвание упаковки: «Кейс для X» / «Рэк для X» (ТЗ §12)."""
        return f"{self.packing_type.prefix} {model_name}"

    def packages_for(self, quantity: int) -> int:
        """Количество упаковок: ceil(quantity / capacity) (ТЗ §12)."""
        if self.capacity <= 0 or quantity <= 0:
            return 0
        return math.ceil(quantity / self.capacity)


class Kit(Base, TimestampMixin):
    """Комплект — кейс с фиксированной комплектацией (структура «Комплект»).

    В комплект помещаются конкретные единицы (EquipmentItem). Помещённая единица
    занята комплектом и НЕ входит в свободный сток своей модели.
    Комплект — бронируемая позиция: его можно добавлять в смету и packing-лист.
    """

    __tablename__ = "kits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Расположение на складе — свободный текст, задаётся вручную. В packing-лист
    # и смету не выводится (справочная информация только для склада).
    storage_location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Настройка веса для packing (необязательная). weight_value интерпретируется
    # по weight_mode: содержимое / содержимое+упаковка / фиксированный общий вес.
    weight_mode: Mapped[KitWeightMode] = mapped_column(
        _enum_column(KitWeightMode, 12), default=KitWeightMode.CONTENT, nullable=False
    )
    weight_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    items: Mapped[list[EquipmentItem]] = relationship(
        back_populates="kit",
        order_by="EquipmentItem.model_id, EquipmentItem.barcode",
    )


class EquipmentItem(Base, TimestampMixin):
    """Физический экземпляр посерийной модели (ТЗ §8.2)."""

    __tablename__ = "equipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Штрих-код опционален; при наличии — уникален во всей системе (ТЗ §21.1, §40.2).
    barcode: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    status: Mapped[ItemStatus] = mapped_column(
        _enum_column(ItemStatus, 10),
        default=ItemStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inventory_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Разрешение использовать экземпляр со статусом «Есть дефект» несмотря на
    # дефект (напр. косметический, не влияющий на работу). Значимо только при
    # status == DEFECT; при уходе со статуса сбрасывается (см. change_status).
    usable_despite_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Комплект, в который помещена единица (структура «Комплект»). Пока задан —
    # единица исключена из свободного стока модели (см. availability/stock).
    kit_id: Mapped[int | None] = mapped_column(
        ForeignKey("kits.id", ondelete="SET NULL"), nullable=True, index=True
    )

    model: Mapped[EquipmentModel] = relationship(back_populates="items")
    kit: Mapped[Kit | None] = relationship(back_populates="items")
    status_history: Mapped[list[EquipmentStatusHistory]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="EquipmentStatusHistory.changed_at.desc()",
    )

    @property
    def is_usable(self) -> bool:
        """Можно ли использовать единицу (аренда/паковка): активна, либо дефект
        отмечен как допустимый к использованию (usable_despite_defect)."""
        if self.status == ItemStatus.ACTIVE:
            return True
        return self.status == ItemStatus.DEFECT and self.usable_despite_defect


class EquipmentStatusHistory(Base):
    """История смены статуса экземпляра (ТЗ §9)."""

    __tablename__ = "equipment_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    old_status: Mapped[ItemStatus | None] = mapped_column(
        _enum_column(ItemStatus, 10), nullable=True
    )
    new_status: Mapped[ItemStatus] = mapped_column(_enum_column(ItemStatus, 10), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    item: Mapped[EquipmentItem] = relationship(back_populates="status_history")


class AccessoryCategory(Base, TimestampMixin):
    """Категория справочника аксессуаров (напр. «Питание», «Управление», «Такелаж»)."""

    __tablename__ = "accessory_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    accessories: Mapped[list[Accessory]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="Accessory.sort_order, Accessory.name",
    )


class Accessory(Base, TimestampMixin):
    """Аксессуар справочника — позиция комплектации (шнур, клемп, трос и т.п.)."""

    __tablename__ = "accessories"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_accessory_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("accessory_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    category: Mapped[AccessoryCategory] = relationship(back_populates="accessories")


class EquipmentModelAccessory(Base):
    """Связь модель↔аксессуар с количеством в комплектации (одна на пару)."""

    __tablename__ = "equipment_model_accessories"
    __table_args__ = (UniqueConstraint("model_id", "accessory_id", name="uq_model_accessory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    accessory_id: Mapped[int] = mapped_column(
        ForeignKey("accessories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    model: Mapped[EquipmentModel] = relationship(back_populates="accessories")
    accessory: Mapped[Accessory] = relationship()


class QuantityAdjustment(Base):
    """История изменения количественного остатка модели (ТЗ §10)."""

    __tablename__ = "quantity_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    old_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    new_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[EquipmentModel] = relationship()
