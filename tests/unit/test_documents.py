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
    assert format_date(d, "de") == "30. Juni 2026"
    assert format_date(date(2026, 3, 1), "de") == "1. März 2026"


def test_format_money():
    assert format_money(Decimal("1250"), "ru") == "1 250,00 €"
    assert format_money(Decimal("1250"), "en") == "€1,250.00"
    assert format_money(Decimal("1250"), "de") == "1.250,00 €"
    assert format_money(Decimal("1234567.5"), "ru") == "1 234 567,50 €"
    assert format_money(Decimal("1234567.5"), "en") == "€1,234,567.50"
    assert format_money(Decimal("1234567.5"), "de") == "1.234.567,50 €"


def test_format_weight_volume():
    assert format_weight(Decimal("125.4"), "ru") == "125,4 кг"
    assert format_weight(Decimal("125.4"), "en") == "125.4 kg"
    assert format_weight(Decimal("125.4"), "de") == "125,4 kg"
    assert format_volume(Decimal("0.18"), "ru") == "0,180 м³"
    assert format_volume(Decimal("0.18"), "en") == "0.180 m³"
    assert format_volume(Decimal("0.18"), "de") == "0,180 m³"


def test_format_percent_de():
    from app.documents.l10n import format_percent

    assert format_percent(Decimal("19"), "de") == "19 %"
    assert format_percent(Decimal("19"), "ru") == "19%"
    assert format_percent(Decimal("19"), "en") == "19%"


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


def test_estimate_html_shows_logo_and_company_ids(db_session, project, tmp_path, monkeypatch):
    """Смета показывает логотип, VAT ID и налоговый номер компании раздельно."""
    from app.settings.service import get_company_settings

    # Кладём логотип во временное хранилище и прописываем его в настройках.
    monkeypatch.setattr("app.documents.builder.get_settings", lambda: _FakeStorage(tmp_path))
    logo_dir = tmp_path / "logo"
    logo_dir.mkdir()
    (logo_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    company = get_company_settings(db_session)
    company.logo_path = "logo/logo.png"
    company.vat_id = "DE123456789"
    company.tax_number = "12/345/67890"
    db_session.commit()

    html, _ = builder.render_html(db_session, project, DocumentType.ESTIMATE, "ru")
    assert 'class="logo"' in html
    assert "DE123456789" in html
    assert "Налоговый номер" in html
    assert "12/345/67890" in html


class _FakeStorage:
    def __init__(self, path):
        self.storage_path = str(path)


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


def test_generate_real_pdf_xhtml2pdf(db_session, project, tmp_path, monkeypatch):
    """Сквозная генерация PDF локально через xhtml2pdf (без WeasyPrint/Docker)."""
    monkeypatch.setattr(builder, "resolve_engine", lambda: "xhtml2pdf")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)

    builder.generate(db_session, project, DocumentType.ESTIMATE, "ru")
    data = (tmp_path / "estimate_ru.pdf").read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 5000  # кириллический шрифт встроен в документ


def test_render_estimate_html_de(db_session, project):
    html, fp = builder.render_html(db_session, project, DocumentType.ESTIMATE, "de")
    assert "Angebot" in html  # немецкая подпись «Смета»
    assert "Mietzeitraum" in html
    assert "1. Juli 2026" in html  # дата аренды по-немецки
    assert "200,00 €" in html  # 100 × 2 × 1
    assert fp


def test_generate_real_pdf_de_xhtml2pdf(db_session, project, tmp_path, monkeypatch):
    """Сквозная генерация DE-сметы через xhtml2pdf: латиница/€/³ рендерятся."""
    monkeypatch.setattr(builder, "resolve_engine", lambda: "xhtml2pdf")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)

    builder.generate(db_session, project, DocumentType.ESTIMATE, "de")
    data = (tmp_path / "estimate_de.pdf").read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 5000


def test_generate_pdf_embeds_logo_xhtml2pdf(db_session, project, tmp_path, monkeypatch):
    """Логотип компании попадает в PDF при локальной сборке через xhtml2pdf.

    Regression: xhtml2pdf на Windows не забирал изображение из file://-URI —
    добавлен link_callback, преобразующий URI в путь ФС.
    """
    import io

    from PIL import Image

    from app.settings.service import get_company_settings

    monkeypatch.setattr("app.documents.builder.get_settings", lambda: _FakeStorage(tmp_path))
    monkeypatch.setattr(builder, "resolve_engine", lambda: "xhtml2pdf")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)

    logo_dir = tmp_path / "logo"
    logo_dir.mkdir()
    buf = io.BytesIO()
    Image.new("RGBA", (40, 20), (10, 20, 30, 255)).save(buf, format="PNG")
    (logo_dir / "logo.png").write_bytes(buf.getvalue())

    company = get_company_settings(db_session)
    company.logo_path = "logo/logo.png"
    db_session.commit()

    builder.generate(db_session, project, DocumentType.ESTIMATE, "ru")
    data = (tmp_path / "estimate_ru.pdf").read_bytes()
    assert data[:5] == b"%PDF-"
    # Растровое изображение в PDF хранится как XObject.
    assert b"/XObject" in data


def test_resolve_engine_override(monkeypatch):
    from app.documents.builder import resolve_engine

    monkeypatch.setattr("app.documents.builder.get_settings", lambda: _FakeSettings("xhtml2pdf"))
    assert resolve_engine() == "xhtml2pdf"
    monkeypatch.setattr("app.documents.builder.get_settings", lambda: _FakeSettings("weasyprint"))
    assert resolve_engine() == "weasyprint"


class _FakeSettings:
    def __init__(self, engine):
        self.pdf_engine = engine
