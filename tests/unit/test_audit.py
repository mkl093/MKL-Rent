"""Журнал действий (ТЗ §29)."""

from app.audit.events import EventType
from app.audit.service import list_entries, log
from app.auth.service import create_user


def test_log_creates_entry(db_session):
    user = create_user(db_session, "admin", "pass123")
    log(
        db_session,
        user,
        EventType.PROJECT_CREATE,
        "Создан проект PRJ-2026-001",
        object_type="project",
        object_id=7,
    )
    entries = list_entries(db_session)
    assert len(entries) == 1
    e = entries[0]
    assert e.event_type == "project_create"
    assert e.user_name == "admin"
    assert e.object_type == "project" and e.object_id == 7
    assert "PRJ-2026-001" in e.description


def test_log_old_new_values(db_session):
    log(
        db_session,
        None,
        EventType.PROJECT_STATUS,
        "смена",
        old_value="Черновик",
        new_value="Забронирован",
    )
    e = list_entries(db_session)[0]
    assert e.user_name == "система"
    assert e.old_value == "Черновик" and e.new_value == "Забронирован"


def test_list_filter_by_event_type(db_session):
    user = create_user(db_session, "u", "p")
    log(db_session, user, EventType.PROJECT_CREATE, "a")
    log(db_session, user, EventType.DOCUMENT_GENERATE, "b")
    assert len(list_entries(db_session)) == 2
    assert len(list_entries(db_session, event_type="project_create")) == 1
    assert len(list_entries(db_session, event_type="user_manage")) == 0
