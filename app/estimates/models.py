"""Модели сметы (ТЗ §16).

В строках хранятся снимки данных модели на момент добавления (название, категория,
производитель, цена), чтобы правки модели не меняли уже созданные документы (ТЗ §7.3).
Деньги/коэффициенты — Numeric, без float (ТЗ §45.4).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Estimate(Base, TimestampMixin):
    """Смета проекта — одна на проект (ТЗ §16, §16.8: хранится только актуальная версия)."""

    __tablename__ = "estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    # Общая скидка в процентах (ТЗ §16.7). VAT берётся из проекта.
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), nullable=False
    )

    lines: Mapped[list[EstimateLine]] = relationship(
        back_populates="estimate",
        cascade="all, delete-orphan",
        order_by="EstimateLine.sort_order, EstimateLine.id",
    )


class EstimateLine(Base):
    """Строка сметы: складская позиция или произвольная строка (ТЗ §16.3, §16.5)."""

    __tablename__ = "estimate_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    estimate_id: Mapped[int] = mapped_column(
        ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Для произвольной строки model_id отсутствует, is_custom=True (ТЗ §16.5).
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Снимки на момент добавления (ТЗ §7.3).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    coefficient: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), default=Decimal("1"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    estimate: Mapped[Estimate] = relationship(back_populates="lines")

    @property
    def line_total(self) -> Decimal:
        """Итог строки = цена × количество × коэффициент (ТЗ §16.4)."""
        from app.estimates.totals import money

        return money(self.unit_price * self.quantity * self.coefficient)
