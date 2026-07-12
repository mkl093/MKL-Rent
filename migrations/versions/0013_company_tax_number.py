"""Настройки компании: отдельное поле «Налоговый номер» (отдельно от VAT ID)

Revision ID: 0013_company_tax_number
Revises: 0012_project_ship_return_dates
Create Date: 2026-07-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_company_tax_number"
down_revision: str | None = "0012_project_ship_return_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("company_settings", sa.Column("tax_number", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("company_settings") as batch_op:
        batch_op.drop_column("tax_number")
