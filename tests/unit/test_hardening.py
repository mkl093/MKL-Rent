"""Hardening: доступ к приватным файлам и security-заголовки (ТЗ §41.2)."""

import re
from pathlib import Path

import pytest

from app.auth import service as auth_service
from app.config import get_settings


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


def test_media_requires_login(client):
    """Фото/файлы недоступны без авторизации (ТЗ §41.2)."""
    resp = client.get("/media/models/anything.jpg", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_media_serves_for_authenticated(auth_client):
    storage = Path(get_settings().storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    probe = storage / "_probe.txt"
    probe.write_text("ok", encoding="utf-8")
    try:
        resp = auth_client.get("/media/_probe.txt")
        assert resp.status_code == 200
        assert resp.text == "ok"
    finally:
        probe.unlink(missing_ok=True)


def test_media_blocks_path_traversal(auth_client):
    resp = auth_client.get("/media/../app/config.py", follow_redirects=False)
    assert resp.status_code == 404


def test_security_headers_present(client):
    headers = client.get("/login").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "same-origin"
