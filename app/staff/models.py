"""Модели персонала и занятости (ТЗ §54).

Employee — отдельный справочник, не User: у монтажников/водителей может не быть
логина в системе. Связь с User опциональна (employee.user_id).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin
from app.inventory.models import _enum_column
from app.projects.models import Project
from app.staff.enums import AssignmentStatus, AssignmentType


class Department(Base, TimestampMixin):
    """Отдел/группа персонала."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    employees: Mapped[list[Employee]] = relationship(back_populates="department")


class Employee(Base, TimestampMixin):
    """Сотрудник (ТЗ §54): не привязан к учётной записи по умолчанию."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    department: Mapped[Department | None] = relationship(back_populates="employees")
    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()


class Assignment(Base, TimestampMixin):
    """Запись занятости сотрудника — проектная или без привязки к проекту."""

    __tablename__ = "staff_assignments"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_assignment_ends_after_starts"),
        Index("ix_staff_assignments_employee_range", "employee_id", "starts_at", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[AssignmentType] = mapped_column(
        _enum_column(AssignmentType, 16), nullable=False
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        _enum_column(AssignmentStatus, 16), default=AssignmentStatus.PLANNED, nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    employee: Mapped[Employee] = relationship(back_populates="assignments")
    project: Mapped[Project | None] = relationship()
