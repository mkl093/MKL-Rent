"""Календарь занятости персонала

Добавляет справочники «Отделы» и «Сотрудники» (отдельно от учётных записей
пользователей — у монтажников/водителей логина может не быть) и таблицу
записей занятости staff_assignments: проектная занятость и занятость без
привязки к проекту (отпуск, больничный, недоступен и т.п.), см. ТЗ §54.
Также добавляет company_settings.work_day_start/work_day_end для подсветки
рабочего времени в календаре.

Revision ID: 0018_staff_calendar
Revises: 0017_ip_login_lock
Create Date: 2026-08-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_staff_calendar"
down_revision: str | None = "0017_ip_login_lock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_employees_department_id", "employees", ["department_id"])

    op.create_table(
        "staff_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_assignment_ends_after_starts"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_assignments_employee_id", "staff_assignments", ["employee_id"])
    op.create_index("ix_staff_assignments_project_id", "staff_assignments", ["project_id"])
    op.create_index(
        "ix_staff_assignments_employee_range",
        "staff_assignments",
        ["employee_id", "starts_at", "ends_at"],
    )

    op.add_column(
        "company_settings",
        sa.Column("work_day_start", sa.String(length=5), nullable=False, server_default="08:00"),
    )
    op.add_column(
        "company_settings",
        sa.Column("work_day_end", sa.String(length=5), nullable=False, server_default="18:00"),
    )


def downgrade() -> None:
    with op.batch_alter_table("company_settings") as batch_op:
        batch_op.drop_column("work_day_end")
        batch_op.drop_column("work_day_start")

    op.drop_index("ix_staff_assignments_employee_range", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_project_id", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_employee_id", table_name="staff_assignments")
    op.drop_table("staff_assignments")

    op.drop_index("ix_employees_department_id", table_name="employees")
    op.drop_table("employees")

    op.drop_table("departments")
