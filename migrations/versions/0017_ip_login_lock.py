"""Ограничение попыток входа по IP-адресу

Добавляет таблицу ip_login_locks для отдельного (от блокировки аккаунта)
rate-limit'а по IP: 20 неудачных попыток входа за 15 минут блокируют вход
с этого IP на 15 минут, независимо от того, к каким логинам шли попытки.
Событие блокировки пишется в журнал действий (audit_log, event_type
auth_login_blocked).

Revision ID: 0017_ip_login_lock
Revises: 0016_location_defect_usable
Create Date: 2026-08-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ip_login_lock"
down_revision: str | None = "0016_location_defect_usable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ip_login_locks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ip_login_locks_ip_address", "ip_login_locks", ["ip_address"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_ip_login_locks_ip_address", table_name="ip_login_locks")
    op.drop_table("ip_login_locks")
