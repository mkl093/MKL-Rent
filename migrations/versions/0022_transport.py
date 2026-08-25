"""Транспорт: справочник машин и распределение packing-листа по машинам

Vehicle — глобальный справочник; ProjectVehicle — снимок машины в проекте
(правки справочника не меняют задним числом уже распределённый проект);
TransportAssignment — количество строки packing-листа, назначенное в машину.

Revision ID: 0022_transport
Revises: 0021_estimate_packing_sync
Create Date: 2026-08-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_transport"
down_revision: str | None = "0021_estimate_packing_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plate_number", sa.String(length=32), nullable=True),
        sa.Column("max_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "project_vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plate_number", sa.String(length=32), nullable=True),
        sa.Column("max_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_vehicles_project_id", "project_vehicles", ["project_id"])
    op.create_index("ix_project_vehicles_vehicle_id", "project_vehicles", ["vehicle_id"])

    op.create_table(
        "transport_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_vehicle_id", sa.Integer(), nullable=False),
        sa.Column("packing_line_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["project_vehicle_id"], ["project_vehicles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["packing_line_id"], ["packing_lines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_vehicle_id", "packing_line_id", name="uq_transport_assignment_line"
        ),
    )
    op.create_index(
        "ix_transport_assignments_project_vehicle_id", "transport_assignments", ["project_vehicle_id"]
    )
    op.create_index(
        "ix_transport_assignments_packing_line_id", "transport_assignments", ["packing_line_id"]
    )


def downgrade() -> None:
    op.drop_table("transport_assignments")
    op.drop_table("project_vehicles")
    op.drop_table("vehicles")
