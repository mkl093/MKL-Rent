"""Этап 8: журнал действий

Revision ID: 0007_audit
Revises: 0006_documents
Create Date: 2026-07-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit"
down_revision: str | None = "0006_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(length=150), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=True),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])


def downgrade() -> None:
    op.drop_table("audit_log")
