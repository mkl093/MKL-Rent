"""Синхронизация сметы и packing-листа: перенос произвольных строк, ручные позиции

Добавляет estimate_lines.add_to_packing (произвольную строку сметы можно
исключить из переноса в packing-лист), packing_lines.estimate_line_id (связь
с породившей строкой сметы для синхронизации произвольных позиций) и
packing_lines.is_manual (строка добавлена в packing вручную, минуя смету —
синхронизация её не трогает).

Revision ID: 0021_estimate_packing_sync
Revises: 0020_project_color
Create Date: 2026-08-19
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_estimate_packing_sync"
down_revision: str | None = "0020_project_color"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.add_column(
            sa.Column("add_to_packing", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.add_column(sa.Column("estimate_line_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_foreign_key(
            "fk_packing_lines_estimate_line_id",
            "estimate_lines",
            ["estimate_line_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_packing_lines_estimate_line_id", "packing_lines", ["estimate_line_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_packing_lines_estimate_line_id", "packing_lines")
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.drop_constraint("fk_packing_lines_estimate_line_id", type_="foreignkey")
        batch_op.drop_column("is_manual")
        batch_op.drop_column("estimate_line_id")
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.drop_column("add_to_packing")
