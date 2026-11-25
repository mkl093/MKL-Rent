"""Дашборд: «Активные»/«Просроченные» считаются по концу окна занятости (ТЗ §5, §15.1)."""

from datetime import timedelta

from app.dashboard.service import active_booked, overdue_booked
from app.projects.availability import local_today
from app.projects.enums import ProjectStatus
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


def _project(db_session, start, end, returned=None, status=ProjectStatus.SHIPPED):
    project = create_project(
        db_session,
        ProjectInput(name="P", start_date=start, end_date=end, returned_date=returned),
    )
    project.status = status
    db_session.commit()
    return project


def test_planned_return_in_future_is_not_overdue(db_session):
    today = local_today()
    # Аренда кончилась вчера, но плановый возврат через 2 дня — ещё не просрочка.
    p = _project(
        db_session, today - timedelta(days=5), today - timedelta(days=1), today + timedelta(days=2)
    )
    assert p.is_overdue_booked is False
    assert p in active_booked(db_session)
    assert p not in overdue_booked(db_session)


def test_past_planned_return_is_overdue(db_session):
    today = local_today()
    p = _project(
        db_session, today - timedelta(days=5), today - timedelta(days=3), today - timedelta(days=1)
    )
    assert p.is_overdue_booked is True
    assert p in overdue_booked(db_session)
    assert p not in active_booked(db_session)


def test_without_return_date_uses_rental_end(db_session):
    today = local_today()
    p = _project(db_session, today - timedelta(days=5), today - timedelta(days=1))
    assert p.is_overdue_booked is True
    assert p in overdue_booked(db_session)
