"""Транспорт и приёмка при нескольких стендах: группировка по стендам, слияние моделей."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.documents import builder
from app.documents.enums import DocumentType
from app.estimates import service as est_service
from app.estimates import versions
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate, KitInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.packing import service as packing_service
from app.packing.schemas import CustomPackingLine
from app.projects.schemas import ProjectInput
from app.projects.service import create_project
from app.returns import service as returns_service
from app.transport import service as transport_service


def _models(db):
    cat = cat_service.create_category(db, "Звук")
    mixer = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id, name="Микшер", accounting_type=AccountingType.SERIAL
        ),
    )
    speaker = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=3,
            weight_kg=Decimal("10"),
        ),
    )
    for bc in ("M1", "M2", "M3"):
        item_service.create_item(db, mixer, EquipmentItemInput(barcode=bc), user_id=None)
    return mixer, speaker


@pytest.fixture
def env(db_session):
    """Стенды A и B. A: микшер 2, колонка 2, складской комплект; B: микшер 1,
    колонка 1, дополнительная позиция. Отсканированы M1 (A) и M3 (B)."""
    db = db_session
    mixer, speaker = _models(db)
    project = create_project(
        db,
        ProjectInput(name="Выставка", start_date=date(2026, 9, 1), end_date=date(2026, 9, 5)),
    )
    a = versions.create_section(db, project, "A")
    b = versions.create_section(db, project, "B")
    est_service.add_model(db, a.active_version, project, mixer, 2)
    est_service.add_model(db, a.active_version, project, speaker, 2)
    est_service.add_model(db, b.active_version, project, mixer, 1)
    est_service.add_model(db, b.active_version, project, speaker, 1)
    pa = packing_service.create_from_estimate(db, project, a)
    pb = packing_service.create_from_estimate(db, project, b)
    packing_service.add_kit(db, pa, kit_service.create_kit(db, KitInput(name="Кейс света")))
    packing_service.add_custom_line(db, pb, CustomPackingLine(name="Стойка", quantity=2))
    packing_service.scan(db, pa, "M1")
    packing_service.scan(db, pb, "M3")
    db.expire_all()
    return db, project, pa, pb, mixer, speaker


def _vehicle(db, project, name="Фура"):
    vehicle = transport_service.create_vehicle(
        db, name=name, plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    return transport_service.add_vehicle_to_project(db, project, vehicle)


def _line(packing, model):
    return next(ln for ln in packing.lines if ln.model_id == model.id)


# --- Транспорт ------------------------------------------------------------------


def test_board_pool_grouped_by_stand(env):
    db, project, pa, pb, mixer, speaker = env
    board = transport_service.board(db, project)
    assert board.multi_section and board.sections == ["A", "B"]
    assert [name for name, _ in board.pool_groups] == ["A", "B"]
    pool_b = dict(board.pool_groups)["B"]
    assert {item.line.id for item in pool_b} == {ln.id for ln in pb.lines}


def test_vehicle_rows_grouped_by_stand(env):
    db, project, pa, pb, mixer, speaker = env
    pv = _vehicle(db, project)
    # Сначала строка стенда B, потом той же модели стенда A — в машине A идёт первым.
    transport_service.assign(db, project, pv, _line(pb, speaker), 1)
    transport_service.assign(db, project, pv, _line(pa, speaker), 2)
    vb = transport_service.board(db, project).vehicles[0]
    assert [(name, [r.line.id for r in rows]) for name, rows in vb.groups] == [
        ("A", [_line(pa, speaker).id]),
        ("B", [_line(pb, speaker).id]),
    ]
    # Назначение строки B не тронуло нераспределённый остаток строки A той же модели.
    assert transport_service.remaining_quantity(db, project, _line(pa, speaker)) == 0
    assert transport_service.remaining_quantity(db, project, _line(pb, speaker)) == 0


def test_transport_pdf_has_stand_headers(env):
    db, project, pa, pb, mixer, speaker = env
    pv = _vehicle(db, project)
    transport_service.assign(db, project, pv, _line(pb, speaker), 1)
    html, _ = builder.render_html(db, project, DocumentType.TRANSPORT, "ru")
    assert f"Стенды: A ({pa.number}), B ({pb.number})" in html
    assert "Стенд: B" in html  # подзаголовок в машине
    assert "Стенд: A" in html  # подзаголовок в «Не распределено»


def test_single_stand_board_and_pdf_unchanged(db_session):
    db = db_session
    mixer, speaker = _models(db)
    project = create_project(db, ProjectInput(name="Один стенд"))
    est = est_service.get_or_create_estimate(db, project)
    est_service.add_model(db, est, project, speaker, 2)
    packing = packing_service.create_from_estimate(db, project)
    pv = _vehicle(db, project)
    transport_service.assign(db, project, pv, packing.lines[0], 1)
    board = transport_service.board(db, project)
    assert not board.multi_section and board.sections == []
    assert [name for name, _ in board.pool_groups] == [None]
    html, _ = builder.render_html(db, project, DocumentType.TRANSPORT, "ru")
    assert "Стенд:" not in html and "Стенды:" not in html

    ret = returns_service.create_from_packing(db, project)
    assert [ln.section_label for ln in ret.lines] == [None]


# --- Приёмка ----------------------------------------------------------------------


def test_return_merges_models_across_stands(env):
    db, project, pa, pb, mixer, speaker = env
    ret = returns_service.create_from_packing(db, project)
    by_name = {}
    for ln in ret.lines:
        by_name.setdefault(ln.name, []).append(ln)

    [mix] = by_name["Микшер"]
    assert mix.expected_quantity == 2  # отсканировано M1 (A) и M3 (B)
    assert {si.barcode for si in mix.serial_items} == {"M1", "M3"}
    assert mix.section_label == "A: 1 · B: 1"

    [spk] = by_name["Колонка"]
    assert spk.expected_quantity == 3 and spk.section_label == "A: 2 · B: 1"

    assert by_name["Кейс света"][0].section_label == "A"
    assert by_name["Стойка"][0].section_label == "B"


def test_return_merged_line_scan_and_shortage(env):
    db, project, pa, pb, mixer, speaker = env
    ret = returns_service.create_from_packing(db, project)
    outcome = returns_service.scan(db, ret, "M3")
    assert outcome.result == returns_service.ScanResult.OK
    assert outcome.model_name == "Микшер (A: 1 · B: 1)"
    spk = next(ln for ln in ret.lines if ln.name == "Колонка")
    returns_service.update_quantity_line(db, spk, 2, None)
    db.expire_all()
    spk = next(ln for ln in ret.lines if ln.name == "Колонка")
    assert spk.missing_quantity == 1  # вернулось 2 из 3 — одной цифрой на проект


# --- Веб ----------------------------------------------------------------------------


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


def test_web_transport_and_returns_show_stands(auth_client, env):
    db, project, pa, pb, mixer, speaker = env
    pv = _vehicle(db, project)
    transport_service.assign(db, project, pv, _line(pb, speaker), 1)
    board_page = auth_client.get(f"/projects/{project.id}/transport").text
    assert "Стенд: A" in board_page and "Стенд: B" in board_page

    returns_service.create_from_packing(db, project)
    page = auth_client.get(f"/projects/{project.id}/returns").text
    assert "Стенды: A: 2 · B: 1" in page
