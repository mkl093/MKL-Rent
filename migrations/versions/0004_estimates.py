"""Этап 4: сметы

Revision ID: 0004_estimates
Revises: 0003_projects
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_estimates"
down_revision: str | None = "0003_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "estimates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=5, scale=2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_estimates_number", "estimates", ["number"], unique=True)
    op.create_index("ix_estimates_project_id", "estimates", ["project_id"])

    op.create_table(
        "estimate_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("estimate_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("category_name", sa.String(length=255), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("coefficient", sa.Numeric(precision=6, scale=3), nullable=False, server_default="1"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_estimate_lines_estimate_id", "estimate_lines", ["estimate_id"])
    op.create_index("ix_estimate_lines_model_id", "estimate_lines", ["model_id"])


def downgrade() -> None:
    op.drop_table("estimate_lines")
    op.drop_table("estimates")
