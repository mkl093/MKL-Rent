"""Этап 7: сгенерированные документы (PDF)

Revision ID: 0006_documents
Revises: 0005_packing
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_documents"
down_revision: str | None = "0005_packing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generated_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "doc_type", "language", name="uq_document_combo"),
    )
    op.create_index(
        "ix_generated_documents_project_id", "generated_documents", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("generated_documents")
