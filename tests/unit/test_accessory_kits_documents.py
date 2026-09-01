"""Карнет и PDF-документы с комплектом аксессуаров (Карнет, Packing-лист, Транспорт-PDF)."""

from datetime import date
from decimal import Decimal

from app.accessory_kits import service as ak_service
from app.accessory_kits.schemas import AccessoryKitInput
from app.documents import builder
from app.documents.enums import DocumentType
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.packing import carnet
from app.packing import service as packing_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput


def _project_with_accessory_kit(db_session, *, weight_kg="0.5", quantity=4, country="Germany"):
    cat = cat_service.create_category(db_session, "Кабели")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="XLR 10м",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal(weight_kg),
            manufacturer="Sommer",
            country_of_origin=country,
        ),
    )
    project = proj_service.create_project(
        db_session, ProjectInput(name="Тур", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    kit = ak_service.create_kit(db_session, project, AccessoryKitInput(name="Кабелярка FOH"))
    ak_service.add_model_line(db_session, project, kit, model, quantity)
    est_service.add_accessory_kit_line(db_session, estimate, project, kit)
    packing = packing_service.create_from_estimate(db_session, project)
    return project, model, kit, packing


# --- Карнет ---------------------------------------------------------------


def test_carnet_expands_accessory_kit_content(db_session):
    project, model, kit, packing = _project_with_accessory_kit(db_session)
    rows = carnet.build_rows(db_session, packing)
    assert len(rows) == 1
    row = rows[0]
    assert "XLR 10м" in row.description
    assert "Sommer" in row.description
    assert "NSN" in row.description  # содержимое количественное, без штрих-кодов единиц
    assert row.quantity == 4
    assert row.weight_kg == Decimal("2.0")  # 4 × 0.5
    assert row.country_of_origin == "Germany"


def test_carnet_skips_accessory_kit_without_content(db_session):
    cat = cat_service.create_category(db_session, "Кабели")
    project = proj_service.create_project(
        db_session, ProjectInput(name="Тур", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    estimate = est_service.get_or_create_estimate(db_session, project)
    kit = ak_service.create_kit(db_session, project, AccessoryKitInput(name="Пустая кабелярка"))
    est_service.add_accessory_kit_line(db_session, estimate, project, kit)
    packing = packing_service.create_from_estimate(db_session, project)

    rows = carnet.build_rows(db_session, packing)
    assert rows == []


# --- Packing PDF (локализация группы и суффикса) --------------------------


def test_packing_pdf_accessory_kit_group_ru_en_de(db_session):
    project, model, kit, packing = _project_with_accessory_kit(db_session)

    html_ru, _ = builder.render_html(db_session, project, DocumentType.PACKING, "ru")
    assert "Комплекты аксессуаров" in html_ru
    assert "(комплект аксессуаров)" in html_ru
    assert "XLR 10м" in html_ru  # содержимое видно живьём и в PDF

    html_en, _ = builder.render_html(db_session, project, DocumentType.PACKING, "en")
    assert "Комплекты аксессуаров" not in html_en
    assert "Accessory kits" in html_en
    assert "(accessory kit)" in html_en

    html_de, _ = builder.render_html(db_session, project, DocumentType.PACKING, "de")
    assert "Zubehör-Sets" in html_de
    assert "(Zubehör-Set)" in html_de


# --- Estimate PDF (группа тоже локализуется) -------------------------------


def test_estimate_pdf_accessory_kit_group_localized(db_session):
    project, model, kit, packing = _project_with_accessory_kit(db_session)

    html_ru, _ = builder.render_html(db_session, project, DocumentType.ESTIMATE, "ru")
    assert "Комплекты аксессуаров" in html_ru
    assert "XLR 10м" not in html_ru  # содержимое в смете не показывается

    html_en, _ = builder.render_html(db_session, project, DocumentType.ESTIMATE, "en")
    assert "Accessory kits" in html_en


# --- Транспорт PDF (вложенная таблица содержимого по машине) --------------


def test_transport_pdf_shows_accessory_kit_content_per_vehicle(db_session):
    from app.transport import service as transport_service

    project, model, kit, packing = _project_with_accessory_kit(db_session)
    line = next(ln for ln in packing.lines if ln.accessory_kit_id == kit.id)

    van = transport_service.create_vehicle(
        db_session, name="Газель", plate_number=None, max_weight_kg=Decimal("1000"), comment=None
    )
    pv = transport_service.add_vehicle_to_project(db_session, project, van)
    transport_service.assign(db_session, project, pv, line, 1)

    html, fp = builder.render_html(db_session, project, DocumentType.TRANSPORT, "ru")
    assert "Кабелярка FOH" in html
    assert "(комплект аксессуаров)" in html
    assert "XLR 10м" in html  # содержимое раскрыто на листе машины
    assert fp

    # Fingerprint учитывает содержимое — правка кабелярки должна инвалидировать кэш.
    ak_service.add_model_line(db_session, project, kit, model, 2)
    _, fp2 = builder.render_html(db_session, project, DocumentType.TRANSPORT, "ru")
    assert fp2 != fp
