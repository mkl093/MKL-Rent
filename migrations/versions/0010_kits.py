"""Структура «Комплект»: комплекты и бронируемая позиция-комплект

Revision ID: 0010_kits
Revises: 0009_line_discount
Create Date: 2026-07-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_kits"
down_revision: str | None = "0009_line_discount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("photo_path", sa.String(length=500), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kits_name", "kits", ["name"])

    # Единица → комплект (помещённая исключается из свободного стока модели).
    with op.batch_alter_table("equipment_items") as batch_op:
        batch_op.add_column(sa.Column("kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_equipment_items_kit_id", "kits", ["kit_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_equipment_items_kit_id", "equipment_items", ["kit_id"])

    # Строка-комплект в смете (бронируемая позиция, выводится только название).
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.add_column(sa.Column("kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_estimate_lines_kit_id", "kits", ["kit_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_estimate_lines_kit_id", "estimate_lines", ["kit_id"])

    # Строка-комплект в packing-листе (название + перечень комплектации).
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.add_column(sa.Column("kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_packing_lines_kit_id", "kits", ["kit_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_packing_lines_kit_id", "packing_lines", ["kit_id"])

    # Бронь: комплект резервируется целиком (model_id ИЛИ kit_id).
    with op.batch_alter_table("project_reservations") as batch_op:
        batch_op.alter_column("model_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_project_reservations_kit_id",
            "kits",
            ["kit_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_reservation_project_kit", ["project_id", "kit_id"]
        )
    op.create_index("ix_project_reservations_kit_id", "project_reservations", ["kit_id"])


def downgrade() -> None:
    op.drop_index("ix_project_reservations_kit_id", "project_reservations")
    with op.batch_alter_table("project_reservations") as batch_op:
        batch_op.drop_constraint("uq_reservation_project_kit", type_="unique")
        batch_op.drop_constraint("fk_project_reservations_kit_id", type_="foreignkey")
        batch_op.drop_column("kit_id")
        batch_op.alter_column("model_id", existing_type=sa.Integer(), nullable=False)

    op.drop_index("ix_packing_lines_kit_id", "packing_lines")
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.drop_constraint("fk_packing_lines_kit_id", type_="foreignkey")
        batch_op.drop_column("kit_id")

    op.drop_index("ix_estimate_lines_kit_id", "estimate_lines")
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.drop_constraint("fk_estimate_lines_kit_id", type_="foreignkey")
        batch_op.drop_column("kit_id")

    op.drop_index("ix_equipment_items_kit_id", "equipment_items")
    with op.batch_alter_table("equipment_items") as batch_op:
        batch_op.drop_constraint("fk_equipment_items_kit_id", type_="foreignkey")
        batch_op.drop_column("kit_id")

    op.drop_index("ix_kits_name", "kits")
    op.drop_table("kits")
