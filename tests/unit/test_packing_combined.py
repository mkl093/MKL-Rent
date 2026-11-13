"""Общий packing-лист проекта: слияние по модели, итоги, PDF, carnet, веб."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.documents import builder
from app.documents.enums import DocumentType
from app.estimates import service as est_service
from app.estimates import versions
from app.inventory.enums import AccountingType, PackingType
from app.inventory.schemas import (
    EquipmentItemInput,
    EquipmentModelCreate,
    KitInput,
    PackingRuleInput,
)
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.packing import carnet, combined, service
from app.packing.calc import compute_totals
from app.packing.enums import PackingStatus
from app.packing.schemas import CustomPackingLine
from app.projects.schemas import ProjectInput
from app.projects.service import create_project


@pytest.fixture
def env(db_session):
    """Два стенда. Колонка (количественная, кейс на 4 шт., 10 кг + кейс 5 кг):
    A — 2, B — 2. Микшер (серийный): A — 2, B — 1. В B — произвольная строка,
    в A — складской комплект."""
    db = db_session
    cat = cat_service.create_category(db, "Звук")
    speaker = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=4,
            weight_kg=Decimal("10"),
            packing=PackingRuleInput(
                packing_type=PackingType.CASE, capacity=4, empty_weight_kg=Decimal("5")
            ),
        ),
    )
    mixer = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id, name="Микшер", accounting_type=AccountingType.SERIAL
        ),
    )
    for bc in ("S1", "S2", "S3", "S4"):
        item_service.create_item(db, speaker, EquipmentItemInput(barcode=bc), user_id=None)
    for bc in ("M1", "M2", "M3"):
        item_service.create_item(db, mixer, EquipmentItemInput(barcode=bc), user_id=None)
    project = create_project(
        db,
        ProjectInput(name="Выставка", start_date=date(2026, 9, 1), end_date=date(2026, 9, 5)),
    )
    a = versions.create_section(db, project, "A")
    b = versions.create_section(db, project, "B")
    est_service.add_model(db, a.active_version, project, speaker, 2)
    est_service.add_model(db, a.active_version, project, mixer, 2)
    est_service.add_model(db, b.active_version, project, speaker, 2)
    est_service.add_model(db, b.active_version, project, mixer, 1)
    pa = service.create_from_estimate(db, project, a)
    pb = service.create_from_estimate(db, project, b)
    service.add_custom_line(db, pb, CustomPackingLine(name="Стойка", quantity=3))
    kit = kit_service.create_kit(db, KitInput(name="Кейс света"))
    service.add_kit(db, pa, kit)
    db.expire_all()
    return db, project, a, b, pa, pb, speaker, mixer


def _line(cp, name):
    return next(c for c in cp.lines if c.name == name)


def test_models_merge_with_distribution_and_summed_packaging(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    cp = combined.combined_packing(db, project)
    spk = _line(cp, "Колонка")
    assert spk.merged
    assert (spk.planned_quantity, spk.fact_quantity, spk.packed_quantity) == (4, 4, 4)
    assert [(s.section_name, s.fact) for s in spk.by_section] == [("A", 2), ("B", 2)]
    assert spk.stands_text == "A: 2 · B: 2"
    # Кейсы стендов разные: 1 + 1, а не ceil(4 / 4) = 1.
    assert spk.calc.packages == 2
    assert spk.calc.total_weight == Decimal("50.0")  # 4 × 10 + 2 кейса × 5

    # Только одна строка колонки и одна строка микшера на проект.
    assert sum(1 for c in cp.lines if c.name == "Колонка") == 1
    mix = _line(cp, "Микшер")
    assert mix.planned_quantity == 3 and mix.fact_quantity == 0
    assert mix.stands_text == "A: 2 · B: 1"  # факта нет — по плану


def test_kits_and_custom_lines_are_not_merged(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    cp = combined.combined_packing(db, project)
    custom = _line(cp, "Стойка")
    assert not custom.merged and custom.stands_text == "B"
    kit = _line(cp, "Кейс света")
    assert not kit.merged and kit.is_kit and kit.stands_text == "A"
    # Дополнительные позиции — в конце, как в листе стенда.
    assert cp.lines[-1] is custom


def test_totals_equal_sum_of_stands(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    cp = combined.combined_packing(db, project)
    ta, tb = compute_totals(pa.lines), compute_totals(pb.lines)
    assert cp.totals.total_weight == ta.total_weight + tb.total_weight
    assert cp.totals.total_volume == ta.total_volume + tb.total_volume
    assert cp.planned == sum(ln.planned_quantity for ln in pa.lines + pb.lines)
    assert sum(b.total_weight for b in cp.breakdown) == cp.totals.total_weight


def test_serials_union_per_stand_confirmation(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    service.scan(db, pa, "M1")
    service.scan(db, pb, "M3")
    a_placeholder = next(
        si.barcode for ln in pa.lines if ln.model_id == speaker.id for si in ln.serial_items
    )
    service.scan(db, pa, a_placeholder)  # подтверждаем одну заготовку стенда A
    db.expire_all()
    cp = combined.combined_packing(db, project)
    assert {si.barcode for si in _line(cp, "Микшер").shown_serial_items} == {"M1", "M3"}
    # У A показан только подтверждённый, у B — обе заготовки (подтверждённых нет).
    shown = [si.barcode for si in _line(cp, "Колонка").shown_serial_items]
    assert len(shown) == 3 and a_placeholder in shown


def test_combined_status():
    class _P:
        def __init__(self, status):
            self.status = status

    ns, pk, done = PackingStatus.NOT_STARTED, PackingStatus.PICKING, PackingStatus.PICKED
    assert combined.combined_status([_P(ns), _P(ns)]) == ns
    assert combined.combined_status([_P(done), _P(done)]) == done
    assert combined.combined_status([_P(done), _P(ns)]) == pk


def test_combined_shortages(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    service.set_status(
        db, pb, PackingStatus.PICKED, shortage_comment="нет микшера", confirm_undercomplete=True
    )
    cp = combined.combined_packing(db, project)
    assert cp.shortages == [("B", "нет микшера")]


# --- PDF ----------------------------------------------------------------------


@pytest.fixture
def pdf_env(env, tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)
    return env, tmp_path


def test_combined_packing_pdf(pdf_env):
    (db, project, a, b, pa, pb, speaker, mixer), tmp_path = pdf_env
    html, _ = builder.render_html(db, project, DocumentType.PACKING, "ru")
    assert f"{project.number} — все стенды" in html
    assert f"A ({pa.number}), B ({pb.number})" in html
    assert "<div>A: 2</div><div>B: 2</div>" in html
    html_en, _ = builder.render_html(db, project, DocumentType.PACKING, "en")
    assert "all stands" in html_en and "Stands" in html_en

    # Лист стенда и общий лист хранятся раздельно и не перезаписывают друг друга.
    builder.generate(db, project, DocumentType.PACKING, "ru", packing=pa)
    doc = builder.generate(db, project, DocumentType.PACKING, "ru")
    assert doc.scope == "project"
    assert (tmp_path / "packing_all_ru.pdf").exists()
    assert (tmp_path / f"packing_pl{pa.id}_ru.pdf").exists()

    status = {(s.doc_type, s.language.value): s for s in builder.combined_status(db, project)}
    assert status[(DocumentType.PACKING, "ru")].generated
    assert not status[(DocumentType.PACKING, "ru")].stale
    assert not status[(DocumentType.PICKING, "ru")].generated

    # Изменение листа любого стенда делает общий PDF устаревшим.
    service.scan(db, pb, "M3")
    status = {(s.doc_type, s.language.value): s for s in builder.combined_status(db, project)}
    assert status[(DocumentType.PACKING, "ru")].stale


def test_combined_picking_pdf(pdf_env):
    (db, project, a, b, pa, pb, speaker, mixer), tmp_path = pdf_env
    html, _ = builder.render_html(db, project, DocumentType.PICKING, "ru")
    assert "все стенды" in html
    assert "4 / 4" in html  # колонки: план/факт суммой
    assert "<div>A: 2 / 0</div><div>B: 1 / 0</div>" in html  # микшер: план / факт по стендам
    builder.generate(db, project, DocumentType.PICKING, "ru")
    assert (tmp_path / "picking_all_ru.pdf").exists()


def test_stand_pdf_unchanged_by_combined(pdf_env):
    (db, project, a, b, pa, pb, speaker, mixer), tmp_path = pdf_env
    html, _ = builder.render_html(db, project, DocumentType.PACKING, "ru", packing=pb)
    assert "все стенды" not in html and "Стенд: B" in html


# --- Carnet -------------------------------------------------------------------


def test_combined_carnet_merges_models(env):
    db, project, a, b, pa, pb, speaker, mixer = env
    service.scan(db, pa, "M1")
    service.scan(db, pb, "M3")
    db.expire_all()
    rows = carnet.build_rows(db, combined.combine_lines(service.list_packings(db, project)))
    mixer_rows = [r for r in rows if r.description.startswith("Микшер")]
    assert len(mixer_rows) == 1
    assert mixer_rows[0].quantity == 2
    assert "M1" in mixer_rows[0].description and "M3" in mixer_rows[0].description
    # По стенду — как раньше.
    rows_b = carnet.build_rows(db, pb)
    assert [r.quantity for r in rows_b if r.description.startswith("Микшер")] == [1]


# --- Веб ----------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db_session):
    auth_service.create_user(db_session, "admin", "pass123")
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    return client


def _csrf(client, url) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_web_overview_shows_combined(auth_client, env):
    db, project, a, b, pa, pb, speaker, mixer = env
    page = auth_client.get(f"/projects/{project.id}/packing").text
    assert "Общий packing-лист проекта" in page
    assert "Итого по проекту" in page
    assert "Стойка" in page and "Кейс света" in page
    detail = auth_client.get(f"/projects/{project.id}").text
    assert "Packing-листы (общий)" in detail


def test_web_overview_without_combined_for_single_packing(auth_client, db_session):
    db = db_session
    project = create_project(db, ProjectInput(name="Проект"))
    a = versions.create_section(db, project, "A")
    versions.create_section(db, project, "B")
    service.create_from_estimate(db, project, a)
    page = auth_client.get(f"/projects/{project.id}/packing").text
    assert "Общий packing-лист проекта" not in page
    docs = auth_client.get(f"/projects/{project.id}/documents").text
    assert "Общий carnet" not in docs


def test_web_documents_combined(auth_client, env, tmp_path, monkeypatch):
    db, project, a, b, pa, pb, speaker, mixer = env
    from app.documents import router as documents_router

    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    storage = tmp_path / "documents" / f"project_{project.id}"
    storage.mkdir(parents=True)
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: storage)

    class _S:
        storage_path = str(tmp_path)

    monkeypatch.setattr(documents_router, "get_settings", lambda: _S())

    docs_url = f"/projects/{project.id}/documents"
    docs = auth_client.get(docs_url).text
    assert "Общий carnet" in docs and "все стенды" in docs

    token = _csrf(auth_client, docs_url)
    resp = auth_client.post(
        f"{docs_url}/generate",
        data={"doc_type": "packing", "language": "ru", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert builder.get_document(db, project.id, DocumentType.PACKING, "ru", "project") is not None

    pdf = auth_client.get(f"{docs_url}/packing/ru/download")
    assert pdf.status_code == 200
    assert "_all_ru.pdf" in pdf.headers["content-disposition"]

    xlsx = auth_client.get(f"{docs_url}/carnet")
    assert xlsx.status_code == 200
    assert f"carnet_{project.number}_all.xlsx" in xlsx.headers["content-disposition"]
