"""Packing-листы стендов: экземпляр — в одном листе проекта, транспорт/приёмка/документы."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.accessory_kits import service as accessory_kit_service
from app.accessory_kits.schemas import AccessoryKitInput
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
from app.packing import service
from app.packing.models import PackingSerialItem
from app.projects.schemas import ProjectInput
from app.projects.service import create_project
from app.returns import service as returns_service
from app.transport import service as transport_service


@pytest.fixture
def env(db_session):
    """Проект с двумя стендами: в смете A — 2 микшера (серийные) и 2 колонки,
    в смете B — 1 микшер и 1 колонка. На складе по 3 экземпляра со штрих-кодами."""
    db = db_session
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
        ),
    )
    for bc in ("M1", "M2", "M3"):
        item_service.create_item(db, mixer, EquipmentItemInput(barcode=bc), user_id=None)
    for bc in ("S1", "S2", "S3"):
        item_service.create_item(db, speaker, EquipmentItemInput(barcode=bc), user_id=None)
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
    pa = service.create_from_estimate(db, project, a)
    pb = service.create_from_estimate(db, project, b)
    return db, project, a, b, pa, pb, mixer, speaker


def _barcodes(packing) -> set[str]:
    return {si.barcode for ln in packing.lines for si in ln.serial_items}


def test_auto_assigned_barcodes_do_not_overlap(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    # Колонки (количественные) получили заготовки: A — 2, B — 1, без пересечений.
    sa = {bc for bc in _barcodes(pa) if bc.startswith("S")}
    sb = {bc for bc in _barcodes(pb) if bc.startswith("S")}
    assert len(sa) == 2 and len(sb) == 1
    assert not sa & sb


def test_scan_rejects_item_confirmed_in_other_stand(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    assert service.scan(db, pa, "M1").result == service.SerialResult.OK
    outcome = service.scan(db, pb, "M1")
    assert outcome.result == service.SerialResult.IN_OTHER_PACKING
    assert outcome.other_section == "A"
    assert "M1" not in _barcodes(pb)
    # Ручное назначение в серийную строку тоже отклоняется.
    line_b = next(ln for ln in pb.lines if ln.model_id == mixer.id)
    assert service.add_serial_item(db, line_b, "M1") == service.SerialResult.IN_OTHER_PACKING
    # Свободный экземпляр — можно.
    assert service.scan(db, pb, "M3").result == service.SerialResult.OK


def test_scan_claims_unconfirmed_placeholder_from_other_stand(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    placeholder_b = next(bc for bc in _barcodes(pb) if bc.startswith("S"))
    line_b = next(ln for ln in pb.lines if ln.model_id == speaker.id)
    fact_b = line_b.fact_quantity

    # Реальный скан на стенде A забирает автоподобранный (не подтверждённый) экземпляр B.
    outcome = service.scan(db, pa, placeholder_b)
    assert outcome.result == service.SerialResult.OK
    db.expire_all()
    assert placeholder_b in _barcodes(pa)
    assert placeholder_b not in _barcodes(pb)
    assert line_b.fact_quantity == fact_b  # факт B не меняется
    confirmed = db.query(PackingSerialItem).filter_by(barcode=placeholder_b).one()
    assert confirmed.confirmed_by_scan

    # B позже подберёт другой свободный экземпляр — если он есть.
    service.backfill_quantity_barcodes(db, pb)
    db.expire_all()
    assert not _barcodes(pa) & _barcodes(pb)


def test_wrong_model_hints_other_stand(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    line = next(ln for ln in pb.lines if ln.model_id == mixer.id)
    service.delete_line(db, line)
    db.expire_all()
    outcome = service.scan(db, pb, "M3")
    assert outcome.result == service.SerialResult.WRONG_MODEL
    assert outcome.other_section == "A"


def test_manual_selection_excludes_items_of_other_stands(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    service.scan(db, pa, "M1")
    used = service.project_used_item_ids(db, pb)
    m1 = item_service.find_by_barcode(db, "M1")
    assert m1.id in used


def test_kits_belong_to_one_packing(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    kit = kit_service.create_kit(db, KitInput(name="Кейс света"))
    assert service.add_kit(db, pa, kit) is not None
    with pytest.raises(service.PackingError, match="«A»"):
        service.add_kit(db, pb, kit)

    ak = accessory_kit_service.create_kit(db, project, AccessoryKitInput(name="Кабели"))
    assert service.add_accessory_kit(db, pb, ak) is not None
    with pytest.raises(service.PackingError, match="«B»"):
        service.add_accessory_kit(db, pa, ak)


def test_transport_and_returns_use_all_packings(env):
    db, project, a, b, pa, pb, mixer, speaker = env
    service.scan(db, pa, "M1")
    service.scan(db, pb, "M3")

    board = transport_service.board(db, project)
    pool_lines = {u.line.id for u in board.unassigned}
    assert pool_lines & {ln.id for ln in pa.lines}
    assert pool_lines & {ln.id for ln in pb.lines}

    vehicle = transport_service.create_vehicle(
        db, name="Фура", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    pv = transport_service.add_vehicle_to_project(db, project, vehicle)
    line_b = next(ln for ln in pb.lines if ln.model_id == mixer.id)
    assert transport_service.get_line(db, project, line_b.id) is line_b
    transport_service.assign(db, project, pv, line_b, 1)
    board = transport_service.board(db, project)
    assert [r.line.id for r in board.vehicles[0].assignments] == [line_b.id]

    ret = returns_service.create_from_packing(db, project)
    serials = {si.barcode for ln in ret.lines for si in ln.serial_items}
    assert {"M1", "M3"} <= serials


def test_packing_documents_per_stand(env, tmp_path, monkeypatch):
    db, project, a, b, pa, pb, mixer, speaker = env
    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)

    html, _ = builder.render_html(db, project, DocumentType.PACKING, "ru", packing=pb)
    assert pb.number in html and "Стенд: B" in html
    builder.generate(db, project, DocumentType.PACKING, "ru", packing=pb)
    builder.generate(db, project, DocumentType.PICKING, "ru", packing=pa)
    assert (tmp_path / f"packing_pl{pb.id}_ru.pdf").exists()
    assert (tmp_path / f"picking_pl{pa.id}_ru.pdf").exists()

    status_b = {(s.doc_type, s.language.value): s for s in builder.packing_status(db, project, pb)}
    status_a = {(s.doc_type, s.language.value): s for s in builder.packing_status(db, project, pa)}
    assert status_b[(DocumentType.PACKING, "ru")].generated
    assert not status_a[(DocumentType.PACKING, "ru")].generated
    assert status_a[(DocumentType.PICKING, "ru")].generated


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


def test_web_stand_packing_pages(auth_client, env):
    db, project, a, b, pa, pb, mixer, speaker = env
    # Несколько стендов — обзор листов.
    overview = auth_client.get(f"/projects/{project.id}/packing")
    assert overview.status_code == 200
    assert pa.number in overview.text and pb.number in overview.text

    base_b = f"/projects/{project.id}/sections/{b.id}/packing"
    page = auth_client.get(base_b)
    assert page.status_code == 200
    assert pb.number in page.text and "· B" in page.text

    # Скан в лист B экземпляра, уже подтверждённого в листе A.
    service.scan(db, pa, "M1")
    token = _csrf(auth_client, f"{base_b}/scan")
    data = auth_client.post(f"{base_b}/scan", data={"barcode": "M1", "csrf_token": token}).json()
    assert data["ok"] is False
    assert data["result"] == "in_other_packing"
    assert "«A»" in data["message"]

    # Чужой стенд в адресе — 303 на обзор (не 404/500), лист другого проекта не открыть.
    other = create_project(db, ProjectInput(name="Другой"))
    other_section = versions.create_section(db, other, "X")
    resp = auth_client.get(
        f"/projects/{project.id}/sections/{other_section.id}/packing", follow_redirects=False
    )
    assert resp.status_code in (302, 303, 307)

    # Карточка проекта показывает листы стендов.
    detail = auth_client.get(f"/projects/{project.id}").text
    assert pa.number in detail and pb.number in detail

    # Документы: блоки стендов с packing-листом и carnet.
    docs = auth_client.get(f"/projects/{project.id}/documents").text
    assert f"/documents/carnet/{pb.id}" in docs
    carnet = auth_client.get(f"/projects/{project.id}/documents/carnet/{pb.id}")
    assert carnet.status_code == 200
    assert pb.number in carnet.headers["content-disposition"]


def test_web_create_packing_for_stand(auth_client, db_session):
    db = db_session
    project = create_project(db, ProjectInput(name="Проект"))
    a = versions.create_section(db, project, "A")
    versions.create_section(db, project, "B")
    base = f"/projects/{project.id}/sections/{a.id}/packing"
    assert "Создать packing-лист" in auth_client.get(base).text
    token = _csrf(auth_client, base)
    resp = auth_client.post(f"{base}/create", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert service.get_section_packing(db, a) is not None
    overview = auth_client.get(f"/projects/{project.id}/packing").text
    assert "не создан" in overview  # у стенда B листа нет


def test_stand_fingerprint_only_when_stand_is_shown(db_session, tmp_path, monkeypatch):
    """Имя стенда входит в отпечаток, только когда выводится в шапке: у проекта с
    одним «Основным стендом» PDF, сформированные до стендов, не устаревают."""
    db = db_session
    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    monkeypatch.setattr(builder, "_storage_dir", lambda pid: tmp_path)
    project = create_project(db, ProjectInput(name="Один стенд"))
    est = est_service.get_or_create_estimate(db, project)
    section = est.section
    packing = service.create_from_estimate(db, project, section)

    def stale():
        return {
            s.doc_type: s.stale
            for s in builder.packing_status(db, project, packing)
            if s.language.value == "ru"
        }

    for dt in (DocumentType.PACKING, DocumentType.PICKING):
        html, _ = builder.render_html(db, project, dt, "ru", packing=packing)
        assert "Стенд:" not in html
        builder.generate(db, project, dt, "ru", packing=packing)
    assert stale() == {DocumentType.PACKING: False, DocumentType.PICKING: False}

    # Переименованный стенд появляется в шапке — PDF устаревает.
    versions.rename_section(db, section, "Hall 1")
    assert stale() == {DocumentType.PACKING: True, DocumentType.PICKING: True}


def test_batch_backfill_does_not_share_items_between_stands(db_session):
    """Скрипт бэкфилла идёт по листам одной транзакцией (commit=False): заготовки
    одного стенда должны быть видны следующему, иначе экземпляр попадёт в два листа."""
    db = db_session
    cat = cat_service.create_category(db, "Свет")
    lamp = eq_service.create_model(
        db,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Прибор",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=2,
        ),
    )
    project = create_project(db, ProjectInput(name="Бэкфилл"))
    a = versions.create_section(db, project, "A")
    b = versions.create_section(db, project, "B")
    est_service.add_model(db, a.active_version, project, lamp, 1)
    est_service.add_model(db, b.active_version, project, lamp, 1)
    pa = service.create_from_estimate(db, project, a)
    pb = service.create_from_estimate(db, project, b)
    assert not _barcodes(pa) and not _barcodes(pb)  # экземпляров со штрих-кодом ещё нет

    for bc in ("L1", "L2"):
        item_service.create_item(db, lamp, EquipmentItemInput(barcode=bc), user_id=None)
    assert service.backfill_quantity_barcodes(db, pa, commit=False) == 1
    assert service.backfill_quantity_barcodes(db, pb, commit=False) == 1
    db.commit()
    db.expire_all()
    assert len(_barcodes(pa) | _barcodes(pb)) == 2
