"""Смета: формула строки, итоги, merge, бронь, копирование (ТЗ §16, §15)."""

from datetime import date
from decimal import Decimal

import pytest

from app.estimates import service
from app.estimates.schemas import CustomLineInput, LineUpdate
from app.estimates.totals import compute_totals
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


@pytest.fixture
def setup(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=20,
            base_price_eur=Decimal("100.00"),
        ),
    )
    project = create_project(
        db_session,
        ProjectInput(
            name="Концерт",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            rental_coefficient=Decimal("1.5"),
            vat=Decimal("19"),
        ),
    )
    estimate = service.get_or_create_estimate(db_session, project)
    return project, estimate, model


def test_estimate_number_assigned(setup):
    _, estimate, _ = setup
    assert estimate.number.startswith("EST-")


def test_add_model_snapshots_price_and_coefficient(db_session, setup):
    project, estimate, model = setup
    line = service.add_model(db_session, estimate, project, model, 2)
    assert line.unit_price == Decimal("100.00")
    assert line.coefficient == Decimal("1.5")  # из коэффициента проекта
    # итог = 100 × 2 × 1.5 = 300
    assert line.line_total == Decimal("300.00")


def test_merge_vs_separate(db_session, setup):
    project, estimate, model = setup
    service.add_model(db_session, estimate, project, model, 2, merge=True)
    service.add_model(db_session, estimate, project, model, 3, merge=True)
    assert len(estimate.lines) == 1
    assert estimate.lines[0].quantity == 5

    service.add_model(db_session, estimate, project, model, 1, merge=False)
    assert len(estimate.lines) == 2


def test_reservation_synced_from_warehouse_lines(db_session, setup):
    project, estimate, model = setup
    service.add_model(db_session, estimate, project, model, 4)
    res = db_session.query(ProjectReservation).filter_by(project_id=project.id).all()
    assert len(res) == 1 and res[0].quantity == 4

    # увеличиваем количество строки → бронь обновляется
    service.update_line(
        db_session,
        estimate.lines[0],
        LineUpdate(quantity=6, unit_price=Decimal("100"), coefficient=Decimal("1")),
    )
    service.sync_reservations(db_session, project, estimate)
    res = db_session.query(ProjectReservation).filter_by(project_id=project.id).all()
    assert res[0].quantity == 6


def test_custom_line_excluded_from_reservation(db_session, setup):
    project, estimate, model = setup
    service.add_custom_line(
        db_session,
        estimate,
        project,
        CustomLineInput(
            name="Транспорт", quantity=1, unit_price=Decimal("250"), coefficient=Decimal("1")
        ),
    )
    res = db_session.query(ProjectReservation).filter_by(project_id=project.id).all()
    assert res == []
    assert estimate.lines[0].is_custom


def test_totals_with_discount_and_vat():
    # подытог 1000, скидка 10% → 900, VAT 19% → 171, итог 1071
    t = compute_totals([Decimal("600"), Decimal("400")], Decimal("10"), Decimal("19"))
    assert t.subtotal == Decimal("1000.00")
    assert t.discount_amount == Decimal("100.00")
    assert t.after_discount == Decimal("900.00")
    assert t.vat_amount == Decimal("171.00")
    assert t.total == Decimal("1071.00")
    assert t.has_discount


def test_totals_zero_discount_hidden():
    t = compute_totals([Decimal("100")], Decimal("0"), Decimal("0"))
    assert not t.has_discount
    assert t.total == Decimal("100.00")


def test_grouped_lines_custom_last(db_session, setup):
    project, estimate, model = setup
    service.add_model(db_session, estimate, project, model, 1)
    service.add_custom_line(
        db_session, estimate, project, CustomLineInput(name="Доставка", unit_price=Decimal("50"))
    )
    groups = service.grouped_lines(estimate)
    assert groups[-1].category_name == "Прочее"


def test_copy_estimate(db_session, setup):
    project, estimate, model = setup
    service.add_model(db_session, estimate, project, model, 2)
    service.set_discount(db_session, estimate, Decimal("5"))

    target = create_project(db_session, ProjectInput(name="Копия"))
    copied = service.copy_estimate(db_session, project, target)
    assert copied.discount_percent == Decimal("5")
    assert len(copied.lines) == 1
    # бронь скопированной сметы тоже синхронизирована
    res = db_session.query(ProjectReservation).filter_by(project_id=target.id).all()
    assert res[0].quantity == 2
