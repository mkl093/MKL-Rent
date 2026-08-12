"""Файловое хранилище мануалов и сертификатов испытаний

Добавляет equipment_manuals (файлы мануалов модели оборудования — PDF/ZIP,
несколько на модель) и equipment_certificates (файлы сертификатов испытаний
конкретной единицы оборудования — несколько на единицу, с датой выдачи и
сроком действия для контроля просрочки).

Revision ID: 0019_equipment_documents
Revises: 0018_staff_calendar
Create Date: 2026-08-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_equipment_documents"
down_revision: str | None = "0018_staff_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equipment_manuals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_manuals_model_id", "equipment_manuals", ["model_id"])

    op.create_table(
        "equipment_certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["equipment_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_certificates_item_id", "equipment_certificates", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_equipment_certificates_item_id", table_name="equipment_certificates")
    op.drop_table("equipment_certificates")

    op.drop_index("ix_equipment_manuals_model_id", table_name="equipment_manuals")
    op.drop_table("equipment_manuals")
