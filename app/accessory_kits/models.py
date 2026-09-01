"""Модели «Комплект аксессуаров» — кабелярка (обиходное название, для истории).

В отличие от складского Комплекта (``app.inventory.models.Kit``), комплект
аксессуаров существует в рамках одного проекта: содержимое каждый раз новое
(коммутация, крепёж, кабели), хранить его на складе как отдельную единицу
нецелесообразно. Содержимое строится либо со склада (модель + количество —
снимок, без привязки к конкретным экземплярам EquipmentItem, как у Kit), либо
произвольными позициями. Содержимое со склада учитывается в резерве проекта
наравне со сметой — см. ``app.estimates.service.sync_reservations``.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.enums import KitWeightMode
from app.inventory.models import _enum_column


class AccessoryKit(Base, TimestampMixin):
    """Комплект аксессуаров проекта."""

    __tablename__ = "accessory_kits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Штрих-код кейса — свободный, вводится/сканируется вручную (как у EquipmentItem).
    barcode: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )

    # Настройка веса — та же механика, что и у складского Комплекта (KitWeightMode).
    weight_mode: Mapped[KitWeightMode] = mapped_column(
        _enum_column(KitWeightMode, 12), default=KitWeightMode.CONTENT, nullable=False
    )
    weight_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    length_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    lines: Mapped[list[AccessoryKitLine]] = relationship(
        back_populates="accessory_kit",
        cascade="all, delete-orphan",
        order_by="AccessoryKitLine.sort_order, AccessoryKitLine.id",
    )


class AccessoryKitLine(Base):
    """Позиция содержимого комплекта аксессуаров: со склада или произвольная."""

    __tablename__ = "accessory_kit_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accessory_kit_id: Mapped[int] = mapped_column(
        ForeignKey("accessory_kits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Снимок на момент добавления (ТЗ §7.3 — тот же принцип, что и везде в проекте).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    accessory_kit: Mapped[AccessoryKit] = relationship(back_populates="lines")


class AccessoryKitTemplate(Base, TimestampMixin):
    """Переиспользуемый шаблон состава комплекта аксессуаров (типовой сетап).

    Глобальный справочник (не привязан к проекту) — состав копируется снимком
    при создании комплекта аксессуаров «из шаблона», дальше живёт независимо.
    """

    __tablename__ = "accessory_kit_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[AccessoryKitTemplateLine]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="AccessoryKitTemplateLine.sort_order, AccessoryKitTemplateLine.id",
    )


class AccessoryKitTemplateLine(Base):
    """Позиция состава шаблона."""

    __tablename__ = "accessory_kit_template_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("accessory_kit_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    template: Mapped[AccessoryKitTemplate] = relationship(back_populates="lines")
