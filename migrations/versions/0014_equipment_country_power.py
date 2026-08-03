"""Оборудование: страна-производитель и энергопотребление (Вт)

Добавляет модели полей «Страна-производитель» и энергопотребление
(пиковое/номинальное), а строкам packing-листа — снимок энергопотребления
для итогов по категориям и суммарно.

Revision ID: 0014_equipment_country_power
Revises: 0013_company_tax_number
Create Date: 2026-08-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_equipment_country_power"
down_revision: str | None = "0013_company_tax_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equipment_models",
        sa.Column("country_of_origin", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "equipment_models",
        sa.Column("has_power", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("equipment_models", sa.Column("power_peak_w", sa.Integer(), nullable=True))
    op.add_column("equipment_models", sa.Column("power_nominal_w", sa.Integer(), nullable=True))

    op.add_column(
        "packing_lines",
        sa.Column("has_power", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "packing_lines",
        sa.Column("power_peak_w", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "packing_lines",
        sa.Column("power_nominal_w", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.drop_column("power_nominal_w")
        batch_op.drop_column("power_peak_w")
        batch_op.drop_column("has_power")
    with op.batch_alter_table("equipment_models") as batch_op:
        batch_op.drop_column("power_nominal_w")
        batch_op.drop_column("power_peak_w")
        batch_op.drop_column("has_power")
        batch_op.drop_column("country_of_origin")
