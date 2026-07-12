"""Проекты: статусы, бронирование, копирование, удаление (ТЗ §13–§15)."""

from datetime import date
from decimal import Decimal

import pytest

from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects import service
from app.projects.enums import ProjectStatus
from app.projects.models import Project, ProjectReservation
from app.projects.schemas import ProjectInput


def _model(db_session, qty=5):
    cat = cat_service.create_category(db_session, "Звук")
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=qty,
        ),
    )


def test_deficits_skips_kit_reservation_without_warning(db_session):
    """Бронь комплекта (model_id пуст) не должна вызывать SAWarning о NULL-PK."""
    import warnings

    from sqlalchemy.exc import SAWarning

    from app.inventory.schemas import KitInput
    from app.inventory.services import kits as kit_service

    project = service.create_project(
        db_session,
        ProjectInput(
            name="С комплектом", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)
        ),
    )
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс"))
    db_session.add(ProjectReservation(project_id=project.id, kit_id=kit.id, quantity=1))
    db_session.commit()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        assert service.project_deficits(db_session, project) == []


def test_create_assigns_number_and_draft(db_session):
    p = service.create_project(db_session, ProjectInput(name="Концерт"))
    assert p.number.startswith("PRJ-")
    assert p.status == ProjectStatus.DRAFT


def test_book_requires_dates(db_session):
    p = service.create_project(db_session, ProjectInput(name="Без дат"))
    with pytest.raises(service.ValidationError):
        service.book_project(db_session, p)


def test_book_rejects_inverted_dates(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 10), end_date=date(2026, 7, 1)),
    )
    with pytest.raises(service.ValidationError):
        service.book_project(db_session, p)


def test_book_success_without_reservations(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    service.book_project(db_session, p)
    assert p.status == ProjectStatus.BOOKED


def test_book_deficit_requires_confirmation(db_session):
    model = _model(db_session, qty=3)
    # сосед бронирует 3 на пересекающийся период
    neighbor = service.create_project(
        db_session,
        ProjectInput(name="Сосед", start_date=date(2026, 7, 1), end_date=date(2026, 7, 9)),
    )
    neighbor.status = ProjectStatus.BOOKED
    db_session.add(ProjectReservation(project_id=neighbor.id, model_id=model.id, quantity=3))
    db_session.commit()

    p = service.create_project(
        db_session,
        ProjectInput(name="Наш", start_date=date(2026, 7, 5), end_date=date(2026, 7, 12)),
    )
    db_session.add(ProjectReservation(project_id=p.id, model_id=model.id, quantity=2))
    db_session.commit()
    db_session.refresh(p)

    with pytest.raises(service.DeficitError) as exc:
        service.book_project(db_session, p)
    assert exc.value.lines[0].deficit == 2

    service.book_project(db_session, p, allow_deficit=True)
    assert p.status == ProjectStatus.BOOKED


def test_copy_resets_dates_status_number(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(
            name="Оригинал",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            customer="ООО Ромашка",
            vat=Decimal("19"),
        ),
    )
    p.status = ProjectStatus.BOOKED
    db_session.commit()

    copy = service.copy_project(db_session, p)
    assert copy.number != p.number
    assert copy.status == ProjectStatus.DRAFT
    assert copy.start_date is None and copy.end_date is None
    assert copy.name == "Оригинал (копия)"
    assert copy.customer == "ООО Ромашка"
    assert copy.vat == Decimal("19")


def test_delete_only_draft(db_session):
    p = service.create_project(
        db_session,
        ProjectInput(name="X", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)),
    )
    service.book_project(db_session, p)
    with pytest.raises(service.ValidationError):
        service.delete_project(db_session, p)
    service.set_status(db_session, p, ProjectStatus.DRAFT)
    service.delete_project(db_session, p)
    assert db_session.get(Project, p.id) is None


def test_list_active_vs_archived(db_session):
    a = service.create_project(db_session, ProjectInput(name="Активный"))
    b = service.create_project(db_session, ProjectInput(name="Архивный"))
    service.set_status(db_session, b, ProjectStatus.CANCELLED)
    active_ids = {p.id for p in service.list_projects(db_session, archived=False)}
    archived_ids = {p.id for p in service.list_projects(db_session, archived=True)}
    assert a.id in active_ids and b.id not in active_ids
    assert b.id in archived_ids and a.id not in archived_ids
