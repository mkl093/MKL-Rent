"""Возврат комплекта аксессуаров: чек-лист по содержимому

accessory_kit_id добавлен в return_lines по той же схеме, что и kit_id.
ReturnAccessoryKitLine — снимок каждой позиции содержимого кабелярки на момент
оформления возврата, чтобы недостачу мелких кабелей/коннекторов можно было
сверить по позициям, а не только «кейс целиком» (ТЗ §56.1, §56.3).

Revision ID: 0026_return_accessory_kits
Revises: 0025_accessory_kits
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_return_accessory_kits"
down_revision: str | None = "0025_accessory_kits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("return_lines") as batch_op:
        batch_op.add_column(sa.Column("accessory_kit_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_return_lines_accessory_kit_id", "accessory_kits", ["accessory_kit_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_return_lines_accessory_kit_id", "return_lines", ["accessory_kit_id"])

    op.create_table(
        "return_accessory_kit_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_line_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("expected_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("returned_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["return_line_id"], ["return_lines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_return_accessory_kit_lines_return_line_id", "return_accessory_kit_lines", ["return_line_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_return_accessory_kit_lines_return_line_id", "return_accessory_kit_lines")
    op.drop_table("return_accessory_kit_lines")

    op.drop_index("ix_return_lines_accessory_kit_id", "return_lines")
    with op.batch_alter_table("return_lines") as batch_op:
        batch_op.drop_constraint("fk_return_lines_accessory_kit_id", type_="foreignkey")
        batch_op.drop_column("accessory_kit_id")
