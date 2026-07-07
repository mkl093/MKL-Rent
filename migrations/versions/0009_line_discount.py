"""Индивидуальная скидка строки сметы (ТЗ §16.7)

Revision ID: 0009_line_discount
Revises: 0008_units
Create Date: 2026-07-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_line_discount"
down_revision: str | None = "0008_units"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "estimate_lines",
        sa.Column(
            "discount_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("estimate_lines", "discount_percent")
