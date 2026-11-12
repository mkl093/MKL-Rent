"""Коэффициент сметы и «Скрыть стоимость оборудования»

- estimates.rental_coefficient — коэффициент аренды сметы целиком (по умолчанию
  из проекта). Смена коэффициента сметы переписывает коэффициент всех строк,
  кроме скорректированных вручную.
- estimate_lines.coefficient_custom — коэффициент строки задан вручную и при
  смене коэффициента сметы не перезаписывается.
- estimates.hide_prices — в документах (PDF) скрыть цены позиций: остаются
  наименования, количества, «Итого по разделу», коэффициент и общие суммы.
  На редактор сметы флаг не влияет.

Backfill: коэффициент сметы = коэффициент проекта; строка считается
скорректированной, если её коэффициент отличается от коэффициента проекта.

Revision ID: 0028_estimate_coef_hide_prices
Revises: 0027_packing_confirmed_by_scan
Create Date: 2026-11-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_estimate_coef_hide_prices"
down_revision: str | None = "0027_packing_confirmed_by_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # rental_coefficient добавляется nullable и становится NOT NULL после backfill:
    # SQLite в batch-режиме пересоздаёт таблицу и копирует строки без server_default.
    with op.batch_alter_table("estimates") as batch_op:
        batch_op.add_column(sa.Column("rental_coefficient", sa.Numeric(6, 3), nullable=True))
        batch_op.add_column(
            sa.Column("hide_prices", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.add_column(
            sa.Column(
                "coefficient_custom", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    op.execute(
        """
        UPDATE estimates
        SET rental_coefficient = COALESCE(
            (SELECT projects.rental_coefficient FROM projects
             WHERE projects.id = estimates.project_id),
            1
        )
        """
    )
    op.execute(
        """
        UPDATE estimate_lines
        SET coefficient_custom = (
            estimate_lines.coefficient <> (
                SELECT estimates.rental_coefficient FROM estimates
                WHERE estimates.id = estimate_lines.estimate_id
            )
        )
        """
    )

    with op.batch_alter_table("estimates") as batch_op:
        batch_op.alter_column(
            "rental_coefficient",
            existing_type=sa.Numeric(6, 3),
            nullable=False,
            server_default=sa.text("1"),
        )


def downgrade() -> None:
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.drop_column("coefficient_custom")
    with op.batch_alter_table("estimates") as batch_op:
        batch_op.drop_column("hide_prices")
        batch_op.drop_column("rental_coefficient")
