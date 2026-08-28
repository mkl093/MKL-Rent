"""Гостевой доступ: таблица guest_users и общий флаг categories.hidden_from_guests

Revision ID: 0023_guest_access
Revises: 0022_transport
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_guest_access"
down_revision: str | None = "0022_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("access_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_users_username", "guest_users", ["username"], unique=True)

    op.add_column(
        "categories",
        sa.Column("hidden_from_guests", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("categories", "hidden_from_guests")
    op.drop_index("ix_guest_users_username", table_name="guest_users")
    op.drop_table("guest_users")
