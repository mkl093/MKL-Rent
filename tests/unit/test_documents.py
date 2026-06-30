"""PDF-документы: локализация, рендеринг, устаревание (ТЗ §26)."""

from datetime import date
from decimal import Decimal

import pytest

from app.documents import builder
from app.documents.enums import DocumentType
from app.documents.l10n import (
    format_date,
    format_money,
    format_volume,
    format_weight,
)
from app.documents.models import GeneratedDocument
from app.estimates import service as est_service
from app.estimates.schemas import LineUpdate
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput

# --- Локализация (ТЗ §26.3) ---------------------------------------------


def test_format_date():
    d = date(2026, 6, 30)
    assert format_date(d, "ru") == "30.06.2026"
    assert format_date(d, "en") == "30 June 2026"


def test_format_money():
    assert format_money(Decimal("1250"), "ru") == "1 250,00 €"
    assert format_money(Decimal("1250"), "en") == "€1,250.00"
    assert format_money(Decimal("1234567.5"), "ru") == "1 234 567,50 €"
    assert format_money(Decimal("1234567.5"), "en") == "€1,234,567.50"


def test_format_weight_volume():
    assert format_weight(Decimal("125.4"), "ru") == "125,4 кг"
    assert format_weight(Decimal("125.4"), "en") == "125.4 kg"
    assert format_volume(Decimal("0.18"), "ru") == "0,180 м³"
    assert format_volume(Decimal("0.18"), "en") == "0.180 m³"


# --- Рендеринг и устаревание --------------------------------------------


@pytest.fixture
def project(db_session):
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
    project = proj_service.create_project(
        db_session,
        ProjectInput(
            name="Концерт",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 5),
            vat=Decimal("19"),
        ),
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_model(db_session, estimate, project, model, 2)
    return project


def test_render_estimate_html(db_session, project):
    html, fp = builder.render_html(db_session, project, DocumentType.ESTIMATE, "ru")
    assert "EST-" in html
    assert "Концерт" in html
    assert "Смета" in html
    assert "200,00 €" in html  # 100 × 2 × 1
    assert fp  # непустой отпечаток


def test_estimate_available_packing_not(db_session, project):
    statuses = {(s.doc_type, s.language.value): s for s in builder.status(db_session, project)}
    est_ru = statuses[(DocumentType.ESTIMATE, "ru")]
    pack_ru = statuses[(DocumentType.PACKING, "ru")]
    assert est_ru.available and not est_ru.generated
    assert not pack_ru.available  # packing-лист не создан


def test_generate_and_staleness(db_session, project, tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)

    builder.generate(db_session, project, DocumentType.ESTIMATE, "ru")
    assert (tmp_path / "estimate_ru.pdf").exists()

    statuses = {(s.doc_type, s.language.value): s for s in builder.status(db_session, project)}
    s = statuses[(DocumentType.ESTIMATE, "ru")]
    assert s.generated and not s.stale

    # Меняем смету → документ устаревает (ТЗ §26.6)
    estimate = est_service.get_estimate(db_session, project)
    est_service.update_line(
        db_session,
        estimate.lines[0],
        LineUpdate(quantity=5, unit_price=Decimal("100"), coefficient=Decimal("1")),
    )
    statuses = {(s.doc_type, s.language.value): s for s in builder.status(db_session, project)}
    assert statuses[(DocumentType.ESTIMATE, "ru")].stale

    # Перегенерация заменяет ту же строку (одна на сочетание)
    builder.generate(db_session, project, DocumentType.ESTIMATE, "ru")
    count = (
        db_session.query(GeneratedDocument)
        .filter_by(project_id=project.id, doc_type="estimate", language="ru")
        .count()
    )
    assert count == 1
