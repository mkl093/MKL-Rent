"""Стенды проекта и версии смет

- project_sections — стенд/площадка проекта: название, номер сметы EST-YYYY-NNN,
  порядок. Владеет версиями сметы.
- estimates становится версией сметы стенда: section_id, version_no, label,
  is_active (одна активная на стенд — частичный уникальный индекс), заморозка
  (is_locked/locked_at/locked_by_id), автор и время активации. Ограничение
  «одна смета на проект» (UNIQUE project_id) снимается.
- generated_documents.scope — область документа: «project» или «estimate:{id}»
  (PDF конкретной версии сметы). Уникальность — (проект, область, тип, язык).

Backfill: каждая существующая смета → стенд «Основной стенд» с тем же номером,
сама смета — его активная незамороженная версия v1. Сгенерированные PDF смет
переходят в область «estimate:{id}».

Revision ID: 0029_sections_versions
Revises: 0028_estimate_coef_hide_prices
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_sections_versions"
down_revision: str | None = "0028_estimate_coef_hide_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SECTION_NAME = "Основной стенд"

# В SQLite ограничение UNIQUE(project_id) из 0004 безымянное; batch-режим
# находит его по этому шаблону имени.
_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _drop_project_unique(bind) -> None:
    """Снять UNIQUE(project_id) у estimates (имя зависит от СУБД)."""
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "estimates", recreate="always", naming_convention=_NAMING
        ) as batch_op:
            batch_op.drop_constraint("uq_estimates_project_id", type_="unique")
        return
    for uc in sa.inspect(bind).get_unique_constraints("estimates"):
        if uc["column_names"] == ["project_id"]:
            op.drop_constraint(uc["name"], "estimates", type_="unique")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "project_sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_sections_project_id", "project_sections", ["project_id"])
    op.create_index("ix_project_sections_number", "project_sections", ["number"], unique=True)

    _drop_project_unique(bind)

    with op.batch_alter_table("estimates") as batch_op:
        batch_op.add_column(sa.Column("section_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("version_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("label", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_locked", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("locked_by_id", sa.Integer(), nullable=True))

    # Backfill: смета → стенд «Основной стенд» + активная версия v1.
    sections = sa.table(
        "project_sections",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("number", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    estimates = sa.table(
        "estimates",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("number", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("section_id", sa.Integer()),
        sa.column("version_no", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("is_locked", sa.Boolean()),
        sa.column("activated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            estimates.c.id, estimates.c.project_id, estimates.c.number, estimates.c.created_at
        ).order_by(estimates.c.id)
    ).all()
    for est_id, project_id, number, created_at in rows:
        section_id = bind.execute(
            sections.insert()
            .values(
                project_id=project_id,
                name=DEFAULT_SECTION_NAME,
                number=number,
                sort_order=0,
                created_at=created_at,
                updated_at=created_at,
            )
            .returning(sections.c.id)
        ).scalar_one()
        bind.execute(
            estimates.update()
            .where(estimates.c.id == est_id)
            .values(
                section_id=section_id,
                version_no=1,
                is_active=True,
                is_locked=False,
                activated_at=created_at,
            )
        )

    with op.batch_alter_table("estimates") as batch_op:
        batch_op.alter_column("section_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("version_no", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "is_active",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        batch_op.alter_column(
            "is_locked",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        batch_op.create_foreign_key(
            "fk_estimates_section_id",
            "project_sections",
            ["section_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_estimates_created_by_id", "users", ["created_by_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_estimates_locked_by_id", "users", ["locked_by_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_unique_constraint(
            "uq_estimate_section_version", ["section_id", "version_no"]
        )
        batch_op.create_index("ix_estimates_section_id", ["section_id"])
    op.create_index(
        "uq_estimates_active_per_section",
        "estimates",
        ["section_id"],
        unique=True,
        sqlite_where=sa.text("is_active"),
        postgresql_where=sa.text("is_active"),
    )

    # Документы: область «project» / «estimate:{id}».
    with op.batch_alter_table("generated_documents") as batch_op:
        batch_op.add_column(
            sa.Column("scope", sa.String(32), nullable=False, server_default="project")
        )
    docs = sa.table(
        "generated_documents",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("doc_type", sa.String()),
        sa.column("scope", sa.String()),
    )
    estimate_by_project = {project_id: est_id for est_id, project_id, _, _ in rows}
    doc_rows = bind.execute(
        sa.select(docs.c.id, docs.c.project_id).where(docs.c.doc_type == "estimate")
    ).all()
    for doc_id, project_id in doc_rows:
        est_id = estimate_by_project.get(project_id)
        if est_id is not None:
            bind.execute(
                docs.update().where(docs.c.id == doc_id).values(scope=f"estimate:{est_id}")
            )
    with op.batch_alter_table("generated_documents") as batch_op:
        batch_op.drop_constraint("uq_document_combo", type_="unique")
        batch_op.create_unique_constraint(
            "uq_document_scope_combo", ["project_id", "scope", "doc_type", "language"]
        )


def downgrade() -> None:
    # Возврат к «одна смета на проект» возможен только без дополнительных
    # стендов/версий — неактивные версии и лишние стенды удаляются.
    bind = op.get_bind()
    with op.batch_alter_table("generated_documents") as batch_op:
        batch_op.drop_constraint("uq_document_scope_combo", type_="unique")
    # PDF той версии, что останется сметой проекта, возвращаются в область проекта.
    bind.execute(
        sa.text(
            "UPDATE generated_documents SET scope = 'project' "
            "WHERE scope = 'estimate:' || CAST(("
            "  SELECT MIN(e.id) FROM estimates e"
            "  WHERE e.project_id = generated_documents.project_id AND e.is_active"
            ") AS TEXT)"
        )
    )
    bind.execute(sa.text("DELETE FROM generated_documents WHERE scope <> 'project'"))
    with op.batch_alter_table("generated_documents") as batch_op:
        batch_op.drop_column("scope")
        batch_op.create_unique_constraint(
            "uq_document_combo", ["project_id", "doc_type", "language"]
        )

    bind.execute(sa.text("DELETE FROM estimates WHERE NOT is_active"))
    bind.execute(
        sa.text(
            "DELETE FROM estimates WHERE id NOT IN "
            "(SELECT MIN(id) FROM estimates GROUP BY project_id)"
        )
    )
    op.drop_index("uq_estimates_active_per_section", table_name="estimates")
    with op.batch_alter_table("estimates") as batch_op:
        batch_op.drop_index("ix_estimates_section_id")
        batch_op.drop_constraint("uq_estimate_section_version", type_="unique")
        batch_op.drop_constraint("fk_estimates_locked_by_id", type_="foreignkey")
        batch_op.drop_constraint("fk_estimates_created_by_id", type_="foreignkey")
        batch_op.drop_constraint("fk_estimates_section_id", type_="foreignkey")
        for col in (
            "locked_by_id",
            "locked_at",
            "is_locked",
            "created_by_id",
            "activated_at",
            "is_active",
            "label",
            "version_no",
            "section_id",
        ):
            batch_op.drop_column(col)
        batch_op.create_unique_constraint("uq_estimates_project_id", ["project_id"])
    op.drop_index("ix_project_sections_number", table_name="project_sections")
    op.drop_index("ix_project_sections_project_id", table_name="project_sections")
    op.drop_table("project_sections")
