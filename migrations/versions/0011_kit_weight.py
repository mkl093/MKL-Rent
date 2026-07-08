"""Комплект: настройка веса упаковки/общего веса (структура «Комплект»)

Revision ID: 0011_kit_weight
Revises: 0010_kits
Create Date: 2026-07-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_kit_weight"
down_revision: str | None = "0010_kits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kits",
        sa.Column("weight_mode", sa.String(length=12), nullable=False, server_default="content"),
    )
    op.add_column("kits", sa.Column("weight_value", sa.Numeric(10, 3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kits") as batch_op:
        batch_op.drop_column("weight_value")
        batch_op.drop_column("weight_mode")
