"""Модели проектов и броней (ТЗ §13, §15).

Даты — только календарные (Date, без времени). Деньги/коэффициенты — Numeric.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.models import _enum_column
from app.projects.enums import ProjectStatus


class Project(Base, TimestampMixin):
    """Проект — центральная сущность (ТЗ §13)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    # Обязательные поля (ТЗ §13.1). Даты nullable, т.к. копия создаётся без дат (§13.8),
    # но обязательны при переводе в «Забронирован».
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rental_coefficient: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), default=Decimal("1"), nullable=False
    )
    vat: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)

    # Необязательные поля (ТЗ §13.2).
    customer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        _enum_column(ProjectStatus, 12),
        default=ProjectStatus.DRAFT,
        nullable=False,
        index=True,
    )

    reservations: Mapped[list[ProjectReservation]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def is_archived(self) -> bool:
        return self.status.is_archived

    @property
    def is_overdue_booked(self) -> bool:
        """Забронирован, но дата окончания уже прошла (ТЗ §5, §13.4)."""
        from app.database import utcnow

        return (
            self.status == ProjectStatus.BOOKED
            and self.end_date is not None
            and self.end_date < utcnow().date()
        )


class ProjectReservation(Base):
    """Бронь количества модели проектом (ТЗ §15).

    Заполняется из сметы (Этап 4). На доступность влияет только пока проект
    в статусе «Забронирован».
    """

    __tablename__ = "project_reservations"
    __table_args__ = (
        UniqueConstraint("project_id", "model_id", name="uq_reservation_project_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project: Mapped[Project] = relationship(back_populates="reservations")
