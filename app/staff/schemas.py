"""Pydantic-схемы персонала и занятости (ТЗ §54)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.staff.enums import AssignmentStatus, AssignmentType


class DepartmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sort_order: int = 0


class EmployeeInput(BaseModel):
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    position: str | None = Field(default=None, max_length=255)
    department_id: int | None = None
    user_id: int | None = None
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    comment: str | None = None


class AssignmentInput(BaseModel):
    employee_id: int
    project_id: int | None = None
    type: AssignmentType
    status: AssignmentStatus = AssignmentStatus.PLANNED
    starts_at: datetime
    ends_at: datetime
    title: str | None = Field(default=None, max_length=255)
    comment: str | None = None


class BulkAssignmentInput(BaseModel):
    employee_ids: list[int] = Field(min_length=1)
    project_id: int | None = None
    type: AssignmentType
    status: AssignmentStatus = AssignmentStatus.PLANNED
    starts_at: datetime
    ends_at: datetime
    title: str | None = Field(default=None, max_length=255)
    comment: str | None = None
