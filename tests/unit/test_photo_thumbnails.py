"""Миниатюры фото для списков и кеширование /media, /static (ТЗ §11, §25)."""

import io
import re
from decimal import Decimal

import pytest
from PIL import Image

from app.auth import service as auth_service
from app.config import get_settings
from app.inventory.enums import AccountingType
from app.inventory.schemas import EquipmentModelCreate
from app.inventory.services import categories as cat_service
from app.inventory.services import equipment as eq_service
from app.utils import images
from app.utils.images import (
    THUMB_SIZE,
    delete_photo,
    is_model_photo_path,
    make_thumbnail,
    save_model_photo,
    thumb_rel_path,
)

IMMUTABLE = "private, max-age=31536000, immutable"


def _jpeg(width=1600, height=900, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def storage(monkeypatch, tmp_path):
    # Реальный объект настроек, но с временным каталогом файлов.
    monkeypatch.setattr(get_settings(), "storage_path", str(tmp_path))
    return tmp_path


@pytest.fixture
def auth_client(storage, client, db_session):
    auth_service.create_user(db_session, "admin", "pass123")
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "pass123", "csrf_token": token},
        follow_redirects=False,
    )
    return client


# --- Сервис -----------------------------------------------------------------


def test_thumb_rel_path_and_photo_path_check():
    assert thumb_rel_path("models/abc.jpg") == "models/abc.thumb.jpg"
    assert is_model_photo_path("models/abc.jpg")
    assert not is_model_photo_path("models/abc.thumb.jpg")
    assert not is_model_photo_path("logo/abc.png")
    assert not is_model_photo_path("models/../x.jpg")
    assert not is_model_photo_path("models/..\\..\\x.jpg")
    assert not is_model_photo_path("documents/project_1/a.pdf")
    assert not is_model_photo_path(None)


def test_upload_creates_square_thumbnail(storage):
    rel = save_model_photo(_jpeg())
    thumb = storage / thumb_rel_path(rel)
    assert thumb.is_file()
    with Image.open(thumb) as img:
        assert img.size == (THUMB_SIZE, THUMB_SIZE)
    assert thumb.stat().st_size < (storage / rel).stat().st_size


def test_make_thumbnail_for_old_photo_and_idempotent(storage):
    rel = save_model_photo(_jpeg())
    thumb = storage / thumb_rel_path(rel)
    thumb.unlink()  # как у фото, загруженного до появления миниатюр
    assert make_thumbnail(rel) == thumb
    mtime = thumb.stat().st_mtime_ns
    assert make_thumbnail(rel) == thumb
    assert thumb.stat().st_mtime_ns == mtime  # без force не пересоздаётся


def test_make_thumbnail_missing_or_broken_source(storage):
    assert make_thumbnail("models/nope.jpg") is None
    (storage / "models").mkdir(exist_ok=True)
    (storage / "models" / "broken.jpg").write_bytes(b"not an image")
    assert make_thumbnail("models/broken.jpg") is None
    assert not (storage / "models" / "broken.thumb.jpg").exists()


def test_delete_photo_removes_thumbnail(storage):
    rel = save_model_photo(_jpeg())
    delete_photo(rel)
    assert not (storage / rel).exists()
    assert not (storage / thumb_rel_path(rel)).exists()


def test_upload_survives_thumbnail_write_error(storage, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(images, "_write_thumbnail", boom)
    rel = save_model_photo(_jpeg())
    assert (storage / rel).is_file()


# --- Маршруты ---------------------------------------------------------------


def test_thumb_route_requires_login(storage, client):
    resp = client.get("/media/thumb/models/a.jpg", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_thumb_route_generates_and_caches(auth_client, storage):
    rel = save_model_photo(_jpeg())
    (storage / thumb_rel_path(rel)).unlink()
    resp = auth_client.get(f"/media/thumb/{rel}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == IMMUTABLE
    with Image.open(io.BytesIO(resp.content)) as img:
        assert img.size == (THUMB_SIZE, THUMB_SIZE)
    assert (storage / thumb_rel_path(rel)).is_file()


def test_thumb_route_rejects_other_paths(auth_client, storage):
    (storage / "documents").mkdir()
    (storage / "documents" / "a.jpg").write_bytes(_jpeg(10, 10))
    assert auth_client.get("/media/thumb/documents/a.jpg").status_code == 404
    assert auth_client.get("/media/thumb/models/../documents/a.jpg").status_code == 404
    assert auth_client.get("/media/thumb/models/missing.jpg").status_code == 404


def test_thumb_route_falls_back_to_original(auth_client, storage, monkeypatch):
    rel = save_model_photo(_jpeg())
    (storage / thumb_rel_path(rel)).unlink()
    monkeypatch.setattr("app.main.make_thumbnail", lambda _p: None)
    resp = auth_client.get(f"/media/thumb/{rel}")
    assert resp.status_code == 200
    assert resp.content == (storage / rel).read_bytes()


def test_media_cache_headers_by_prefix(auth_client, storage):
    rel = save_model_photo(_jpeg())
    assert auth_client.get(f"/media/{rel}").headers["cache-control"] == IMMUTABLE
    (storage / "documents").mkdir()
    (storage / "documents" / "a.pdf").write_bytes(b"%PDF-1.4")
    doc = auth_client.get("/media/documents/a.pdf")
    assert doc.status_code == 200
    assert doc.headers["cache-control"] == "private, no-cache"


def test_static_versioned_is_immutable(client):
    versioned = client.get("/static/css/app.css?v=1")
    assert versioned.status_code == 200
    assert "immutable" in versioned.headers["cache-control"]
    plain = client.get("/static/css/app.css")
    assert "cache-control" not in plain.headers


def test_inventory_list_uses_lazy_thumbnails(auth_client, db_session, storage):
    cat = cat_service.create_category(db_session, "Свет")
    model = eq_service.create_model(
        db_session,
        EquipmentModelCreate(
            category_id=cat.id,
            name="Beam",
            accounting_type=AccountingType.SERIAL,
            base_price_eur=Decimal("0"),
        ),
    )
    model.photo_path = save_model_photo(_jpeg())
    db_session.commit()
    body = auth_client.get("/inventory?view=list").text
    imgs = re.findall(r"<img [^>]*>", body)
    photo_imgs = [tag for tag in imgs if model.photo_path in tag]
    assert len(photo_imgs) == 2  # таблица (десктоп) и карточки (мобильный)
    for tag in photo_imgs:
        assert f'src="/media/thumb/{model.photo_path}"' in tag
        assert 'loading="lazy"' in tag
