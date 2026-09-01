"""Веб-маршруты комплекта аксессуаров: вкладка проекта и шаблоны."""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.estimates import service as est_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects import service as proj_service
from app.projects.schemas import ProjectInput


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


@pytest.fixture
def project_and_model(db_session):
    cat = cat_service.create_category(db_session, "Кабели")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="XLR 10м",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=50,
            weight_kg=Decimal("0.5"),
        ),
    )
    project = proj_service.create_project(
        db_session, ProjectInput(name="Тур", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))
    )
    return project, model


def test_create_kit_and_view_list(auth_client, project_and_model):
    project, model = project_and_model
    base = f"/projects/{project.id}/accessory-kits"
    token = _csrf(auth_client, base)
    resp = auth_client.post(base, data={"name": "Кабелярка FOH", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303

    page = auth_client.get(base).text
    assert "Кабелярка FOH" in page


def test_kit_detail_add_custom_and_stock_line(auth_client, db_session, project_and_model):
    project, model = project_and_model
    base = f"/projects/{project.id}/accessory-kits"
    token = _csrf(auth_client, base)
    auth_client.post(base, data={"name": "Кабелярка", "csrf_token": token}, follow_redirects=False)
    from app.accessory_kits import service as ak_service

    kit = ak_service.list_kits(db_session, project)[0]
    detail_url = f"{base}/{kit.id}"

    token = _csrf(auth_client, detail_url)
    auth_client.post(
        f"{detail_url}/custom",
        data={"name": "Скотч", "quantity": "2", "unit_weight_kg": "0.3", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(detail_url).text
    assert "Скотч" in page

    token = _csrf(auth_client, f"{detail_url}?q=XLR")
    resp = auth_client.post(
        f"{detail_url}/add",
        data={f"select_{model.id}": "1", f"qty_{model.id}": "4", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = auth_client.get(detail_url).text
    assert "XLR 10м" in page
    assert "Вес содержимого" in page


def test_add_to_estimate_shows_single_line_without_content(auth_client, db_session, project_and_model):
    project, model = project_and_model
    base = f"/projects/{project.id}/accessory-kits"
    token = _csrf(auth_client, base)
    auth_client.post(base, data={"name": "Кабелярка", "csrf_token": token}, follow_redirects=False)
    from app.accessory_kits import service as ak_service

    kit = ak_service.list_kits(db_session, project)[0]
    ak_service.add_model_line(db_session, project, kit, model, 3)
    detail_url = f"{base}/{kit.id}"

    token = _csrf(auth_client, detail_url)
    resp = auth_client.post(f"{detail_url}/add-to-estimate", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303

    estimate_page = auth_client.get(f"/projects/{project.id}/estimate").text
    assert "Кабелярка" in estimate_page
    assert "XLR 10м" not in estimate_page  # содержимое в смете не показывается


def test_packing_page_renders_accessory_kit_line(auth_client, db_session, project_and_model):
    project, model = project_and_model
    base = f"/projects/{project.id}/accessory-kits"
    token = _csrf(auth_client, base)
    auth_client.post(base, data={"name": "Кабелярка FOH", "csrf_token": token}, follow_redirects=False)
    from app.accessory_kits import service as ak_service

    kit = ak_service.list_kits(db_session, project)[0]
    ak_service.add_model_line(db_session, project, kit, model, 4)

    estimate = est_service.get_or_create_estimate(db_session, project)
    est_service.add_accessory_kit_line(db_session, estimate, project, kit)

    token = _csrf(auth_client, f"/projects/{project.id}/packing")
    auth_client.post(f"/projects/{project.id}/packing/create", data={"csrf_token": token}, follow_redirects=False)

    page = auth_client.get(f"/projects/{project.id}/packing")
    assert page.status_code == 200
    assert "Кабелярка FOH" in page.text
    assert "XLR 10м" in page.text  # содержимое видно живьём в packing-листе


def test_template_crud_and_create_kit_from_template(auth_client, db_session, project_and_model):
    project, model = project_and_model
    token = _csrf(auth_client, "/accessory-kit-templates")
    auth_client.post(
        "/accessory-kit-templates", data={"name": "FOH", "csrf_token": token}, follow_redirects=False
    )
    from app.accessory_kits import service as ak_service

    template = ak_service.list_templates(db_session)[0]
    tpl_url = f"/accessory-kit-templates/{template.id}"

    token = _csrf(auth_client, tpl_url)
    auth_client.post(
        f"{tpl_url}/add",
        data={f"select_{model.id}": "1", f"qty_{model.id}": "5", "csrf_token": token},
        follow_redirects=False,
    )
    page = auth_client.get(tpl_url).text
    assert "XLR 10м" in page

    base = f"/projects/{project.id}/accessory-kits"
    token = _csrf(auth_client, base)
    resp = auth_client.post(
        base,
        data={"name": "Кабелярка из шаблона", "template_id": str(template.id), "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    kit = ak_service.list_kits(db_session, project)[0]
    assert len(kit.lines) == 1
    assert kit.lines[0].quantity == 5
