"""Штрих-код количественных единиц в packing-листе: подтверждение сканом

PackingSerialItem.confirmed_by_scan различает экземпляр, реально подтверждённый
сканированием штрих-кода, от заготовки, автоматически привязанной при простом
наборе количества (без сканирования) — см. app.packing.service. Существующие
записи созданы только через сканирование, поэтому backfill = true.

Revision ID: 0027_packing_confirmed_by_scan
Revises: 0026_return_accessory_kits
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_packing_confirmed_by_scan"
down_revision: str | None = "0026_return_accessory_kits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("packing_serial_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "confirmed_by_scan", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("packing_serial_items") as batch_op:
        batch_op.drop_column("confirmed_by_scan")
