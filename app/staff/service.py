"""Бизнес-логика персонала и календаря занятости (ТЗ §54).

Интервалы занятости полуоткрытые [starts_at, ends_at): смежные записи
(18:00–00:00 и 00:00–08:00) пересечением не считаются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.staff.enums import AssignmentStatus, AssignmentType
from app.staff.models import Assignment, Department, Employee
from app.staff.schemas import AssignmentInput, BulkAssignmentInput, DepartmentInput, EmployeeInput


class StaffError(Exception):
    """Базовая ошибка домена персонала."""


class ConflictError(StaffError):
    """Пересечение с уже существующей занятостью сотрудника."""

    def __init__(self, conflicts: list[Assignment]):
        self.conflicts = conflicts
        super().__init__("Обнаружено пересечение занятости")


# --- Отделы ---------------------------------------------------------------


def list_departments(db: Session) -> list[Department]:
    stmt = select(Department).order_by(Department.sort_order, Department.name)
    return list(db.execute(stmt).scalars().all())


def get_department(db: Session, department_id: int) -> Department | None:
    return db.get(Department, department_id)


def group_employees_by_department(
    departments: list[Department], employees: list[Employee]
) -> list[tuple[Department | None, list[Employee]]]:
    """Сгруппировать сотрудников по отделам в порядке отделов (sort_order, name).

    Сотрудники без отдела выносятся отдельной группой (ключ None) в конец —
    список отделов уже отсортирован вызывающей стороной (list_departments).
    """
    by_department: dict[int, list[Employee]] = {}
    unassigned: list[Employee] = []
    for employee in employees:
        if employee.department_id is None:
            unassigned.append(employee)
        else:
            by_department.setdefault(employee.department_id, []).append(employee)

    groups: list[tuple[Department | None, list[Employee]]] = [
        (dept, by_department[dept.id]) for dept in departments if dept.id in by_department
    ]
    if unassigned:
        groups.append((None, unassigned))
    return groups


def create_department(db: Session, data: DepartmentInput) -> Department:
    dept = Department(name=data.name.strip(), sort_order=data.sort_order)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def update_department(db: Session, dept: Department, data: DepartmentInput) -> Department:
    dept.name = data.name.strip()
    dept.sort_order = data.sort_order
    db.commit()
    db.refresh(dept)
    return dept


def delete_department(db: Session, dept: Department) -> None:
    db.delete(dept)
    db.commit()


# --- Сотрудники -------------------------------------------------------------


@dataclass
class EmployeeFilters:
    """Фильтры списка/поиска сотрудников."""

    query: str | None = None
    department_id: int | None = None
    position: str | None = None
    is_active: bool | None = True


def list_employees(db: Session, filters: EmployeeFilters) -> list[Employee]:
    stmt = (
        select(Employee)
        .options(selectinload(Employee.department))
        .order_by(Employee.last_name, Employee.first_name)
    )
    if filters.query:
        like = f"%{filters.query.strip()}%"
        stmt = stmt.where(Employee.first_name.ilike(like) | Employee.last_name.ilike(like))
    if filters.department_id:
        stmt = stmt.where(Employee.department_id == filters.department_id)
    if filters.position:
        stmt = stmt.where(Employee.position == filters.position)
    if filters.is_active is not None:
        stmt = stmt.where(Employee.is_active.is_(filters.is_active))
    return list(db.execute(stmt).scalars().all())


def list_positions(db: Session) -> list[str]:
    """Уникальные должности сотрудников — для выпадающего фильтра."""
    stmt = (
        select(Employee.position)
        .where(Employee.position.is_not(None))
        .distinct()
        .order_by(Employee.position)
    )
    return [p for p in db.execute(stmt).scalars().all() if p]


def get_employee(db: Session, employee_id: int) -> Employee | None:
    return db.get(Employee, employee_id)


def create_employee(db: Session, data: EmployeeInput) -> Employee:
    employee = Employee(**data.model_dump())
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


def update_employee(db: Session, employee: Employee, data: EmployeeInput) -> Employee:
    for field_name, value in data.model_dump().items():
        setattr(employee, field_name, value)
    db.commit()
    db.refresh(employee)
    return employee


def delete_employee(db: Session, employee: Employee) -> None:
    db.delete(employee)
    db.commit()


# --- Занятость ---------------------------------------------------------------


@dataclass
class AssignmentFilters:
    """Фильтры выборки занятости — диапазон обязателен (ТЗ §54: без полной истории)."""

    start: datetime
    end: datetime
    employee_ids: list[int] | None = None
    department_id: int | None = None
    position: str | None = None
    project_id: int | None = None
    types: list[AssignmentType] | None = None
    statuses: list[AssignmentStatus] | None = None


def list_assignments(db: Session, filters: AssignmentFilters) -> list[Assignment]:
    """Записи занятости, пересекающиеся с [filters.start, filters.end)."""
    stmt = (
        select(Assignment)
        .join(Assignment.employee)
        .options(
            selectinload(Assignment.employee).selectinload(Employee.department),
            selectinload(Assignment.project),
        )
        .where(Assignment.starts_at < filters.end)
        .where(Assignment.ends_at > filters.start)
    )
    if filters.employee_ids:
        stmt = stmt.where(Assignment.employee_id.in_(filters.employee_ids))
    if filters.department_id:
        stmt = stmt.where(Employee.department_id == filters.department_id)
    if filters.position:
        stmt = stmt.where(Employee.position == filters.position)
    if filters.project_id:
        stmt = stmt.where(Assignment.project_id == filters.project_id)
    if filters.types:
        stmt = stmt.where(Assignment.type.in_(filters.types))
    if filters.statuses:
        stmt = stmt.where(Assignment.status.in_(filters.statuses))
    stmt = stmt.order_by(Assignment.starts_at)
    return list(db.execute(stmt).scalars().unique().all())


def list_project_assignments(db: Session, project_id: int) -> list[Assignment]:
    """Вся занятость по проекту, кроме отменённой — для карточки проекта.

    В отличие от list_assignments(), диапазон дат не требуется: карточка
    проекта показывает бронь персонала целиком, а не срез по датам.
    """
    stmt = (
        select(Assignment)
        .options(selectinload(Assignment.employee).selectinload(Employee.department))
        .where(Assignment.project_id == project_id)
        .where(Assignment.status != AssignmentStatus.CANCELLED)
        .order_by(Assignment.starts_at)
    )
    return list(db.execute(stmt).scalars().all())


def get_assignment(db: Session, assignment_id: int) -> Assignment | None:
    stmt = (
        select(Assignment)
        .options(selectinload(Assignment.employee), selectinload(Assignment.project))
        .where(Assignment.id == assignment_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def find_conflicts(
    db: Session,
    employee_id: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> list[Assignment]:
    """Пересекающиеся активные (не отменённые) записи того же сотрудника."""
    stmt = (
        select(Assignment)
        .options(selectinload(Assignment.project))
        .where(Assignment.employee_id == employee_id)
        .where(Assignment.starts_at < ends_at)
        .where(Assignment.ends_at > starts_at)
        .where(Assignment.status != AssignmentStatus.CANCELLED)
    )
    if exclude_id is not None:
        stmt = stmt.where(Assignment.id != exclude_id)
    return list(db.execute(stmt).scalars().all())


def create_assignment(
    db: Session,
    data: AssignmentInput,
    created_by_id: int | None = None,
    confirm: bool = False,
) -> Assignment:
    if data.status.blocks:
        conflicts = find_conflicts(db, data.employee_id, data.starts_at, data.ends_at)
        if conflicts and not confirm:
            raise ConflictError(conflicts)
    assignment = Assignment(**data.model_dump(), created_by_id=created_by_id)
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def update_assignment(
    db: Session,
    assignment: Assignment,
    data: AssignmentInput,
    confirm: bool = False,
) -> Assignment:
    if data.status.blocks:
        conflicts = find_conflicts(
            db, data.employee_id, data.starts_at, data.ends_at, exclude_id=assignment.id
        )
        if conflicts and not confirm:
            raise ConflictError(conflicts)
    for field_name, value in data.model_dump().items():
        setattr(assignment, field_name, value)
    db.commit()
    db.refresh(assignment)
    return assignment


def delete_assignment(db: Session, assignment: Assignment) -> None:
    db.delete(assignment)
    db.commit()


@dataclass
class BulkResult:
    created: list[Assignment] = field(default_factory=list)
    conflicts: dict[int, list[Assignment]] = field(default_factory=dict)


def bulk_create_assignments(
    db: Session,
    data: BulkAssignmentInput,
    created_by_id: int | None = None,
    confirm: bool = False,
) -> BulkResult:
    """Назначить одну и ту же занятость нескольким сотрудникам сразу.

    Без confirm — при любом конфликте не создаёт ничего и возвращает список
    конфликтов по каждому затронутому сотруднику, чтобы диспетчер видел
    полную картину и мог подтвердить сохранение целиком.
    """
    conflicts: dict[int, list[Assignment]] = {}
    if data.status.blocks:
        for employee_id in data.employee_ids:
            found = find_conflicts(db, employee_id, data.starts_at, data.ends_at)
            if found:
                conflicts[employee_id] = found
    if conflicts and not confirm:
        return BulkResult(conflicts=conflicts)

    created: list[Assignment] = []
    for employee_id in data.employee_ids:
        assignment = Assignment(
            employee_id=employee_id,
            project_id=data.project_id,
            type=data.type,
            status=data.status,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            title=data.title,
            comment=data.comment,
            created_by_id=created_by_id,
        )
        db.add(assignment)
        created.append(assignment)
    db.commit()
    for assignment in created:
        db.refresh(assignment)
    return BulkResult(created=created)


def busy_employee_ids(db: Session, start: datetime, end: datetime) -> set[int]:
    """ID сотрудников с активной занятостью, пересекающей [start, end)."""
    stmt = (
        select(Assignment.employee_id)
        .where(Assignment.starts_at < end)
        .where(Assignment.ends_at > start)
        .where(Assignment.status != AssignmentStatus.CANCELLED)
        .distinct()
    )
    return set(db.execute(stmt).scalars().all())


def free_employees(
    db: Session, start: datetime, end: datetime, filters: EmployeeFilters | None = None
) -> list[Employee]:
    """Сотрудники без пересекающейся занятости в [start, end)."""
    employees = list_employees(db, filters or EmployeeFilters())
    busy_ids = busy_employee_ids(db, start, end)
    return [e for e in employees if e.id not in busy_ids]
