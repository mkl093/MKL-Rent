"""Приёмка оборудования (ТЗ §56)

Revision ID: 0024_returns
Revises: 0023_guest_access
Create Date: 2026-08-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_returns"
down_revision: str | None = "0023_guest_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "return_lists",
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
    op.create_index("ix_return_lists_number", "return_lists", ["number"], unique=True)
    op.create_index("ix_return_lists_project_id", "return_lists", ["project_id"])

    op.create_table(
        "return_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_list_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("kit_id", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_serial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("category_name", sa.String(length=255), nullable=True),
        sa.Column("subcategory_name", sa.String(length=255), nullable=True),
        sa.Column("expected_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("returned_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["return_list_id"], ["return_lists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["kit_id"], ["kits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_return_lines_return_list_id", "return_lines", ["return_list_id"])
    op.create_index("ix_return_lines_model_id", "return_lines", ["model_id"])
    op.create_index("ix_return_lines_kit_id", "return_lines", ["kit_id"])

    op.create_table(
        "return_serial_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_line_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=128), nullable=False),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("is_returned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("condition", sa.String(length=8), nullable=False, server_default="ok"),
        sa.Column("condition_comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["return_line_id"], ["return_lines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["equipment_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("return_line_id", "item_id", name="uq_return_line_item"),
    )
    op.create_index(
        "ix_return_serial_items_return_line_id", "return_serial_items", ["return_line_id"]
    )
    op.create_index("ix_return_serial_items_item_id", "return_serial_items", ["item_id"])


def downgrade() -> None:
    op.drop_table("return_serial_items")
    op.drop_table("return_lines")
    op.drop_table("return_lists")
