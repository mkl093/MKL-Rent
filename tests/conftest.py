"""Общие фикстуры тестов.

Unit-тесты — на SQLite in-memory (быстро). Integration-тесты — против реального
PostgreSQL из DATABASE_URL_TEST (пропускаются, если переменная не задана).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Регистрируем модели в Base.metadata.
from app.audit import models as _audit_models  # noqa: F401
from app.auth import models as _auth_models  # noqa: F401
from app.database import Base, get_db
from app.documents import models as _documents_models  # noqa: F401
from app.estimates import models as _estimates_models  # noqa: F401
from app.inventory import models as _inventory_models  # noqa: F401
from app.numbering import models as _numbering_models  # noqa: F401
from app.packing import models as _packing_models  # noqa: F401
from app.projects import models as _projects_models  # noqa: F401
from app.settings import models as _settings_models  # noqa: F401


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture
def db_session(session_factory) -> Iterator[Session]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:
    from app.main import create_app

    app = create_app()

    def override_get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Integration (PostgreSQL) ------------------------------------------


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST не задан — integration-тесты пропущены")
    eng = create_engine(url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()
