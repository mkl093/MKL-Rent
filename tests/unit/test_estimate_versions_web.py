"""Веб-маршруты стендов и версий смет."""

import re
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.estimates import versions
from app.estimates.models import Estimate, ProjectSection
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.projects.models import Project


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
def model(db_session):
    cat = cat_service.create_category(db_session, "Звук")
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Колонка",
            accounting_type=AccountingType.QUANTITY,
            total_quantity=20,
            base_price_eur=Decimal("100.00"),
        ),
    )


@pytest.fixture
def pid(auth_client):
    token = _csrf(auth_client, "/projects/new")
    resp = auth_client.post(
        "/projects",
        data={
            "name": "Выставка",
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
            "rental_coefficient": "1",
            "vat": "19",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    return int(resp.headers["location"].rsplit("/", 1)[-1])


def _create_section(client, pid, name) -> str:
    token = _csrf(client, f"/projects/{pid}")
    resp = client.post(
        f"/projects/{pid}/sections/create",
        data={"name": name, "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.status_code  # не 422: статический путь не перехвачен
    return resp.headers["location"]


def _add_model(client, url, model, qty):
    token = _csrf(client, f"{url}/add")
    client.post(
        f"{url}/add",
        data={f"select_{model.id}": "1", f"qty_{model.id}": str(qty), "csrf_token": token},
        follow_redirects=False,
    )


def test_sections_on_project_page_and_legacy_redirect(auth_client, db_session, model, pid):
    a_url = _create_section(auth_client, pid, "Стенд A")
    assert a_url.endswith("/estimate/v/1")
    # Один стенд — /estimate ведёт прямо в смету.
    assert auth_client.get(f"/projects/{pid}/estimate").url.path == a_url

    b_url = _create_section(auth_client, pid, "Стенд B")
    _add_model(auth_client, a_url, model, 3)
    _add_model(auth_client, b_url, model, 2)

    page = auth_client.get(f"/projects/{pid}").text
    assert "Стенд A" in page and "Стенд B" in page
    assert "Всего по проекту" in page
    # Несколько стендов — /estimate ведёт на карточку проекта.
    resp = auth_client.get(f"/projects/{pid}/estimate", follow_redirects=False)
    assert resp.headers["location"] == f"/projects/{pid}#sections"

    project = db_session.get(Project, pid)
    db_session.refresh(project)
    assert {r.model_id: r.quantity for r in project.reservations} == {model.id: 5}


def test_version_create_activate_lock_flow(auth_client, db_session, model, pid):
    v1_url = _create_section(auth_client, pid, "Стенд A")
    _add_model(auth_client, v1_url, model, 3)
    section_url = v1_url.rsplit("/estimate/", 1)[0]

    # Новая версия — копия v1, неактивная.
    token = _csrf(auth_client, v1_url)
    resp = auth_client.post(
        f"{section_url}/estimate/versions/create",
        data={"source_version_no": "1", "label": "после созвона", "csrf_token": token},
        follow_redirects=False,
    )
    v2_url = resp.headers["location"]
    assert v2_url.endswith("/estimate/v/2")
    page = auth_client.get(v2_url).text
    assert "Неактивная" in page and "после созвона" in page
    assert "не влияет на бронь" in page

    # Правим v2 (бронь не меняется), активируем через окно версий.
    _add_model(auth_client, v2_url, model, 4)  # merge → 7
    token = _csrf(auth_client, v2_url)
    auth_client.post(
        f"{section_url}/estimate/versions/activate",
        data={"version_no": "2", "csrf_token": token},
        follow_redirects=False,
    )
    project = db_session.get(Project, pid)
    db_session.refresh(project)
    assert {r.model_id: r.quantity for r in project.reservations} == {model.id: 7}
    assert "Активная" in auth_client.get(v2_url).text

    # Заморозка: правки отклоняются.
    token = _csrf(auth_client, v2_url)
    auth_client.post(f"{v2_url}/lock", data={"csrf_token": token}, follow_redirects=False)
    v2 = db_session.query(Estimate).filter_by(version_no=2).one()
    db_session.refresh(v2)
    assert v2.is_locked
    page = auth_client.get(v2_url).text
    assert "Версия заморожена" in page
    assert f"{v2_url}/add" not in page  # кнопка добавления скрыта

    line = v2.lines[0]
    token = _csrf(auth_client, v2_url)
    auth_client.post(
        f"{v2_url}/lines/{line.id}",
        data={"quantity": "99", "unit_price": "100", "coefficient": "1", "csrf_token": token},
        follow_redirects=False,
    )
    db_session.refresh(line)
    assert line.quantity == 7
    assert auth_client.get(f"{v2_url}/add", follow_redirects=False).status_code == 303

    # Разморозка.
    token = _csrf(auth_client, v2_url)
    auth_client.post(f"{v2_url}/unlock", data={"csrf_token": token}, follow_redirects=False)
    db_session.refresh(v2)
    assert not v2.is_locked


def test_rename_and_delete_section(auth_client, db_session, pid):
    a_url = _create_section(auth_client, pid, "A")
    _create_section(auth_client, pid, "B")
    section_a = db_session.query(ProjectSection).filter_by(name="A").one()
    section_b = db_session.query(ProjectSection).filter_by(name="B").one()

    token = _csrf(auth_client, a_url)
    auth_client.post(
        f"/projects/{pid}/sections/{section_a.id}/rename",
        data={"name": "Hall 3", "back": a_url, "csrf_token": token},
        follow_redirects=False,
    )
    db_session.refresh(section_a)
    assert section_a.name == "Hall 3"

    token = _csrf(auth_client, f"/projects/{pid}")
    auth_client.post(
        f"/projects/{pid}/sections/{section_b.id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    project = db_session.get(Project, pid)
    assert [s.name for s in versions.list_sections(db_session, project)] == ["Hall 3"]


def test_generate_pdf_of_version(auth_client, db_session, model, pid, tmp_path, monkeypatch):
    from app.documents import builder

    monkeypatch.setattr(builder, "_html_to_pdf", lambda html: b"%PDF-1.4 test")
    monkeypatch.setattr(builder, "_storage_dir", lambda project_id: tmp_path)

    v1_url = _create_section(auth_client, pid, "Стенд A")
    _add_model(auth_client, v1_url, model, 1)
    estimate = db_session.query(Estimate).filter_by(project_id=pid).one()

    token = _csrf(auth_client, v1_url)
    resp = auth_client.post(
        f"/projects/{pid}/documents/generate",
        data={
            "doc_type": "estimate",
            "language": "en",
            "estimate_id": str(estimate.id),
            "back": v1_url,
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert resp.headers["location"] == v1_url
    assert (tmp_path / f"estimate_{estimate.id}_en.pdf").exists()

    docs = auth_client.get(f"/projects/{pid}/documents").text
    assert "Стенд A" in docs
    assert f"/documents/estimate/{estimate.id}/en/view" in docs

    # «back» вне проекта игнорируется
    token = _csrf(auth_client, v1_url)
    resp = auth_client.post(
        f"/projects/{pid}/documents/generate",
        data={
            "doc_type": "estimate",
            "language": "ru",
            "estimate_id": str(estimate.id),
            "back": "https://evil.example/",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert resp.headers["location"] == f"/projects/{pid}/documents"
