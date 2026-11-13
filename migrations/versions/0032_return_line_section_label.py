"""Метка стендов у строки листа приёмки

return_lines.section_label — снимок распределения строки по стендам на момент
оформления возврата, только для отображения: «A: 4 · B: 2» у слитой строки
складской модели, имя стенда — у комплекта, кабелярки и дополнительной позиции.
Заполняется, только если у проекта несколько packing-листов; у существующих
листов приёмки — NULL (метка не показывается).

Revision ID: 0032_return_section_label
Revises: 0031_packing_per_section
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_return_section_label"
down_revision: str | None = "0031_packing_per_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("return_lines") as batch_op:
        batch_op.add_column(sa.Column("section_label", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("return_lines") as batch_op:
        batch_op.drop_column("section_label")
