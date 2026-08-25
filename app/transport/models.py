"""Модели транспорта: справочник машин и распределение packing-листа по машинам.

ProjectVehicle хранит снимок машины на момент добавления в проект (имя, гос.
номер, грузоподъёмность) — по той же логике, что и снимки в смете и packing-
листе (ТЗ §7.3, §17.2): правка глобального справочника не должна задним числом
менять уже сформированные документы проекта.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.packing.models import PackingLine


class Vehicle(Base, TimestampMixin):
    """Глобальный справочник машин (переиспользуется между проектами)."""

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plate_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProjectVehicle(Base, TimestampMixin):
    """Машина, добавленная в проект — снимок справочника (см. докстринг модуля)."""

    __tablename__ = "project_vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plate_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assignments: Mapped[list[TransportAssignment]] = relationship(
        back_populates="project_vehicle",
        cascade="all, delete-orphan",
        order_by="TransportAssignment.id",
    )


class TransportAssignment(Base):
    """Назначение части строки packing-листа в машину проекта."""

    __tablename__ = "transport_assignments"
    __table_args__ = (
        UniqueConstraint(
            "project_vehicle_id", "packing_line_id", name="uq_transport_assignment_line"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("project_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    packing_line_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project_vehicle: Mapped[ProjectVehicle] = relationship(back_populates="assignments")
    line: Mapped[PackingLine] = relationship()
