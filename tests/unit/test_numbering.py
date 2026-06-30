"""Нумерация документов (ТЗ §14)."""

from app.numbering.models import DocType
from app.numbering.service import next_number


def test_format_and_increment(db_session):
    assert next_number(db_session, DocType.PROJECT, 2026) == "PRJ-2026-001"
    assert next_number(db_session, DocType.PROJECT, 2026) == "PRJ-2026-002"
    db_session.commit()


def test_counters_are_independent(db_session):
    assert next_number(db_session, DocType.PROJECT, 2026) == "PRJ-2026-001"
    assert next_number(db_session, DocType.ESTIMATE, 2026) == "EST-2026-001"
    assert next_number(db_session, DocType.PACKING, 2026) == "PL-2026-001"
    db_session.commit()


def test_counter_resets_per_year(db_session):
    assert next_number(db_session, DocType.PROJECT, 2026) == "PRJ-2026-001"
    assert next_number(db_session, DocType.PROJECT, 2027) == "PRJ-2027-001"
    assert next_number(db_session, DocType.PROJECT, 2026) == "PRJ-2026-002"
    db_session.commit()
