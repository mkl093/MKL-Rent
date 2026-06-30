"""Integration-тесты против реального PostgreSQL (ТЗ §38).

Проверяют то, что нельзя проверить на SQLite: уникальный констрейнт на уровне БД.
Пропускаются, если не задан DATABASE_URL_TEST.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth import service

pytestmark = pytest.mark.integration


def test_username_unique_constraint(pg_engine):
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        service.create_user(db, "admin", "pass123")
        with pytest.raises(IntegrityError):
            service.create_user(db, "admin", "other")
        db.rollback()
    finally:
        db.close()
