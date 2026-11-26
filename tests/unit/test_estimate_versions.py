"""Площадки проекта и версии смет: активация, заморозка, бронь, packing, копирование."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.accessory_kits import service as accessory_kit_service
from app.accessory_kits.schemas import AccessoryKitInput
from app.documents import builder
from app.documents.enums import DocumentType
from app.estimates import service, versions
from app.estimates.schemas import CustomLineInput, LineUpdate
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate, KitInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import kits as kit_service
from app.packing import service as packing_service
from app.projects.models import ProjectReservation
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


@pytest.fixture
def env(db_session):
    cat = cat_service.create_category(db_session, "Свет")
    spot = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Прожектор",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            base_price_eur=Decimal("10.00"),
        ),
    )
    wash = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Заливка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            base_price_eur=Decimal("20.00"),
        ),
    )
    project = create_project(
        db_session,
        ProjectInput(
            name="Выставка",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 5),
            rental_coefficient=Decimal("1.5"),
            vat=Decimal("19"),
        ),
    )
    return project, spot, wash


def _reservations(db, project) -> dict[int, int]:
    rows = db.query(ProjectReservation).filter_by(project_id=project.id).all()
    return {r.model_id: r.quantity for r in rows if r.model_id is not None}


# --- Площадки ------------------------------------------------------------------


def test_create_section_has_active_v1(db_session, env):
    project, _, _ = env
    section = versions.create_section(db_session, project, "  Площадка A  ")
    assert section.name == "Площадка A"
    assert section.number.startswith("EST-")
    (v1,) = section.versions
    assert v1.version_no == 1 and v1.is_active and not v1.is_locked
    assert v1.number == section.number
    assert v1.rental_coefficient == Decimal("1.5")
    assert v1.display_number == f"{section.number} · v1"


def test_create_section_requires_name(db_session, env):
    project, _, _ = env
    with pytest.raises(service.EstimateError):
        versions.create_section(db_session, project, "   ")


def test_get_or_create_estimate_makes_default_section(db_session, env):
    project, _, _ = env
    estimate = service.get_or_create_estimate(db_session, project)
    (section,) = versions.list_sections(db_session, project)
    assert section.name == versions.DEFAULT_SECTION_NAME
    assert estimate.section_id == section.id and estimate.is_active
    # повторный вызов не создаёт второй площадка
    assert service.get_or_create_estimate(db_session, project).id == estimate.id
    assert len(versions.list_sections(db_session, project)) == 1


def test_reservations_sum_active_versions_of_all_sections(db_session, env):
    project, spot, wash = env
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_model(db_session, a.active_version, project, spot, 4)
    service.add_model(db_session, b.active_version, project, spot, 2)
    service.add_model(db_session, b.active_version, project, wash, 1)
    assert _reservations(db_session, project) == {spot.id: 6, wash.id: 1}


def test_move_section(db_session, env):
    project, _, _ = env
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    versions.move_section(db_session, project, b, -1)
    assert [s.name for s in versions.list_sections(db_session, project)] == ["B", "A"]
    del a


def test_delete_section_only_when_empty(db_session, env):
    project, spot, _ = env
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_model(db_session, a.active_version, project, spot, 3)
    with pytest.raises(service.EstimateError):
        versions.delete_section(db_session, project, a)
    versions.delete_section(db_session, project, b)
    assert [s.name for s in versions.list_sections(db_session, project)] == ["A"]
    assert _reservations(db_session, project) == {spot.id: 3}


# --- Версии ------------------------------------------------------------------


def test_create_version_copies_and_is_inactive(db_session, env):
    project, spot, _ = env
    section = versions.create_section(db_session, project, "A")
    v1 = section.active_version
    service.add_model(db_session, v1, project, spot, 4)
    service.set_discount(db_session, v1, Decimal("5"))
    service.set_hide_prices(db_session, v1, True)

    v2 = versions.create_version(db_session, project, section, source=v1, label=" правки ")
    assert v2.version_no == 2 and not v2.is_active
    assert v2.number == f"{section.number}-v2"
    assert v2.label == "правки"
    assert [(ln.model_id, ln.quantity) for ln in v2.lines] == [(spot.id, 4)]
    assert v2.discount_percent == Decimal("5") and v2.hide_prices is True
    # строки — копии, а не те же объекты
    assert v2.lines[0].id != v1.lines[0].id

    # правка неактивной версии не трогает бронь
    service.add_model(db_session, v2, project, spot, 6)
    assert _reservations(db_session, project) == {spot.id: 4}


def test_create_empty_version(db_session, env):
    project, spot, _ = env
    section = versions.create_section(db_session, project, "A")
    service.add_model(db_session, section.active_version, project, spot, 4)
    v2 = versions.create_version(db_session, project, section)
    assert v2.lines == [] and v2.rental_coefficient == Decimal("1.5")


def test_activate_version_switches_reservations(db_session, env):
    project, spot, wash = env
    section = versions.create_section(db_session, project, "A")
    v1 = section.active_version
    service.add_model(db_session, v1, project, spot, 4)
    v2 = versions.create_version(db_session, project, section, source=v1)
    service.update_line(
        db_session,
        v2.lines[0],
        LineUpdate(quantity=10, unit_price=Decimal("10"), coefficient=Decimal("1.5")),
    )
    service.add_model(db_session, v2, project, wash, 2)

    versions.activate_version(db_session, project, v2)
    db_session.refresh(v1)
    assert v2.is_active and not v1.is_active and v2.activated_at is not None
    assert _reservations(db_session, project) == {spot.id: 10, wash.id: 2}

    versions.activate_version(db_session, project, v1)
    assert _reservations(db_session, project) == {spot.id: 4}


def test_create_version_with_activate(db_session, env):
    project, spot, _ = env
    section = versions.create_section(db_session, project, "A")
    service.add_model(db_session, section.active_version, project, spot, 4)
    v2 = versions.create_version(db_session, project, section, activate=True)
    assert v2.is_active
    assert _reservations(db_session, project) == {}


def test_only_one_active_version_per_section(db_session, env):
    project, _, _ = env
    section = versions.create_section(db_session, project, "A")
    v2 = versions.create_version(db_session, project, section)
    v2.is_active = True  # в обход activate_version
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_preview_activation(db_session, env):
    project, spot, wash = env
    section = versions.create_section(db_session, project, "A")
    v1 = section.active_version
    service.add_model(db_session, v1, project, spot, 4)
    service.add_model(db_session, v1, project, wash, 1)
    v2 = versions.create_version(db_session, project, section)
    service.add_model(db_session, v2, project, spot, 6)

    preview = versions.preview_activation(db_session, project, v2)
    assert preview.changed == [("Прожектор", 4, 6)]
    assert preview.removed == [("Заливка", 1)]
    assert preview.added == []
    assert not preview.packing_exists

    packing_service.create_from_estimate(db_session, project)
    preview = versions.preview_activation(db_session, project, v2)
    assert preview.packing_exists and preview.packing_discrepancies == 2


# --- Заморозка -----------------------------------------------------------------


def test_locked_version_rejects_all_mutations(db_session, env):
    project, spot, _ = env
    section = versions.create_section(db_session, project, "A")
    estimate = section.active_version
    service.add_model(db_session, estimate, project, spot, 2)
    line = estimate.lines[0]
    versions.lock_version(db_session, estimate, None)
    assert estimate.is_locked and estimate.locked_at is not None

    mutations = [
        lambda: service.add_model(db_session, estimate, project, spot, 1),
        lambda: service.add_custom_line(
            db_session, estimate, project, CustomLineInput(name="Доставка")
        ),
        lambda: service.update_line(
            db_session,
            line,
            LineUpdate(quantity=9, unit_price=Decimal("1"), coefficient=Decimal("1")),
        ),
        lambda: service.delete_line(db_session, line),
        lambda: service.move_line(db_session, estimate, line, 1),
        lambda: service.set_discount(db_session, estimate, Decimal("10")),
        lambda: service.set_coefficient(db_session, estimate, Decimal("3")),
        lambda: service.reset_line_coefficient(db_session, line),
        lambda: service.set_hide_prices(db_session, estimate, True),
        lambda: versions.set_version_label(db_session, estimate, "x"),
    ]
    for mutate in mutations:
        with pytest.raises(service.EstimateLocked):
            mutate()
    db_session.refresh(line)
    assert line.quantity == 2

    versions.unlock_version(db_session, estimate)
    service.add_model(db_session, estimate, project, spot, 1)
    assert estimate.lines[0].quantity == 3


def test_locked_version_can_be_activated_and_copied(db_session, env):
    project, spot, _ = env
    section = versions.create_section(db_session, project, "A")
    v1 = section.active_version
    service.add_model(db_session, v1, project, spot, 2)
    v2 = versions.create_version(db_session, project, section, source=v1)
    versions.lock_version(db_session, v2, None)

    versions.activate_version(db_session, project, v2)
    assert v2.is_active

    v3 = versions.create_version(db_session, project, section, source=v2)
    assert not v3.is_locked and len(v3.lines) == 1


# --- Комплекты ----------------------------------------------------------------


def test_kit_belongs_to_one_section(db_session, env):
    project, _, _ = env
    kit = kit_service.create_kit(db_session, KitInput(name="Кейс света"))
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    assert service.add_kit_line(db_session, a.active_version, project, kit) is not None
    with pytest.raises(service.EstimateError, match="«A»"):
        service.add_kit_line(db_session, b.active_version, project, kit)
    # в другую версию своей площадки — можно
    v2 = versions.create_version(db_session, project, a)
    assert service.add_kit_line(db_session, v2, project, kit) is not None


def test_accessory_kit_belongs_to_one_section(db_session, env):
    project, _, _ = env
    ak = accessory_kit_service.create_kit(db_session, project, AccessoryKitInput(name="Кабели"))
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_accessory_kit_line(db_session, a.active_version, project, ak)
    with pytest.raises(service.EstimateError):
        service.add_accessory_kit_line(db_session, b.active_version, project, ak)


# --- Packing и копирование ----------------------------------------------------


def test_packing_per_section_uses_its_active_version(db_session, env):
    project, spot, wash = env
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_model(db_session, a.active_version, project, spot, 3)
    service.add_model(db_session, b.active_version, project, spot, 2)
    service.add_model(db_session, b.active_version, project, wash, 1)
    # неактивная версия в план не попадает
    v2 = versions.create_version(db_session, project, a)
    service.add_model(db_session, v2, project, wash, 9)

    pa = packing_service.create_from_estimate(db_session, project, a)
    pb = packing_service.create_from_estimate(db_session, project, b)
    assert pa.section_id == a.id and pb.section_id == b.id
    assert {ln.model_id: ln.planned_quantity for ln in pa.lines} == {spot.id: 3}
    assert {ln.model_id: ln.planned_quantity for ln in pb.lines} == {spot.id: 2, wash.id: 1}
    assert packing_service.discrepancies(db_session, project, pa) == []
    assert [pl.id for pl in packing_service.list_packings(db_session, project)] == [pa.id, pb.id]

    with pytest.raises(packing_service.AlreadyExists):
        packing_service.create_from_estimate(db_session, project, a)

    # Смена активной версии площадки A затрагивает только его лист.
    versions.activate_version(db_session, project, v2)
    assert {d.name for d in packing_service.discrepancies(db_session, project, pa)} == {
        "Прожектор",
        "Заливка",
    }
    assert packing_service.discrepancies(db_session, project, pb) == []


def test_estimate_sync_writes_into_own_section(db_session, env):
    project, spot, _ = env
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_model(db_session, a.active_version, project, spot, 3)
    service.add_model(db_session, b.active_version, project, spot, 2)
    pb = packing_service.create_from_estimate(db_session, project, b)
    pb.lines[0].quantity = 5
    db_session.commit()

    items = packing_service.estimate_discrepancies(db_session, project, pb)
    assert [(it.estimate_quantity, it.fact_quantity) for it in items] == [(2, 5)]
    packing_service.apply_estimate_sync(db_session, project, pb, {it.key for it in items})
    db_session.refresh(a.active_version)
    db_session.refresh(b.active_version)
    assert b.active_version.lines[0].quantity == 5
    assert a.active_version.lines[0].quantity == 3  # смета другой площадки не тронута


def test_section_with_packing_cannot_be_deleted(db_session, env):
    project, _, _ = env
    a = versions.create_section(db_session, project, "A")
    versions.create_section(db_session, project, "B")
    packing_service.create_from_estimate(db_session, project, a)
    with pytest.raises(service.EstimateError, match="packing"):
        versions.delete_section(db_session, project, a)


def test_estimate_sync_into_locked_version_rejected(db_session, env):
    project, spot, _ = env
    a = versions.create_section(db_session, project, "A")
    service.add_model(db_session, a.active_version, project, spot, 3)
    packing = packing_service.create_from_estimate(db_session, project)
    packing.lines[0].quantity = 5
    db_session.commit()
    versions.lock_version(db_session, a.active_version, None)
    keys = {it.key for it in packing_service.estimate_discrepancies(db_session, project, packing)}
    with pytest.raises(service.EstimateLocked):
        packing_service.apply_estimate_sync(db_session, project, packing, keys)


def test_copy_sections_copies_active_versions_only(db_session, env):
    project, spot, wash = env
    ak = accessory_kit_service.create_kit(db_session, project, AccessoryKitInput(name="Кабели"))
    a = versions.create_section(db_session, project, "A")
    b = versions.create_section(db_session, project, "B")
    service.add_model(db_session, a.active_version, project, spot, 3)
    service.add_accessory_kit_line(db_session, a.active_version, project, ak)
    v2 = versions.create_version(db_session, project, b, activate=True)
    service.add_model(db_session, v2, project, wash, 2)

    target = create_project(db_session, ProjectInput(name="Копия"))
    versions.copy_sections(db_session, project, target)
    sections = versions.list_sections(db_session, target)
    assert [s.name for s in sections] == ["A", "B"]
    assert all(len(s.versions) == 1 and s.versions[0].is_active for s in sections)
    a_lines = sections[0].versions[0].lines
    assert [ln.model_id for ln in a_lines] == [spot.id]  # кабелярка не копируется
    assert [(ln.model_id, ln.quantity) for ln in sections[1].versions[0].lines] == [(wash.id, 2)]
    assert _reservations(db_session, target) == {spot.id: 3, wash.id: 2}


# --- PDF сметы ----------------------------------------------------------------


def test_estimate_pdf_shows_section_and_version(db_session, env):
    project, spot, _ = env
    a = versions.create_section(db_session, project, "Площадка A")
    service.add_model(db_session, a.active_version, project, spot, 2)
    html, _ = builder.render_html(
        db_session, project, DocumentType.ESTIMATE, "ru", estimate=a.active_version
    )
    assert "Площадка: Площадка A" in html
    assert f"Смета {a.number}<" in html  # одна версия — без «· v1»

    v2 = versions.create_version(db_session, project, a, source=a.active_version)
    html, _ = builder.render_html(db_session, project, DocumentType.ESTIMATE, "en", estimate=v2)
    assert f"{a.number} · v2" in html
    assert "Venue: Площадка A" in html


def test_default_section_name_hidden_in_pdf(db_session, env):
    project, spot, _ = env
    estimate = service.get_or_create_estimate(db_session, project)
    service.add_model(db_session, estimate, project, spot, 2)
    html, _ = builder.render_html(db_session, project, DocumentType.ESTIMATE, "ru")
    assert "Площадка:" not in html


def test_delete_line_releases_reservation(db_session, env):
    """Удалённая из сметы строка снимает бронь (коллекция estimate.lines не устаревает)."""
    project, spot, wash = env
    section = versions.create_section(db_session, project, "A")
    estimate = section.versions[0]
    service.add_model(db_session, estimate, project, spot, 3)
    service.add_model(db_session, estimate, project, wash, 2)
    assert _reservations(db_session, project) == {spot.id: 3, wash.id: 2}

    line = next(ln for ln in estimate.lines if ln.model_id == spot.id)
    service.delete_line(db_session, line)
    service.sync_reservations(db_session, project)
    assert _reservations(db_session, project) == {wash.id: 2}
    assert [ln.model_id for ln in estimate.lines] == [wash.id]
