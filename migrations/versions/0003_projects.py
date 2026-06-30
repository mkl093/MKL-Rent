"""Этап 3: проекты, брони, счётчики номеров

Revision ID: 0003_projects
Revises: 0002_inventory
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_projects"
down_revision: str | None = "0002_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sequence_counters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_type", "year", name="uq_sequence_doc_year"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("rental_coefficient", sa.Numeric(precision=6, scale=3), nullable=False, server_default="1"),
        sa.Column("vat", sa.Numeric(precision=5, scale=2), nullable=False, server_default="0"),
        sa.Column("customer", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_number", "projects", ["number"], unique=True)
    op.create_index("ix_projects_status", "projects", ["status"])

    op.create_table(
        "project_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "model_id", name="uq_reservation_project_model"),
    )
    op.create_index("ix_project_reservations_project_id", "project_reservations", ["project_id"])
    op.create_index("ix_project_reservations_model_id", "project_reservations", ["model_id"])


def downgrade() -> None:
    op.drop_table("project_reservations")
    op.drop_table("projects")
    op.drop_table("sequence_counters")
