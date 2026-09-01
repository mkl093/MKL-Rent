"""Комплект аксессуаров: проектная кабелярка с содержимым, шаблоны состава

AccessoryKit — существует в рамках проекта (в отличие от складского Kit),
содержимое каждый раз новое. AccessoryKitTemplate — переиспользуемый типовой
состав (глобальный справочник), копируется снимком при создании комплекта
«из шаблона». accessory_kit_id добавлен в estimate_lines и packing_lines по
той же схеме, что и kit_id (0010_kits) — бронируемая позиция сметы/строка
packing-листа.

Revision ID: 0025_accessory_kits
Revises: 0024_returns
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_accessory_kits"
down_revision: str | None = "0024_returns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accessory_kits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("barcode", sa.String(length=128), nullable=True),
        sa.Column("weight_mode", sa.String(length=12), nullable=False, server_default="content"),
        sa.Column("weight_value", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("length_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accessory_kits_project_id", "accessory_kits", ["project_id"])
    op.create_index("ix_accessory_kits_barcode", "accessory_kits", ["barcode"], unique=True)

    op.create_table(
        "accessory_kit_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accessory_kit_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit_weight_kg", sa.Numeric(precision=10, scale=3), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["accessory_kit_id"], ["accessory_kits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accessory_kit_lines_accessory_kit_id", "accessory_kit_lines", ["accessory_kit_id"])
    op.create_index("ix_accessory_kit_lines_model_id", "accessory_kit_lines", ["model_id"])

    op.create_table(
        "accessory_kit_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accessory_kit_templates_name", "accessory_kit_templates", ["name"])

    op.create_table(
        "accessory_kit_template_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["template_id"], ["accessory_kit_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_accessory_kit_template_lines_template_id", "accessory_kit_template_lines", ["template_id"]
    )
    op.create_index(
        "ix_accessory_kit_template_lines_model_id", "accessory_kit_template_lines", ["model_id"]
    )

    # Строка-комплект аксессуаров в смете (аналогично kit_id, 0010_kits).
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.add_column(sa.Column("accessory_kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_estimate_lines_accessory_kit_id", "accessory_kits", ["accessory_kit_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_estimate_lines_accessory_kit_id", "estimate_lines", ["accessory_kit_id"])

    # Строка-комплект аксессуаров в packing-листе.
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.add_column(sa.Column("accessory_kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_packing_lines_accessory_kit_id", "accessory_kits", ["accessory_kit_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_packing_lines_accessory_kit_id", "packing_lines", ["accessory_kit_id"])


def downgrade() -> None:
    op.drop_index("ix_packing_lines_accessory_kit_id", "packing_lines")
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.drop_constraint("fk_packing_lines_accessory_kit_id", type_="foreignkey")
        batch_op.drop_column("accessory_kit_id")

    op.drop_index("ix_estimate_lines_accessory_kit_id", "estimate_lines")
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.drop_constraint("fk_estimate_lines_accessory_kit_id", type_="foreignkey")
        batch_op.drop_column("accessory_kit_id")

    op.drop_index("ix_accessory_kit_template_lines_model_id", "accessory_kit_template_lines")
    op.drop_index("ix_accessory_kit_template_lines_template_id", "accessory_kit_template_lines")
    op.drop_table("accessory_kit_template_lines")

    op.drop_index("ix_accessory_kit_templates_name", "accessory_kit_templates")
    op.drop_table("accessory_kit_templates")

    op.drop_index("ix_accessory_kit_lines_model_id", "accessory_kit_lines")
    op.drop_index("ix_accessory_kit_lines_accessory_kit_id", "accessory_kit_lines")
    op.drop_table("accessory_kit_lines")

    op.drop_index("ix_accessory_kits_barcode", "accessory_kits")
    op.drop_index("ix_accessory_kits_project_id", "accessory_kits")
    op.drop_table("accessory_kits")
