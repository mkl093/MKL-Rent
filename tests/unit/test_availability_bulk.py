"""Пакетный расчёт доступности совпадает с поштучным (ТЗ §15).

Списки склада, подбора в смету и packing считают доступность одним набором
GROUP BY-запросов. Здесь сверяем его с compute_availability / occupancy_detail /
compute_kit_availability на смешанных данных: брони, отгрузки с окном шире аренды,
просрочка, завершённые/черновики, ремонт/дефект, единицы в комплектах.
"""

from datetime import date
from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy import event

from app.inventory.enums import AccountingType, ItemStatus
from app.inventory.models import EquipmentItem
from app.inventory.schemas import EquipmentModelCreate, KitInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import kits as kit_service
from app.projects.availability import (
    compute_availability,
    compute_availability_bulk,
    compute_kit_availability,
    compute_kit_availability_bulk,
    occupancy_detail,
    occupancy_detail_bulk,
)
from app.projects.enums import ProjectStatus
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project

D = date


@pytest.fixture
def data(db_session):
    cat = cat_service.create_category(db_session, "Свет")

    def model(name):
        return eq_service.create_model(
            db_session,
            EquipmentModelCreate(
                category_id=cat.id,
                name=name,
                accounting_type=AccountingType.SERIAL,
                base_price_eur=Decimal("0"),
            ),
        )

    models = [model(f"M{i}") for i in range(4)]
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    kit2 = kit_service.create_kit(db_session, KitInput(name="Кейс 2"))

    # M0: 6 исправных; M1: ремонт, дефект, дефект «можно использовать», списано,
    # единица в комплекте; M2: 2 единицы; M3: без единиц и без броней.
    for j in range(6):
        db_session.add(EquipmentItem(model_id=models[0].id, barcode=f"A{j}"))
    statuses = [
        (ItemStatus.ACTIVE, False, None),
        (ItemStatus.REPAIR, False, None),
        (ItemStatus.DEFECT, False, None),
        (ItemStatus.DEFECT, True, None),
        (ItemStatus.RETIRED, False, None),
        (ItemStatus.ACTIVE, False, kit.id),
        (ItemStatus.REPAIR, False, kit.id),
    ]
    for j, (st, usable, kit_id) in enumerate(statuses):
        db_session.add(
            EquipmentItem(
                model_id=models[1].id,
                barcode=f"B{j}",
                status=st,
                usable_despite_defect=usable,
                kit_id=kit_id,
            )
        )
    for j in range(2):
        db_session.add(EquipmentItem(model_id=models[2].id, barcode=f"C{j}"))
    db_session.commit()

    def project(status, start, end, shipped=None, returned=None, res=(), kits=()):
        p = create_project(db_session, ProjectInput(name="P", start_date=start, end_date=end))
        p.status = status
        p.shipped_date = shipped
        p.returned_date = returned
        for m, qty in res:
            db_session.add(ProjectReservation(project_id=p.id, model_id=m.id, quantity=qty))
        for k, qty in kits:
            db_session.add(ProjectReservation(project_id=p.id, kit_id=k.id, quantity=qty))
        db_session.commit()
        return p

    m0, m1, m2, _ = models
    projects = [
        project(ProjectStatus.BOOKED, D(2026, 7, 1), D(2026, 7, 5), res=[(m0, 2), (m1, 1)]),
        project(
            ProjectStatus.BOOKED,
            D(2026, 7, 10),
            D(2026, 7, 12),
            shipped=D(2026, 7, 8),
            returned=D(2026, 7, 14),
            res=[(m0, 1)],
            kits=[(kit, 1)],
        ),
        project(ProjectStatus.SHIPPED, D(2026, 7, 3), D(2026, 7, 6), res=[(m0, 3), (m2, 2)]),
        project(
            ProjectStatus.SHIPPED,
            D(2026, 7, 20),
            D(2026, 7, 22),
            shipped=D(2026, 7, 18),
            returned=D(2026, 7, 25),
            res=[(m2, 1)],
            kits=[(kit2, 1)],
        ),
        project(ProjectStatus.COMPLETED, D(2026, 7, 1), D(2026, 7, 30), res=[(m0, 5)]),
        project(ProjectStatus.DRAFT, D(2026, 7, 1), D(2026, 7, 30), res=[(m1, 4)]),
        project(ProjectStatus.CANCELLED, D(2026, 7, 1), D(2026, 7, 30), kits=[(kit, 1)]),
    ]
    return models, [kit, kit2], projects


PERIODS = [
    (D(2026, 7, 2), D(2026, 7, 4)),
    (D(2026, 7, 8), D(2026, 7, 9)),
    (D(2026, 7, 13), D(2026, 7, 14)),
    (D(2026, 7, 24), D(2026, 7, 26)),
    (D(2026, 8, 10), D(2026, 8, 12)),
]
TODAYS = [D(2026, 7, 1), D(2026, 7, 10), D(2026, 8, 1)]


def test_bulk_availability_matches_single(db_session, data):
    models, _, projects = data
    ids = [m.id for m in models]
    excludes = [None, projects[0].id, projects[2].id]
    seen = {"reserved": 0, "in_work": 0, "unavailable": 0}
    for (start, end), today, exclude in product(PERIODS, TODAYS, excludes):
        bulk = compute_availability_bulk(
            db_session, ids, start, end, exclude_project_id=exclude, today=today
        )
        assert set(bulk) == set(ids)
        for m in models:
            single = compute_availability(
                db_session, m, start, end, exclude_project_id=exclude, today=today
            )
            assert bulk[m.id] == single, (m.name, start, end, today, exclude)
            seen["reserved"] += single.reserved
            seen["in_work"] += single.in_work
            seen["unavailable"] += single.unavailable_by_status
    # Данные действительно покрывают все виды занятости, а не одни нули.
    assert all(seen.values()), seen


def test_bulk_stock_matches_stock_quantity(db_session, data):
    models, _, _ = data
    day = D(2026, 7, 1)
    bulk = compute_availability_bulk(db_session, [m.id for m in models], day, day)
    for m in models:
        assert bulk[m.id].total == eq_service.stock_quantity(db_session, m)


def test_bulk_occupancy_matches_single(db_session, data):
    models, _, projects = data
    ids = [m.id for m in models]
    total_rows = 0
    for (start, end), today, exclude in product(PERIODS, TODAYS, [None, projects[2].id]):
        bulk = occupancy_detail_bulk(db_session, ids, start, end, exclude, today=today)
        for m in models:
            single = occupancy_detail(db_session, m.id, start, end, exclude, today=today)
            assert bulk.get(m.id, []) == single, (m.name, start, end, today, exclude)
            total_rows += len(single)
    assert total_rows > 0


def test_bulk_kit_availability_matches_single(db_session, data):
    _, kits, projects = data
    ids = [k.id for k in kits]
    for (start, end), today, exclude in product(PERIODS, TODAYS, [None, projects[1].id]):
        bulk = compute_kit_availability_bulk(
            db_session, ids, start, end, exclude_project_id=exclude, today=today
        )
        for k in kits:
            single = compute_kit_availability(
                db_session, k.id, start, end, exclude_project_id=exclude, today=today
            )
            assert bulk[k.id] == single, (k.name, start, end, today, exclude)


def test_empty_inputs(db_session):
    day = D(2026, 7, 1)
    assert compute_availability_bulk(db_session, [], day, day) == {}
    assert occupancy_detail_bulk(db_session, [], day, day) == {}
    assert compute_kit_availability_bulk(db_session, [], day, day) == {}


def test_bulk_query_count_does_not_grow_with_models(db_session, data, engine):
    models, _, _ = data
    ids = [m.id for m in models]
    count = {"n": 0}

    def _inc(*_args, **_kw):
        count["n"] += 1

    event.listen(engine, "before_cursor_execute", _inc)
    try:
        compute_availability_bulk(db_session, ids, D(2026, 7, 1), D(2026, 7, 5))
        few = count["n"]
        count["n"] = 0
        compute_availability_bulk(db_session, ids + [10_000, 10_001], D(2026, 7, 1), D(2026, 7, 5))
        assert count["n"] == few == 3
    finally:
        event.remove(engine, "before_cursor_execute", _inc)
