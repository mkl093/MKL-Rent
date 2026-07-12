"""Веб-маршруты настроек компании: загрузка логотипа (ТЗ §27)."""

import io
import re

import pytest
from PIL import Image

from app.auth import service as auth_service
from app.settings.service import get_company_settings


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


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path):
    """Перенаправить хранение изображений во временную папку."""

    class _S:
        storage_path = str(tmp_path)

    monkeypatch.setattr("app.utils.images.get_settings", lambda: _S())
    return tmp_path


def _png_bytes(color=(255, 0, 0, 128)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (12, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _csrf(client, url="/settings") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_save_logo_keeps_transparency(tmp_storage):
    from app.utils.images import save_logo

    rel = save_logo(_png_bytes())
    assert rel.startswith("logo/")
    saved = Image.open(tmp_storage / rel)
    assert saved.format == "PNG"
    assert saved.mode == "RGBA"


def test_logo_upload_and_removal(auth_client, db_session, tmp_storage):
    token = _csrf(auth_client)
    resp = auth_client.post(
        "/settings",
        data={"company_name": "MKL", "csrf_token": token},
        files={"logo": ("logo.png", _png_bytes(), "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    settings = get_company_settings(db_session)
    assert settings.logo_path and settings.logo_path.startswith("logo/")
    assert (tmp_storage / settings.logo_path).is_file()

    # Страница настроек показывает текущий логотип.
    page = auth_client.get("/settings").text
    assert f"/media/{settings.logo_path}" in page

    # Удаление логотипа.
    token = _csrf(auth_client)
    auth_client.post(
        "/settings",
        data={"company_name": "MKL", "remove_logo": "1", "csrf_token": token},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert get_company_settings(db_session).logo_path is None
