"""Модели приёмки оборудования (ТЗ §56).

Строки хранят снимок соответствующей строки packing-листа на момент оформления
возврата (тот же приём, что и у packing относительно сметы, ТЗ §17.2) — правки
packing-листа после оформления возврата лист приёмки не меняют.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.models import _enum_column
from app.returns.enums import ReturnCondition, ReturnStatus


class ReturnList(Base, TimestampMixin):
    """Лист приёмки — один на проект (ТЗ §56.1)."""

    __tablename__ = "return_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    status: Mapped[ReturnStatus] = mapped_column(
        _enum_column(ReturnStatus, 16), default=ReturnStatus.NOT_STARTED, nullable=False
    )
    # Причина недостачи при переводе в «Принято» (ТЗ §56.5).
    shortage_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[ReturnLine]] = relationship(
        back_populates="return_list",
        cascade="all, delete-orphan",
        order_by="ReturnLine.sort_order, ReturnLine.id",
    )


class ReturnLine(Base):
    """Строка листа приёмки — снимок строки packing-листа (ТЗ §56.1)."""

    __tablename__ = "return_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_list_id: Mapped[int] = mapped_column(
        ForeignKey("return_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kit_id: Mapped[int | None] = mapped_column(
        ForeignKey("kits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Строка-комплект аксессуаров: аналогично kit_id, но комплект проектный
    # (см. app.accessory_kits) — содержимое сверяется по позициям (accessory_kit_lines).
    accessory_kit_id: Mapped[int | None] = mapped_column(
        ForeignKey("accessory_kits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_serial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Снимки для группировки и отображения (ТЗ §17.3, переиспользуется приёмкой).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Ожидается к возврату = фактически выданное количество по packing-листу (ТЗ §56.1).
    expected_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Факт для количественных строк; для серийных факт = число принятых экземпляров.
    returned_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    return_list: Mapped[ReturnList] = relationship(back_populates="lines")
    serial_items: Mapped[list[ReturnSerialItem]] = relationship(
        back_populates="line", cascade="all, delete-orphan", order_by="ReturnSerialItem.barcode"
    )
    accessory_kit_lines: Mapped[list[ReturnAccessoryKitLine]] = relationship(
        back_populates="return_line", cascade="all, delete-orphan", order_by="ReturnAccessoryKitLine.id"
    )

    @property
    def is_kit(self) -> bool:
        return self.kit_id is not None

    @property
    def is_accessory_kit(self) -> bool:
        return self.accessory_kit_id is not None

    @property
    def fact_quantity(self) -> int:
        """Фактически принято: для серийных — число отмеченных «возвращено» (ТЗ §56.3)."""
        if self.is_serial:
            return sum(1 for si in self.serial_items if si.is_returned)
        return self.returned_quantity

    @property
    def missing_quantity(self) -> int:
        return max(0, self.expected_quantity - self.fact_quantity)


class ReturnSerialItem(Base):
    """Ожидаемый (и, при возврате, принятый) экземпляр серийной строки (ТЗ §56.3, §56.4).

    Строка создаётся сразу при оформлении возврата — по одной на каждый экземпляр,
    выданный по packing-листу. is_returned=False до сканирования — это и есть
    «ожидается»/«не возвращено» без отдельной таблицы недостачи.
    """

    __tablename__ = "return_serial_items"
    __table_args__ = (UniqueConstraint("return_line_id", "item_id", name="uq_return_line_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_line_id: Mapped[int] = mapped_column(
        ForeignKey("return_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    barcode: Mapped[str] = mapped_column(String(128), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_returned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    condition: Mapped[ReturnCondition] = mapped_column(
        _enum_column(ReturnCondition, 8), default=ReturnCondition.OK, nullable=False
    )
    condition_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    line: Mapped[ReturnLine] = relationship(back_populates="serial_items")


class ReturnAccessoryKitLine(Base):
    """Снимок позиции содержимого комплекта аксессуаров на момент оформления возврата.

    Содержимое кабелярки количественное, а не поштучно отслеживаемое (в отличие
    от ReturnSerialItem) — поэтому сверяется как «ожидается/принято» по каждой
    позиции состава отдельно, чтобы недостача мелких кабелей/коннекторов не
    терялась за одной общей галочкой «кейс вернулся» (ТЗ §56.1, §56.3).
    """

    __tablename__ = "return_accessory_kit_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_line_id: Mapped[int] = mapped_column(
        ForeignKey("return_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    returned_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    return_line: Mapped[ReturnLine] = relationship(back_populates="accessory_kit_lines")

    @property
    def missing_quantity(self) -> int:
        return max(0, self.expected_quantity - self.returned_quantity)
