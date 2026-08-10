"""Логика календаря занятости персонала: пересечения, выборка по диапазону,
свободные сотрудники, массовое назначение (ТЗ §54)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.staff import service
from app.staff.enums import AssignmentStatus, AssignmentType
from app.staff.schemas import AssignmentInput, BulkAssignmentInput, DepartmentInput, EmployeeInput


def _dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


@pytest.fixture
def employee(db_session):
    dept = service.create_department(db_session, DepartmentInput(name="Монтаж"))
    return service.create_employee(
        db_session,
        EmployeeInput(
            first_name="Иван", last_name="Иванов", position="Монтажник", department_id=dept.id
        ),
    )


@pytest.fixture
def other_employee(db_session):
    return service.create_employee(
        db_session, EmployeeInput(first_name="Пётр", last_name="Петров", position="Водитель")
    )


def _input(employee_id, start, end, **kw):
    kw.setdefault("type", AssignmentType.BUSY)
    return AssignmentInput(employee_id=employee_id, starts_at=start, ends_at=end, **kw)


def _slot(employee_id, h1, h2, day=10, **kw):
    """Занятость в один и тот же августовский день — сокращает часто повторяющийся вызов."""
    return _input(employee_id, _dt(2026, 8, day, h1), _dt(2026, 8, day, h2), **kw)


def _create_slot(db_session, employee_id, h1, h2, day=10, **kw):
    return service.create_assignment(db_session, _slot(employee_id, h1, h2, day, **kw))


# --- Пересечения -----------------------------------------------------------


def test_overlapping_assignment_raises_conflict(db_session, employee):
    _create_slot(db_session, employee.id, 10, 18)
    with pytest.raises(service.ConflictError) as exc:
        _create_slot(db_session, employee.id, 14, 20)
    assert len(exc.value.conflicts) == 1


def test_overlap_confirmed_saves_anyway(db_session, employee):
    _create_slot(db_session, employee.id, 10, 18)
    created = service.create_assignment(db_session, _slot(employee.id, 14, 20), confirm=True)
    assert created.id is not None


def test_adjacent_intervals_do_not_conflict(db_session, employee):
    """Полуоткрытые интервалы: 08:00–18:00 и 18:00–08:00 следующего дня не пересекаются."""
    _create_slot(db_session, employee.id, 8, 18)
    created = service.create_assignment(
        db_session, _input(employee.id, _dt(2026, 8, 10, 18), _dt(2026, 8, 11, 8))
    )
    assert created.id is not None


def test_cancelled_assignment_ignored_in_conflicts(db_session, employee):
    _create_slot(db_session, employee.id, 10, 18, status=AssignmentStatus.CANCELLED)
    created = _create_slot(db_session, employee.id, 12, 16)
    assert created.id is not None


def test_different_employees_do_not_conflict(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 10, 18)
    created = _create_slot(db_session, other_employee.id, 10, 18)
    assert created.id is not None


def test_update_excludes_own_record_from_conflict_check(db_session, employee):
    a = _create_slot(db_session, employee.id, 10, 18)
    updated = service.update_assignment(db_session, a, _slot(employee.id, 11, 19))
    # SQLite не сохраняет tzinfo в DateTime(timezone=True) — naive считается UTC (см. to_local()).
    assert updated.starts_at.replace(tzinfo=UTC) == _dt(2026, 8, 10, 11)


def test_update_still_detects_conflict_with_other_record(db_session, employee):
    _create_slot(db_session, employee.id, 10, 12)
    b = _create_slot(db_session, employee.id, 10, 12, day=12)
    with pytest.raises(service.ConflictError):
        service.update_assignment(db_session, b, _slot(employee.id, 11, 13))


# --- Выборка по диапазону ----------------------------------------------------


def test_list_assignments_only_within_range(db_session, employee):
    _create_slot(db_session, employee.id, 10, 18, day=1)
    in_range = _create_slot(db_session, employee.id, 10, 18)
    filters = service.AssignmentFilters(start=_dt(2026, 8, 5), end=_dt(2026, 8, 15))
    result = service.list_assignments(db_session, filters)
    assert [a.id for a in result] == [in_range.id]


def test_list_assignments_includes_multi_day_spanning_window(db_session, employee):
    """Многодневная запись (отпуск) видна, даже если начинается до окна запроса."""
    vacation = service.create_assignment(
        db_session,
        _input(employee.id, _dt(2026, 7, 20), _dt(2026, 8, 20), type=AssignmentType.VACATION),
    )
    filters = service.AssignmentFilters(start=_dt(2026, 8, 1), end=_dt(2026, 8, 7))
    result = service.list_assignments(db_session, filters)
    assert [a.id for a in result] == [vacation.id]


def test_list_assignments_filters_by_department(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 10, 18)
    _create_slot(db_session, other_employee.id, 10, 18)
    dept_id = employee.department_id
    filters = service.AssignmentFilters(
        start=_dt(2026, 8, 1), end=_dt(2026, 8, 20), department_id=dept_id
    )
    result = service.list_assignments(db_session, filters)
    assert {a.employee_id for a in result} == {employee.id}


# --- Свободные сотрудники ----------------------------------------------------


def test_free_employees_excludes_busy(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 10, 18)
    free = service.free_employees(db_session, _dt(2026, 8, 10, 12), _dt(2026, 8, 10, 14))
    assert {e.id for e in free} == {other_employee.id}


def test_free_employees_ignores_cancelled(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 10, 18, status=AssignmentStatus.CANCELLED)
    free = service.free_employees(db_session, _dt(2026, 8, 10, 12), _dt(2026, 8, 10, 14))
    assert {e.id for e in free} == {employee.id, other_employee.id}


# --- Массовое назначение -----------------------------------------------------


def test_bulk_create_assigns_all_employees(db_session, employee, other_employee):
    data = BulkAssignmentInput(
        employee_ids=[employee.id, other_employee.id],
        type=AssignmentType.PROJECT,
        starts_at=_dt(2026, 8, 10, 8),
        ends_at=_dt(2026, 8, 10, 18),
    )
    result = service.bulk_create_assignments(db_session, data)
    assert {a.employee_id for a in result.created} == {employee.id, other_employee.id}
    assert result.conflicts == {}


def test_bulk_create_partial_conflict_without_confirm(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 8, 18)
    data = BulkAssignmentInput(
        employee_ids=[employee.id, other_employee.id],
        type=AssignmentType.PROJECT,
        starts_at=_dt(2026, 8, 10, 10),
        ends_at=_dt(2026, 8, 10, 16),
    )
    result = service.bulk_create_assignments(db_session, data)
    assert result.created == []
    assert set(result.conflicts.keys()) == {employee.id}


def test_bulk_create_confirmed_saves_despite_conflict(db_session, employee, other_employee):
    _create_slot(db_session, employee.id, 8, 18)
    data = BulkAssignmentInput(
        employee_ids=[employee.id, other_employee.id],
        type=AssignmentType.PROJECT,
        starts_at=_dt(2026, 8, 10, 10),
        ends_at=_dt(2026, 8, 10, 16),
    )
    result = service.bulk_create_assignments(db_session, data, confirm=True)
    assert {a.employee_id for a in result.created} == {employee.id, other_employee.id}
