"""Пункт 3: штрих-код опционален + единицы для количественных моделей

Revision ID: 0008_units
Revises: 0007_audit
Create Date: 2026-07-06
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0008_units"
down_revision: str | None = "0007_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Штрих-код становится необязательным (уникален при наличии).
    with op.batch_alter_table("equipment_items") as batch:
        batch.alter_column("barcode", existing_type=sa.String(length=128), nullable=True)

    # Поединичный учёт для всех моделей: создаём единицы у количественных моделей
    # по их total_quantity (без штрих-кода, статус «Активно»).
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    rows = bind.execute(
        sa.text(
            "SELECT id, total_quantity FROM equipment_models WHERE accounting_type = 'quantity'"
        )
    ).fetchall()
    insert = sa.text(
        "INSERT INTO equipment_items (model_id, barcode, status, created_at, updated_at) "
        "VALUES (:m, NULL, 'active', :c, :c)"
    )
    for model_id, qty in rows:
        for _ in range(int(qty or 0)):
            bind.execute(insert, {"m": model_id, "c": now})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM equipment_items WHERE barcode IS NULL"))
    with op.batch_alter_table("equipment_items") as batch:
        batch.alter_column("barcode", existing_type=sa.String(length=128), nullable=False)
