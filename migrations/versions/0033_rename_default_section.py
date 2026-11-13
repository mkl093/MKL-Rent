"""«Основной стенд» → «Основная площадка»

Раздел проекта (ProjectSection) в интерфейсе теперь называется «площадка», а не
«стенд». Миграции 0029/0031 создавали площадку по умолчанию с именем «Основной
стенд»; переименовываем только площадки с этим точным именем (их имя задала
система, а не пользователь). Без этого приложение сочло бы имя пользовательским
и выводило бы «Площадка: Основной стенд» в шапке PDF.

Downgrade возвращает прежнее имя площадкам «Основная площадка».

Revision ID: 0033_rename_default_section
Revises: 0032_return_section_label
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_rename_default_section"
down_revision: str | None = "0032_return_section_label"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_NAME = "Основной стенд"
NEW_NAME = "Основная площадка"


def _rename(old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text("UPDATE project_sections SET name = :new WHERE name = :old"),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    _rename(OLD_NAME, NEW_NAME)


def downgrade() -> None:
    _rename(NEW_NAME, OLD_NAME)
