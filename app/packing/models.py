"""Модели packing-листа (ТЗ §17–§20).

Строки хранят снимки модели (вес, габариты, правило упаковки) на момент создания,
чтобы правки модели и сметы не меняли готовый документ (ТЗ §7.3, §17.2).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.models import _enum_column
from app.packing.enums import PackingStatus


class PackingList(Base, TimestampMixin):
    """Packing-лист проекта — один на проект (ТЗ §17)."""

    __tablename__ = "packing_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    status: Mapped[PackingStatus] = mapped_column(
        _enum_column(PackingStatus, 16), default=PackingStatus.NOT_STARTED, nullable=False
    )
    # Причина недокомплекта при переводе в «Скомплектован» (ТЗ §17.4).
    shortage_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[PackingLine]] = relationship(
        back_populates="packing_list",
        cascade="all, delete-orphan",
        order_by="PackingLine.sort_order, PackingLine.id",
    )


class PackingLine(Base):
    """Строка packing-листа: складская позиция или дополнительная (ТЗ §17.5, §17.9)."""

    __tablename__ = "packing_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_serial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Снимки для группировки (ТЗ §17.3) и отображения.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # План/факт (ТЗ §17.5). Для серийных факт = число назначенных экземпляров.
    planned_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )  # факт для количественных
    packed_quantity: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )  # в упаковке (ТЗ §18)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Снимки веса/габаритов единицы (ТЗ §19, §20).
    unit_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    length_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Снимки правила упаковки (ТЗ §12, §18).
    has_packing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pack_capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    pack_empty_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    pack_length_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pack_width_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pack_height_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    packing_list: Mapped[PackingList] = relationship(back_populates="lines")
    serial_items: Mapped[list[PackingSerialItem]] = relationship(
        back_populates="line", cascade="all, delete-orphan", order_by="PackingSerialItem.barcode"
    )

    @property
    def fact_quantity(self) -> int:
        """Фактическое количество: для серийных — число экземпляров (ТЗ §17.5)."""
        if self.is_serial:
            return len(self.serial_items)
        return self.quantity

    @property
    def unpacked_quantity(self) -> int:
        return max(0, self.fact_quantity - self.packed_quantity)


class PackingSerialItem(Base):
    """Назначенный в packing-лист экземпляр посерийной модели (ТЗ §17.7)."""

    __tablename__ = "packing_serial_items"
    __table_args__ = (UniqueConstraint("packing_line_id", "item_id", name="uq_packing_line_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_line_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    barcode: Mapped[str] = mapped_column(String(128), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)

    line: Mapped[PackingLine] = relationship(back_populates="serial_items")
