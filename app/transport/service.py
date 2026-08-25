"""Бизнес-логика транспорта: справочник машин и распределение по проекту (ТЗ: транспорт)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.packing import service as packing_service
from app.packing.calc import LineCalc
from app.packing.models import PackingLine
from app.projects.models import Project
from app.transport.calc import VehicleTotals, allocate, vehicle_totals
from app.transport.models import ProjectVehicle, TransportAssignment, Vehicle


class TransportError(Exception):
    """Ошибка домена транспорта."""


# --- Справочник машин ------------------------------------------------------


def list_vehicles(db: Session, *, include_archived: bool = False) -> list[Vehicle]:
    stmt = select(Vehicle).order_by(Vehicle.name)
    if not include_archived:
        stmt = stmt.where(Vehicle.is_archived.is_(False))
    return list(db.execute(stmt).scalars().all())


def get_vehicle(db: Session, vehicle_id: int) -> Vehicle | None:
    return db.get(Vehicle, vehicle_id)


def create_vehicle(
    db: Session,
    *,
    name: str,
    plate_number: str | None,
    max_weight_kg: Decimal,
    comment: str | None,
) -> Vehicle:
    vehicle = Vehicle(
        name=name.strip(),
        plate_number=(plate_number.strip() if plate_number else None),
        max_weight_kg=max(Decimal("0"), max_weight_kg),
        comment=(comment.strip() if comment else None),
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def update_vehicle(
    db: Session,
    vehicle: Vehicle,
    *,
    name: str,
    plate_number: str | None,
    max_weight_kg: Decimal,
    comment: str | None,
) -> None:
    vehicle.name = name.strip()
    vehicle.plate_number = plate_number.strip() if plate_number else None
    vehicle.max_weight_kg = max(Decimal("0"), max_weight_kg)
    vehicle.comment = comment.strip() if comment else None
    db.commit()


def archive_vehicle(db: Session, vehicle: Vehicle) -> None:
    vehicle.is_archived = True
    db.commit()


def unarchive_vehicle(db: Session, vehicle: Vehicle) -> None:
    vehicle.is_archived = False
    db.commit()


# --- Машины проекта ----------------------------------------------------------


def list_project_vehicles(db: Session, project: Project) -> list[ProjectVehicle]:
    # populate_existing() — без него selectinload не обновит .assignments у машины,
    # если та коллекция уже была загружена (например, пустой) ранее в этой же сессии:
    # assign()/move() создают TransportAssignment напрямую, а не через .assignments.append(),
    # поэтому уже закэшированная пустая коллекция иначе осталась бы устаревшей.
    stmt = (
        select(ProjectVehicle)
        .options(selectinload(ProjectVehicle.assignments))
        .where(ProjectVehicle.project_id == project.id)
        .order_by(ProjectVehicle.sort_order, ProjectVehicle.id)
        .execution_options(populate_existing=True)
    )
    return list(db.execute(stmt).scalars().all())


def get_project_vehicle(db: Session, project: Project, pv_id: int) -> ProjectVehicle | None:
    pv = db.get(ProjectVehicle, pv_id)
    if pv is None or pv.project_id != project.id:
        return None
    return pv


def add_vehicle_to_project(db: Session, project: Project, vehicle: Vehicle) -> ProjectVehicle:
    """Добавить машину в проект — снимок справочника на момент добавления."""
    existing = list_project_vehicles(db, project)
    sort_order = max((pv.sort_order for pv in existing), default=0) + 1
    pv = ProjectVehicle(
        project_id=project.id,
        vehicle_id=vehicle.id,
        name=vehicle.name,
        plate_number=vehicle.plate_number,
        max_weight_kg=vehicle.max_weight_kg,
        sort_order=sort_order,
    )
    db.add(pv)
    db.commit()
    db.refresh(pv)
    return pv


def remove_project_vehicle(db: Session, project_vehicle: ProjectVehicle) -> None:
    """Убрать машину из проекта — все её назначения снимаются (cascade)."""
    db.delete(project_vehicle)
    db.commit()


# --- Распределение оборудования ---------------------------------------------


def get_line(db: Session, project: Project, line_id: int) -> PackingLine | None:
    """Строка packing-листа этого проекта (защита от чужого/произвольного line_id)."""
    packing = packing_service.get_packing(db, project)
    if packing is None:
        return None
    return next((ln for ln in packing.lines if ln.id == line_id), None)


def _project_assignments(db: Session, project: Project) -> list[TransportAssignment]:
    stmt = (
        select(TransportAssignment)
        .join(ProjectVehicle, TransportAssignment.project_vehicle_id == ProjectVehicle.id)
        .where(ProjectVehicle.project_id == project.id)
    )
    return list(db.execute(stmt).scalars().all())


def remaining_quantity(db: Session, project: Project, line: PackingLine) -> int:
    """Сколько единиц строки ещё не распределено ни по одной машине проекта."""
    assigned = sum(
        a.quantity for a in _project_assignments(db, project) if a.packing_line_id == line.id
    )
    return max(0, line.fact_quantity - assigned)


def assign(
    db: Session, project: Project, project_vehicle: ProjectVehicle, line: PackingLine, quantity: int
) -> TransportAssignment:
    """Назначить количество строки в машину — зажимается остатком нераспределённого.

    Повторное назначение той же строки в ту же машину увеличивает quantity,
    а не создаёт вторую запись (уникальность (машина, строка)).
    """
    remaining = remaining_quantity(db, project, line)
    qty = max(0, min(quantity, remaining))
    if qty <= 0:
        raise TransportError("Нет нераспределённого количества для назначения")
    existing = get_assignment_for(db, project_vehicle, line)
    if existing is not None:
        existing.quantity += qty
        db.commit()
        db.refresh(existing)
        return existing
    assignment = TransportAssignment(
        project_vehicle_id=project_vehicle.id, packing_line_id=line.id, quantity=qty
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def get_assignment(db: Session, project: Project, assignment_id: int) -> TransportAssignment | None:
    assignment = db.get(TransportAssignment, assignment_id)
    if assignment is None or assignment.project_vehicle.project_id != project.id:
        return None
    return assignment


def get_assignment_for(
    db: Session, project_vehicle: ProjectVehicle, line: PackingLine
) -> TransportAssignment | None:
    return db.execute(
        select(TransportAssignment).where(
            TransportAssignment.project_vehicle_id == project_vehicle.id,
            TransportAssignment.packing_line_id == line.id,
        )
    ).scalar_one_or_none()


def unassign(db: Session, assignment: TransportAssignment, quantity: int | None = None) -> None:
    """Вернуть количество из назначения обратно в пул. quantity=None — снять целиком."""
    if quantity is None or quantity >= assignment.quantity:
        db.delete(assignment)
    else:
        assignment.quantity -= quantity
    db.commit()


def move(db: Session, assignment: TransportAssignment, target: ProjectVehicle, quantity: int) -> None:
    """Переместить часть назначения в другую машину той же строки (drag-and-drop)."""
    qty = max(0, min(quantity, assignment.quantity))
    if qty <= 0 or assignment.project_vehicle_id == target.id:
        return
    line = assignment.line
    unassign(db, assignment, qty)
    existing = get_assignment_for(db, target, line)
    if existing is not None:
        existing.quantity += qty
    else:
        db.add(TransportAssignment(project_vehicle_id=target.id, packing_line_id=line.id, quantity=qty))
    db.commit()


# --- Доска распределения (агрегат для UI и PDF) ------------------------------


@dataclass
class PoolItem:
    line: PackingLine
    remaining: int


@dataclass
class VehicleAssignmentRow:
    assignment: TransportAssignment
    line: PackingLine
    calc: LineCalc


@dataclass
class VehicleBoard:
    project_vehicle: ProjectVehicle
    assignments: list[VehicleAssignmentRow]
    totals: VehicleTotals


@dataclass
class Board:
    unassigned: list[PoolItem]
    vehicles: list[VehicleBoard]


def board(db: Session, project: Project) -> Board:
    """Собрать доску распределения: пул нераспределённого + машины с итогами."""
    packing = packing_service.get_packing(db, project)
    if packing is None:
        return Board(unassigned=[], vehicles=[])

    lines_by_id = {ln.id: ln for ln in packing.lines}
    vehicles = list_project_vehicles(db, project)
    assignments = _project_assignments(db, project)
    pv_order = {pv.id: pv.sort_order for pv in vehicles}

    assignments_by_line: dict[int, list[TransportAssignment]] = {}
    for a in assignments:
        assignments_by_line.setdefault(a.packing_line_id, []).append(a)
    for line_assignments in assignments_by_line.values():
        line_assignments.sort(key=lambda a: (pv_order.get(a.project_vehicle_id, 0), a.id))

    calc_by_assignment: dict[int, LineCalc] = {}
    for line_id, line_assignments in assignments_by_line.items():
        line = lines_by_id.get(line_id)
        if line is not None:
            calc_by_assignment.update(allocate(line, line_assignments))

    vehicle_boards: list[VehicleBoard] = []
    for pv in vehicles:
        rows = [
            VehicleAssignmentRow(
                assignment=a, line=lines_by_id[a.packing_line_id], calc=calc_by_assignment[a.id]
            )
            for a in pv.assignments
            if a.packing_line_id in lines_by_id
        ]
        vehicle_boards.append(
            VehicleBoard(project_vehicle=pv, assignments=rows, totals=vehicle_totals(pv, [r.calc for r in rows]))
        )

    assigned_total: dict[int, int] = {}
    for a in assignments:
        assigned_total[a.packing_line_id] = assigned_total.get(a.packing_line_id, 0) + a.quantity

    ordered_lines = sorted(
        packing.lines,
        key=lambda ln: (
            ln.is_custom,
            ln.category_name or "",
            ln.subcategory_name or "",
            ln.sort_order,
            ln.id,
        ),
    )
    pool = [
        PoolItem(line=ln, remaining=ln.fact_quantity - assigned_total.get(ln.id, 0))
        for ln in ordered_lines
        if ln.fact_quantity - assigned_total.get(ln.id, 0) > 0
    ]

    return Board(unassigned=pool, vehicles=vehicle_boards)
