"""Генерация уникальных номеров документов (ТЗ §14, §38).

Номер вида PRJ-YYYY-NNN. Счётчики уникальны и не редактируются вручную.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.numbering.models import DocType, SequenceCounter


def _get_or_create_counter(db: Session, doc_type: DocType, year: int) -> SequenceCounter:
    stmt = (
        select(SequenceCounter)
        .where(SequenceCounter.doc_type == doc_type.value, SequenceCounter.year == year)
        .with_for_update()
    )
    counter = db.execute(stmt).scalar_one_or_none()
    if counter is not None:
        return counter
    counter = SequenceCounter(doc_type=doc_type.value, year=year, last_value=0)
    db.add(counter)
    try:
        db.flush()
    except IntegrityError:
        # Параллельная вставка — забираем уже созданную строку с блокировкой.
        db.rollback()
        counter = db.execute(stmt).scalar_one()
    return counter


def next_number(db: Session, doc_type: DocType, year: int) -> str:
    """Выдать следующий номер для типа документа и года (внутри транзакции вызывающего)."""
    counter = _get_or_create_counter(db, doc_type, year)
    counter.last_value += 1
    db.flush()
    return f"{doc_type.prefix}-{year}-{counter.last_value:03d}"
