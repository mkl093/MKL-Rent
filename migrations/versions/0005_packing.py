"""Этап 5: packing-листы

Revision ID: 0005_packing
Revises: 0004_estimates
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_packing"
down_revision: str | None = "0004_estimates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packing_lists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="not_started"),
        sa.Column("shortage_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_packing_lists_number", "packing_lists", ["number"], unique=True)
    op.create_index("ix_packing_lists_project_id", "packing_lists", ["project_id"])

    op.create_table(
        "packing_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("packing_list_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_serial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("category_name", sa.String(length=255), nullable=True),
        sa.Column("subcategory_name", sa.String(length=255), nullable=True),
        sa.Column("planned_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("packed_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("length_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_packing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pack_capacity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pack_empty_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("pack_length_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pack_width_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pack_height_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["packing_list_id"], ["packing_lists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packing_lines_packing_list_id", "packing_lines", ["packing_list_id"])
    op.create_index("ix_packing_lines_model_id", "packing_lines", ["model_id"])

    op.create_table(
        "packing_serial_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("packing_line_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=128), nullable=False),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["packing_line_id"], ["packing_lines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["equipment_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("packing_line_id", "item_id", name="uq_packing_line_item"),
    )
    op.create_index(
        "ix_packing_serial_items_packing_line_id", "packing_serial_items", ["packing_line_id"]
    )
    op.create_index("ix_packing_serial_items_item_id", "packing_serial_items", ["item_id"])


def downgrade() -> None:
    op.drop_table("packing_serial_items")
    op.drop_table("packing_lines")
    op.drop_table("packing_lists")
