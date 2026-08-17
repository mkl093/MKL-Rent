"""Цветовая маркировка проекта в календаре занятости

Добавляет projects.color (hex "#rrggbb", выбирается из пресетов или
произвольно) и projects.calendar_bar (показывать отдельную полосу на весь
срок проекта в календаре, а не только красить записи занятости).

Revision ID: 0020_project_color
Revises: 0019_equipment_documents
Create Date: 2026-08-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_project_color"
down_revision: str | None = "0019_equipment_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("color", sa.String(length=7), nullable=True))
    op.add_column(
        "projects",
        sa.Column("calendar_bar", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("calendar_bar")
        batch_op.drop_column("color")
