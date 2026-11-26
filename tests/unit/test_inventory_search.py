"""Подсказки поиска по складу и поиск списка по штрих-коду (ТЗ §25)."""

import re
from decimal import Decimal

import pytest

from app.auth import service as auth_service
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentItemInput, EquipmentModelCreate, KitInput
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.inventory.services import items as item_service
from app.inventory.services import kits as kit_service
from app.inventory.services.search import SUGGEST_LIMIT, escape_like, highlight, suggest


@pytest.fixture
def cat(db_session):
    return cat_service.create_category(db_session, "Свет")


def _model(db_session, cat, name, manufacturer=None):
    return eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name=name,
            manufacturer=manufacturer,
            accounting_type=AccountingType.SERIAL,
            base_price_eur=Decimal("0"),
        ),
    )


def _item(db_session, model, barcode, serial=None):
    return item_service.create_item(
        db_session, model, EquipmentItemInput(barcode=barcode, serial_number=serial), None
    )


# --- Сервис -----------------------------------------------------------------


def test_short_query_returns_none(db_session, cat):
    _model(db_session, cat, "Beam 230")
    assert suggest(db_session, "B") is None
    assert suggest(db_session, "  ") is None
    assert suggest(db_session, None) is None


def test_models_by_name_and_manufacturer_prefix_first(db_session, cat):
    _model(db_session, cat, "Super Beam", manufacturer="Robe")
    _model(db_session, cat, "Beam 230", manufacturer="Clay Paky")
    _model(db_session, cat, "Wash 300", manufacturer="Beamtech")
    s = suggest(db_session, "beam")
    names = [row.model.name for row in s.models]
    # Совпадение с начала названия — первым; производитель тоже ищется.
    assert names[0] == "Beam 230"
    assert set(names) == {"Beam 230", "Super Beam", "Wash 300"}


def test_archived_model_and_kit_are_skipped(db_session, cat):
    m = _model(db_session, cat, "Beam Old")
    eq_service.archive_model(db_session, m)
    kit = kit_service.create_kit(db_session, KitInput(name="Beam Case"))
    kit.is_archived = True
    db_session.commit()
    s = suggest(db_session, "beam")
    assert s.models == [] and s.kits == []
    assert s.is_empty


def test_kits_by_name(db_session):
    kit_service.create_kit(db_session, KitInput(name="Кейс микрофонов"))
    s = suggest(db_session, "Кейс")
    assert [k.name for k in s.kits] == ["Кейс микрофонов"]


def test_items_by_barcode_prefix_ignoring_case_and_spaces(db_session, cat):
    m = _model(db_session, cat, "Beam 230")
    _item(db_session, m, "MKL 00012")
    _item(db_session, m, "XYZ-1")
    s = suggest(db_session, "mkl0001")
    assert [i.barcode for i in s.items] == ["MKL 00012"]
    # Совпадение в середине штрих-кода не подсказывается (только начало).
    assert suggest(db_session, "0012").items == []


def test_items_by_serial_number(db_session, cat):
    m = _model(db_session, cat, "Beam 230")
    _item(db_session, m, "A-1", serial="SN-77810")
    assert [i.serial_number for i in suggest(db_session, "sn-778").items] == ["SN-77810"]


def test_limit_per_group(db_session, cat):
    for i in range(SUGGEST_LIMIT + 3):
        _model(db_session, cat, f"Par {i:02d}")
    assert len(suggest(db_session, "Par").models) == SUGGEST_LIMIT


def test_like_wildcards_are_literal(db_session, cat):
    _model(db_session, cat, "Beam 230")
    _model(db_session, cat, "50% Dimmer")
    assert suggest(db_session, "%%").is_empty
    assert suggest(db_session, "B_am").models == []
    assert [r.model.name for r in suggest(db_session, "50%").models] == ["50% Dimmer"]


def test_escape_like():
    assert escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


def test_highlight():
    assert highlight("Super Beam", "beam") == ("Super ", "Beam", "")
    assert highlight("Wash", "beam") == ("Wash", "", "")
    assert highlight(None, "x") == ("", "", "")


def test_stock_shown_for_models(db_session, cat):
    m = _model(db_session, cat, "Beam 230")
    _item(db_session, m, "B-1")
    _item(db_session, m, "B-2")
    assert suggest(db_session, "Beam").models[0].stock == 2


# --- Список склада ищет и по штрих-коду/серийному номеру --------------------


def test_list_models_finds_model_by_item_barcode(db_session, cat):
    m = _model(db_session, cat, "Beam 230")
    _model(db_session, cat, "Wash 300")
    _item(db_session, m, "MKL 00012", serial="SN-1")
    by_code = eq_service.list_models(db_session, eq_service.ModelFilters(query="mkl00012"))
    by_serial = eq_service.list_models(db_session, eq_service.ModelFilters(query="SN-1"))
    assert [x.name for x in by_code] == ["Beam 230"]
    assert [x.name for x in by_serial] == ["Beam 230"]


def test_list_models_name_search_still_works(db_session, cat):
    _model(db_session, cat, "Beam 230", manufacturer="Clay Paky")
    _model(db_session, cat, "Wash 300")
    found = eq_service.list_models(db_session, eq_service.ModelFilters(query="clay"))
    assert [x.name for x in found] == ["Beam 230"]


# --- Маршрут ----------------------------------------------------------------


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


def test_suggest_requires_login(client):
    resp = client.get("/inventory/search/suggest?q=beam", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_suggest_htmx_without_login_redirects_whole_page(client):
    resp = client.get(
        "/inventory/search/suggest?q=beam",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/login"
    assert resp.text == ""


def test_suggest_renders_groups_and_links(auth_client, db_session, cat):
    m = _model(db_session, cat, "Beam 230")
    item = _item(db_session, m, "BEAM-01")
    kit = kit_service.create_kit(db_session, KitInput(name="Beam Case"))
    body = auth_client.get("/inventory/search/suggest?q=beam").text
    assert f'href="/inventory/models/{m.id}"' in body
    assert f'href="/inventory/kits/{kit.id}"' in body
    assert f'href="/inventory/items/{item.id}"' in body
    assert "<mark" in body
    assert "Показать все результаты" in body


def test_suggest_short_query_is_empty(auth_client):
    resp = auth_client.get("/inventory/search/suggest?q=b")
    assert resp.status_code == 200
    assert resp.text == ""


def test_suggest_nothing_found(auth_client):
    assert "Ничего не найдено" in auth_client.get("/inventory/search/suggest?q=zzzz").text


def test_suggest_escapes_names(auth_client, db_session, cat):
    _model(db_session, cat, "<b>Beam</b> 230")
    body = auth_client.get("/inventory/search/suggest?q=beam").text
    assert "<b>Beam</b>" not in body
    assert "&lt;b&gt;" in body


def test_inventory_page_has_suggest_field(auth_client):
    body = auth_client.get("/inventory").text
    assert 'hx-get="/inventory/search/suggest"' in body
    assert 'autocomplete="off"' in body
    assert "js/inventory-search.js" in body
