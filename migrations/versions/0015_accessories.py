"""Справочник аксессуаров и комплектация моделей

Каталог: категория → аксессуар (один уровень). Аксессуары назначаются моделям
с количеством и суммируются в packing-листе.

Revision ID: 0015_accessories
Revises: 0014_equipment_country_power
Create Date: 2026-08-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_accessories"
down_revision: str | None = "0014_equipment_country_power"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accessory_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "accessories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["accessory_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_accessory_name"),
    )
    op.create_index("ix_accessories_category_id", "accessories", ["category_id"])

    op.create_table(
        "equipment_model_accessories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("accessory_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accessory_id"], ["accessories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "accessory_id", name="uq_model_accessory"),
    )
    op.create_index(
        "ix_equipment_model_accessories_model_id", "equipment_model_accessories", ["model_id"]
    )
    op.create_index(
        "ix_equipment_model_accessories_accessory_id",
        "equipment_model_accessories",
        ["accessory_id"],
    )


def downgrade() -> None:
    op.drop_table("equipment_model_accessories")
    op.drop_index("ix_accessories_category_id", table_name="accessories")
    op.drop_table("accessories")
    op.drop_table("accessory_categories")
