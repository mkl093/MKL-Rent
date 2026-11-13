"""Packing-лист на каждый стенд

packing_lists.section_id — стенд, которому принадлежит лист (один лист на стенд,
ON DELETE RESTRICT). Ограничение «один packing-лист на проект» (UNIQUE project_id)
снимается: у проекта столько листов, сколько стендов.

Backfill: существующий лист привязывается к первому стенду проекта (после 0029 у
каждого проекта со сметой ровно один стенд — «Основной стенд»). Лист проекта без
сметы (а значит и без стенда) получает стенд «Основной стенд» с пустой активной
версией сметы. Сформированные PDF packing-листа и листа комплектации переходят из
области проекта в область листа («packing:{id}»).

Revision ID: 0031_packing_per_section
Revises: 0030_packing_from_estimate
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0031_packing_per_section"
down_revision: str | None = "0030_packing_from_estimate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SECTION_NAME = "Основной стенд"

# В SQLite ограничение UNIQUE(project_id) из 0005 безымянное; batch-режим
# находит его по этому шаблону имени.
_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _drop_project_unique(bind) -> None:
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "packing_lists", recreate="always", naming_convention=_NAMING
        ) as batch_op:
            batch_op.drop_constraint("uq_packing_lists_project_id", type_="unique")
        return
    for uc in sa.inspect(bind).get_unique_constraints("packing_lists"):
        if uc["column_names"] == ["project_id"]:
            op.drop_constraint(uc["name"], "packing_lists", type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    _drop_project_unique(bind)

    with op.batch_alter_table("packing_lists") as batch_op:
        batch_op.add_column(sa.Column("section_id", sa.Integer(), nullable=True))

    packing = sa.table(
        "packing_lists",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("section_id", sa.Integer()),
    )
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
        sa.column("section_id", sa.Integer()),
        sa.column("version_no", sa.Integer()),
        sa.column("number", sa.String()),
        sa.column("discount_percent", sa.Numeric(5, 2)),
        sa.column("rental_coefficient", sa.Numeric(6, 3)),
        sa.column("hide_prices", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("is_locked", sa.Boolean()),
        sa.column("activated_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    projects = sa.table(
        "projects",
        sa.column("id", sa.Integer()),
        sa.column("rental_coefficient", sa.Numeric(6, 3)),
    )

    rows = bind.execute(sa.select(packing.c.id, packing.c.project_id)).all()
    now = datetime.now(UTC)
    for packing_id, project_id in rows:
        section_id = bind.execute(
            sa.select(sections.c.id)
            .where(sections.c.project_id == project_id)
            .order_by(sections.c.sort_order, sections.c.id)
            .limit(1)
        ).scalar()
        if section_id is None:
            number = f"EST-LEGACY-{project_id}"
            coef = bind.execute(
                sa.select(projects.c.rental_coefficient).where(projects.c.id == project_id)
            ).scalar()
            section_id = bind.execute(
                sections.insert()
                .values(
                    project_id=project_id,
                    name=DEFAULT_SECTION_NAME,
                    number=number,
                    sort_order=0,
                    created_at=now,
                    updated_at=now,
                )
                .returning(sections.c.id)
            ).scalar_one()
            bind.execute(
                estimates.insert().values(
                    project_id=project_id,
                    section_id=section_id,
                    version_no=1,
                    number=number,
                    discount_percent=0,
                    rental_coefficient=coef if coef is not None else 1,
                    hide_prices=False,
                    is_active=True,
                    is_locked=False,
                    activated_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        bind.execute(
            packing.update().where(packing.c.id == packing_id).values(section_id=section_id)
        )

    with op.batch_alter_table("packing_lists") as batch_op:
        batch_op.alter_column("section_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            "fk_packing_lists_section_id",
            "project_sections",
            ["section_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_packing_lists_section_id", ["section_id"], unique=True)

    docs = sa.table(
        "generated_documents",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("doc_type", sa.String()),
        sa.column("scope", sa.String()),
    )
    packing_by_project = {project_id: packing_id for packing_id, project_id in rows}
    doc_rows = bind.execute(
        sa.select(docs.c.id, docs.c.project_id).where(
            docs.c.doc_type.in_(["packing", "picking"]), docs.c.scope == "project"
        )
    ).all()
    for doc_id, project_id in doc_rows:
        packing_id = packing_by_project.get(project_id)
        if packing_id is not None:
            bind.execute(
                docs.update().where(docs.c.id == doc_id).values(scope=f"packing:{packing_id}")
            )


def downgrade() -> None:
    # Возврат к «один лист на проект»: остаётся лист первого стенда, листы других
    # стендов удаляются вместе со строками.
    bind = op.get_bind()
    # Общий packing-лист проекта (PDF в области проекта) без листов стендов не
    # существует — удаляем, иначе конфликт уникальности при возврате PDF стенда.
    bind.execute(
        sa.text(
            "DELETE FROM generated_documents "
            "WHERE doc_type IN ('packing', 'picking') AND scope = 'project'"
        )
    )
    # PDF листа первого стенда возвращаются в область проекта, остальные — удаляются.
    bind.execute(
        sa.text(
            "UPDATE generated_documents SET scope = 'project' "
            "WHERE doc_type IN ('packing', 'picking') AND scope = 'packing:' || CAST(("
            "  SELECT MIN(pl.id) FROM packing_lists pl"
            "  WHERE pl.project_id = generated_documents.project_id"
            ") AS TEXT)"
        )
    )
    bind.execute(sa.text("DELETE FROM generated_documents WHERE scope LIKE 'packing:%'"))
    bind.execute(
        sa.text(
            "DELETE FROM packing_lists WHERE id NOT IN ("
            "  SELECT MIN(pl.id) FROM packing_lists pl GROUP BY pl.project_id"
            ")"
        )
    )
    with op.batch_alter_table("packing_lists") as batch_op:
        batch_op.drop_index("ix_packing_lists_section_id")
        batch_op.drop_constraint("fk_packing_lists_section_id", type_="foreignkey")
        batch_op.drop_column("section_id")
        batch_op.create_unique_constraint("uq_packing_lists_project_id", ["project_id"])
