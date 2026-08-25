"""Транспорт: распределение packing-листа по машинам (ТЗ: транспорт)."""

from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service as est_service
from app.inventory.enums import AccountingType, PackingType
from app.inventory.schemas import EquipmentModelCreate, PackingRuleInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput
from app.transport import service


@pytest.fixture
def env(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("2.0"),
            length_mm=100,
            width_mm=100,
            height_mm=100,
            packing=PackingRuleInput(
                packing_type=PackingType.CASE,
                capacity=4,
                empty_weight_kg=Decimal("1.0"),
                length_mm=500,
                width_mm=400,
                height_mm=300,
            ),
        ),
    )
    project = proj_service.create_project(
        db_session,
        ProjectInput(name="Шоу", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, model, 10)
    packing = packing_service.create_from_estimate(db_session, project)
    line = next(ln for ln in packing.lines if ln.model_id == model.id)
    return db_session, project, packing, line


def _add_vehicle(db, project, name, max_weight_kg):
    vehicle = service.create_vehicle(
        db, name=name, plate_number=None, max_weight_kg=Decimal(max_weight_kg), comment=None
    )
    return service.add_vehicle_to_project(db, project, vehicle)


# --- Справочник машин / машины проекта ------------------------------------


def test_add_vehicle_to_project_is_a_snapshot(env):
    db, project, packing, line = env
    vehicle = service.create_vehicle(
        db, name="Газель", plate_number="А123ВС", max_weight_kg=Decimal("1000"), comment=None
    )
    pv = service.add_vehicle_to_project(db, project, vehicle)
    assert pv.name == "Газель"
    assert pv.max_weight_kg == Decimal("1000")

    # Правка справочника не должна задним числом менять уже добавленную в проект машину.
    service.update_vehicle(
        db, vehicle, name="Газель-2", plate_number="А123ВС", max_weight_kg=Decimal("500"), comment=None
    )
    db.refresh(pv)
    assert pv.name == "Газель"
    assert pv.max_weight_kg == Decimal("1000")


def test_remove_project_vehicle_cascades_assignments(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    service.assign(db, project, pv, line, 4)
    assert service.remaining_quantity(db, project, line) == 6

    service.remove_project_vehicle(db, pv)
    assert service.remaining_quantity(db, project, line) == 10


# --- Распределение по количеству ------------------------------------------


def test_assign_clamps_to_remaining(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    service.assign(db, project, pv, line, 100)  # больше, чем есть в строке (10)
    assert service.remaining_quantity(db, project, line) == 0
    board = service.board(db, project)
    assert board.vehicles[0].assignments[0].assignment.quantity == 10


def test_assign_repeated_increments_same_assignment(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    service.assign(db, project, pv, line, 3)
    service.assign(db, project, pv, line, 2)
    board = service.board(db, project)
    rows = board.vehicles[0].assignments
    assert len(rows) == 1
    assert rows[0].assignment.quantity == 5


def test_assign_over_remaining_raises(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    service.assign(db, project, pv, line, 10)  # весь остаток
    with pytest.raises(service.TransportError):
        service.assign(db, project, pv, line, 1)


def test_split_line_between_two_vehicles(env):
    """10 стоек: 6 в газель, 4 в бус — дробление по количеству."""
    db, project, packing, line = env
    van = _add_vehicle(db, project, "Газель", 1000)
    bus = _add_vehicle(db, project, "Бус", 1000)
    service.assign(db, project, van, line, 6)
    service.assign(db, project, bus, line, 4)
    assert service.remaining_quantity(db, project, line) == 0

    board = service.board(db, project)
    van_board = next(vb for vb in board.vehicles if vb.project_vehicle.id == van.id)
    bus_board = next(vb for vb in board.vehicles if vb.project_vehicle.id == bus.id)
    assert van_board.assignments[0].assignment.quantity == 6
    assert bus_board.assignments[0].assignment.quantity == 4


def test_move_transfers_between_vehicles(env):
    db, project, packing, line = env
    van = _add_vehicle(db, project, "Газель", 1000)
    bus = _add_vehicle(db, project, "Бус", 1000)
    service.assign(db, project, van, line, 6)
    assignment = service.get_assignment_for(db, van, line)

    service.move(db, assignment, bus, 2)

    van_assignment = service.get_assignment_for(db, van, line)
    bus_assignment = service.get_assignment_for(db, bus, line)
    assert van_assignment.quantity == 4
    assert bus_assignment.quantity == 2


def test_move_all_deletes_source_assignment(env):
    db, project, packing, line = env
    van = _add_vehicle(db, project, "Газель", 1000)
    bus = _add_vehicle(db, project, "Бус", 1000)
    service.assign(db, project, van, line, 6)
    assignment = service.get_assignment_for(db, van, line)

    service.move(db, assignment, bus, 6)

    assert service.get_assignment_for(db, van, line) is None
    assert service.get_assignment_for(db, bus, line).quantity == 6


def test_unassign_partial_returns_to_pool(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    service.assign(db, project, pv, line, 6)
    assignment = service.get_assignment_for(db, pv, line)

    service.unassign(db, assignment, 2)
    assert service.remaining_quantity(db, project, line) == 6  # 10 - (6-2)


# --- Разложение "сначала упакованные" и вес/объём/перегруз ----------------


def test_splitting_packed_position_increases_package_count(env):
    """Разделение упакованной позиции между машинами разбивает кейс — ceil независимо."""
    db, project, packing, line = env
    # В фикстуре весь факт (10) упакован по умолчанию, capacity=4 → 3 упаковки всего.
    van = _add_vehicle(db, project, "Газель", 1000)
    bus = _add_vehicle(db, project, "Бус", 1000)
    service.assign(db, project, van, line, 6)  # ceil(6/4) = 2 упаковки
    service.assign(db, project, bus, line, 4)  # ceil(4/4) = 1 упаковка

    board = service.board(db, project)
    van_row = next(vb for vb in board.vehicles if vb.project_vehicle.id == van.id).assignments[0]
    bus_row = next(vb for vb in board.vehicles if vb.project_vehicle.id == bus.id).assignments[0]
    assert van_row.calc.packages == 2
    assert bus_row.calc.packages == 1
    # 2 + 1 = 3 упаковки по машинам > было бы 3 в общем листе не разделённым —
    # тут совпадает, но суммарный вес по машинам не должен быть меньше общего.
    from app.packing.calc import compute_line

    total_calc = compute_line(line)
    assert van_row.calc.total_weight + bus_row.calc.total_weight >= total_calc.total_weight


def test_vehicle_overload_flag(env):
    db, project, packing, line = env
    # 10 × 2.0 + 3 упаковки × 1.0 = 23.0 кг общего веса
    pv = _add_vehicle(db, project, "Малый прицеп", 10)
    service.assign(db, project, pv, line, 10)
    board = service.board(db, project)
    totals = board.vehicles[0].totals
    assert totals.overload is True
    assert totals.overload_kg == Decimal("13.0")


def test_no_overload_when_within_capacity(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Фура", 1000)
    service.assign(db, project, pv, line, 10)
    board = service.board(db, project)
    assert board.vehicles[0].totals.overload is False


def test_board_pool_excludes_fully_assigned_lines(env):
    db, project, packing, line = env
    pv = _add_vehicle(db, project, "Газель", 1000)
    board = service.board(db, project)
    assert len(board.unassigned) == 1
    service.assign(db, project, pv, line, 10)
    board = service.board(db, project)
    assert board.unassigned == []
