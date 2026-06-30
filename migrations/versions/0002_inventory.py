"""Этап 2: складской домен (категории, модели, экземпляры, истории)

Revision ID: 0002_inventory
Revises: 0001_initial
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_inventory"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "subcategories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_subcategory_name"),
    )
    op.create_index("ix_subcategories_category_id", "subcategories", ["category_id"])

    op.create_table(
        "equipment_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("accounting_type", sa.String(length=20), nullable=False),
        sa.Column("weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("length_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("base_price_eur", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("total_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subcategory_id", sa.Integer(), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("internal_sku", sa.String(length=100), nullable=True),
        sa.Column("photo_path", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
        sa.ForeignKeyConstraint(["subcategory_id"], ["subcategories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_models_category_id", "equipment_models", ["category_id"])
    op.create_index("ix_equipment_models_subcategory_id", "equipment_models", ["subcategory_id"])
    op.create_index("ix_equipment_models_name", "equipment_models", ["name"])
    op.create_index("ix_equipment_models_manufacturer", "equipment_models", ["manufacturer"])

    op.create_table(
        "packing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("packing_type", sa.String(length=10), nullable=False),
        sa.Column("empty_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("length_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id"),
    )

    op.create_table(
        "equipment_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="active"),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("inventory_number", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_items_model_id", "equipment_items", ["model_id"])
    op.create_index("ix_equipment_items_barcode", "equipment_items", ["barcode"], unique=True)
    op.create_index("ix_equipment_items_status", "equipment_items", ["status"])

    op.create_table(
        "equipment_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("old_status", sa.String(length=10), nullable=True),
        sa.Column("new_status", sa.String(length=10), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["equipment_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_status_history_item_id", "equipment_status_history", ["item_id"]
    )

    op.create_table(
        "quantity_adjustments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("old_quantity", sa.Integer(), nullable=False),
        sa.Column("new_quantity", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quantity_adjustments_model_id", "quantity_adjustments", ["model_id"])


def downgrade() -> None:
    op.drop_table("quantity_adjustments")
    op.drop_table("equipment_status_history")
    op.drop_table("equipment_items")
    op.drop_table("packing_rules")
    op.drop_table("equipment_models")
    op.drop_table("subcategories")
    op.drop_table("categories")
