"""Модели сметы (ТЗ §16): стенды проекта и версии смет.

Проект делится на стенды/площадки (ProjectSection). У стенда — номер сметы
EST-YYYY-NNN и одна или несколько версий сметы (Estimate), ровно одна из них
активна: только активная версия влияет на бронь, packing-лист и документы стенда
по умолчанию. Версии не удаляются; версию можно заморозить (только просмотр).

В строках хранятся снимки данных модели на момент добавления (название, категория,
производитель, цена), чтобы правки модели не меняли уже созданные документы (ТЗ §7.3).
Деньги/коэффициенты — Numeric, без float (ТЗ §45.4).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class ProjectSection(Base, TimestampMixin):
    """Стенд/площадка проекта: владеет номером сметы и её версиями."""

    __tablename__ = "project_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Номер сметы стенда EST-YYYY-NNN (ТЗ §14); у версий — «{number}-vN».
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    versions: Mapped[list[Estimate]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="Estimate.version_no",
    )

    @property
    def active_version(self) -> Estimate | None:
        return next((v for v in self.versions if v.is_active), None)


class Estimate(Base, TimestampMixin):
    """Версия сметы стенда (ТЗ §16). Активна ровно одна версия на стенд."""

    __tablename__ = "estimates"
    __table_args__ = (
        UniqueConstraint("section_id", "version_no", name="uq_estimate_section_version"),
        # Одна активная версия на стенд — частичный уникальный индекс (PG и SQLite).
        Index(
            "uq_estimates_active_per_section",
            "section_id",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Денормализовано для выборок «все сметы проекта» (стенд тоже ссылается на проект).
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("project_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Комментарий версии («после созвона 12.11»).
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Заморозка: версия только для просмотра (включается/снимается отдельно).
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    # Общая скидка в процентах (ТЗ §16.7). VAT берётся из проекта.
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), nullable=False
    )
    # Коэффициент аренды сметы целиком. По умолчанию — из проекта на момент
    # создания сметы; подставляется в новые строки, а при изменении переписывает
    # коэффициент всех строк, кроме скорректированных вручную (coefficient_custom).
    rental_coefficient: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), default=Decimal("1"), nullable=False
    )
    # «Скрыть стоимость оборудования»: только для документов (PDF) — там остаются
    # наименования, количества, «Итого по разделу», коэффициент и общие суммы.
    # Редактор сметы показывает цены всегда.
    hide_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    section: Mapped[ProjectSection] = relationship(back_populates="versions")
    created_by = relationship("User", foreign_keys=[created_by_id])
    locked_by = relationship("User", foreign_keys=[locked_by_id])

    lines: Mapped[list[EstimateLine]] = relationship(
        back_populates="estimate",
        cascade="all, delete-orphan",
        order_by="EstimateLine.sort_order, EstimateLine.id",
    )

    @property
    def display_number(self) -> str:
        """«EST-2026-015 · v2» — номер сметы стенда и номер версии."""
        return f"{self.section.number} · v{self.version_no}"


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
    # Строка-комплект: бронируемая позиция, в смете выводится только название (kit).
    kit_id: Mapped[int | None] = mapped_column(
        ForeignKey("kits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Строка-комплект аксессуаров: аналогично kit_id, но комплект проектный (не складской).
    accessory_kit_id: Mapped[int | None] = mapped_column(
        ForeignKey("accessory_kits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Произвольную строку можно исключить из переноса в packing-лист (ТЗ §16.5, §17.9).
    add_to_packing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    # Коэффициент строки скорректирован вручную — смена коэффициента сметы его
    # не перезаписывает (сброс — reset_line_coefficient).
    coefficient_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Индивидуальная скидка строки в процентах (ТЗ §16.7). Применяется к строке
    # до общей скидки сметы, которая считается от подытога.
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    estimate: Mapped[Estimate] = relationship(back_populates="lines")

    @property
    def is_kit(self) -> bool:
        """Строка-комплект (бронируемая позиция «Комплект»)."""
        return self.kit_id is not None

    @property
    def is_accessory_kit(self) -> bool:
        """Строка-комплект аксессуаров (проектный, см. app.accessory_kits)."""
        return self.accessory_kit_id is not None

    @property
    def line_total(self) -> Decimal:
        """Итог строки = цена × количество × коэффициент × (1 − скидка строки) (ТЗ §16.4)."""
        from app.estimates.totals import money

        gross = self.unit_price * self.quantity * self.coefficient
        return money(gross * (Decimal("1") - self.discount_percent / Decimal("100")))
