"""Резервное копирование и восстановление (ТЗ §36)."""

import re
import sqlite3

import pytest

from app.auth import service as auth_service
from app.backup import service


class _FakeSettings:
    def __init__(self, db_path, storage, backups):
        self.sqlalchemy_url = f"sqlite:///{db_path}"
        self.storage_path = str(storage)
        self.backup_path = str(backups)
        self.backup_retention_days = 14
        # для ветки postgres (не используется в sqlite-тесте)
        self.database_password = ""
        self.database_host = ""
        self.database_port = 0
        self.database_user = ""
        self.database_name = ""


def test_backup_restore_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "data.db"
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "logo.txt").write_text("LOGO", encoding="utf-8")
    backups = tmp_path / "backups"

    fake = _FakeSettings(db_path, storage, backups)
    monkeypatch.setattr("app.backup.service.get_settings", lambda: fake)

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()

    archive = service.create_backup()
    assert archive.exists()
    assert len(service.list_backups()) == 1

    # Портим данные и удаляем файл
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM t")
    con.commit()
    con.close()
    (storage / "logo.txt").unlink()

    # Восстанавливаем
    manifest = service.restore_backup(archive)
    assert manifest["engine"] == "sqlite"

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT x FROM t").fetchall()
    con.close()
    assert rows == [(42,)]
    assert (storage / "logo.txt").read_text(encoding="utf-8") == "LOGO"


def test_retention_removes_old(tmp_path, monkeypatch):
    import os
    import time

    fake = _FakeSettings(tmp_path / "d.db", tmp_path / "s", tmp_path / "b")
    (tmp_path / "b").mkdir()
    fake.backup_retention_days = 1
    monkeypatch.setattr("app.backup.service.get_settings", lambda: fake)

    old = tmp_path / "b" / "backup_20200101_000000.tar.gz"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    new = tmp_path / "b" / "backup_20990101_000000.tar.gz"
    new.write_bytes(b"x")

    assert service.apply_retention() == 1
    assert not old.exists() and new.exists()


def test_get_backup_rejects_traversal(tmp_path, monkeypatch):
    fake = _FakeSettings(tmp_path / "d.db", tmp_path / "s", tmp_path / "b")
    monkeypatch.setattr("app.backup.service.get_settings", lambda: fake)
    assert service.get_backup("../secret") is None
    assert service.get_backup("evil.tar.gz") is None  # не начинается с backup_


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


def test_backup_page_renders(auth_client):
    page = auth_client.get("/backup")
    assert page.status_code == 200
    assert "Резервные копии" in page.text
    assert "Восстановление" in page.text
