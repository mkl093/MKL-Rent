"""Проект: фактические даты отгрузки и возврата оборудования

Revision ID: 0012_project_ship_return_dates
Revises: 0011_kit_weight
Create Date: 2026-07-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_project_ship_return_dates"
down_revision: str | None = "0011_kit_weight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("shipped_date", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("returned_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("returned_date")
        batch_op.drop_column("shipped_date")
