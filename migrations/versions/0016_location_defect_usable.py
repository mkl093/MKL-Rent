"""Расположение на складе и допуск дефектного экземпляра к использованию

Добавляет моделям и комплектам необязательное текстовое поле «Расположение на
складе» (в packing-лист и смету не выводится). Экземплярам оборудования —
флаг usable_despite_defect: единица со статусом «Есть дефект» может быть
отмечена как пригодная к использованию несмотря на дефект и тогда числится
доступной (см. app/inventory/models.py::EquipmentItem.is_usable).

Revision ID: 0016_location_defect_usable
Revises: 0015_accessories
Create Date: 2026-08-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_location_defect_usable"
down_revision: str | None = "0015_accessories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "equipment_models",
        sa.Column("storage_location", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "kits",
        sa.Column("storage_location", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "equipment_items",
        sa.Column(
            "usable_despite_defect", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("equipment_items") as batch_op:
        batch_op.drop_column("usable_despite_defect")
    with op.batch_alter_table("kits") as batch_op:
        batch_op.drop_column("storage_location")
    with op.batch_alter_table("equipment_models") as batch_op:
        batch_op.drop_column("storage_location")
