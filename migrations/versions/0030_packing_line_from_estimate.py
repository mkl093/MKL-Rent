"""Признак «строка packing-листа перенесена из сметы»

packing_lines.from_estimate — строка создана из произвольной строки сметы
(ТЗ §17.9). Без него синхронизация не убирала такую строку после удаления
строки сметы на PostgreSQL: внешний ключ estimate_line_id ON DELETE SET NULL
обнулял ссылку, и строку уже нельзя было отличить от ручной дополнительной
позиции (на SQLite внешние ключи не проверяются, поэтому ошибка там не видна).

Backfill: from_estimate = (estimate_line_id IS NOT NULL). Строки, ссылка которых
уже была обнулена удалением строки сметы, однозначно восстановить нельзя (они
неотличимы от ручных позиций, созданных до 0021) — остаются ручными.

Revision ID: 0030_packing_from_estimate
Revises: 0029_sections_versions
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_packing_from_estimate"
down_revision: str | None = "0029_sections_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.add_column(
            sa.Column("from_estimate", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    lines = sa.table(
        "packing_lines",
        sa.column("estimate_line_id", sa.Integer()),
        sa.column("from_estimate", sa.Boolean()),
    )
    op.execute(
        lines.update().where(lines.c.estimate_line_id.is_not(None)).values(from_estimate=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("packing_lines") as batch_op:
        batch_op.drop_column("from_estimate")
