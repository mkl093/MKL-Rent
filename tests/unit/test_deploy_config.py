"""Прод-конфигурация деплоя: allowed_hosts, TrustedHost за прокси, foreground-scheduler.

Инфраструктурные правила (ТЗ §35, §41.2):
- APP_ALLOWED_HOSTS парсится в список; пусто — проверка Host выключена (dev).
- При заданном APP_ALLOWED_HOSTS чужой Host отклоняется (400), localhost разрешён
  (healthcheck контейнера), домен из списка проходит.
- backup.scheduler.run_foreground при BACKUP_AUTO=false не блокирует (мгновенный возврат).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_allowed_hosts_parsing():
    assert Settings(app_allowed_hosts=" a.com , b.com ,").allowed_hosts == ["a.com", "b.com"]
    assert Settings(app_allowed_hosts="").allowed_hosts == []


def test_sqlalchemy_url_escapes_special_chars():
    # Пароль со спецсимволами (@ ! #) не должен ломать разбор DSN (хост остаётся db).
    s = Settings(
        database_user="rental",
        database_password="timer555!@#",
        database_host="db",
        database_port=5432,
        database_name="rental",
        database_url=None,
    )
    parsed = make_url(s.sqlalchemy_url)
    assert parsed.host == "db"
    assert parsed.port == 5432
    assert parsed.username == "rental"
    assert parsed.password == "timer555!@#"
    assert parsed.database == "rental"


def test_sqlalchemy_url_respects_explicit_override():
    s = Settings(database_url="sqlite:///./dev.db")
    assert s.sqlalchemy_url == "sqlite:///./dev.db"


def test_alembic_url_escapes_percent_for_configparser():
    # Как в migrations/env.py: значение уходит в configparser, где % — интерполяция.
    # Без экранирования percent-encoded пароль (%40, %21) бросает ValueError.
    from configparser import ConfigParser

    s = Settings(
        database_user="rental",
        database_password="MKL_@timer555!",
        database_host="db",
        database_port=5432,
        database_name="rental",
        database_url=None,
    )
    cp = ConfigParser()
    cp.add_section("alembic")
    cp.set("alembic", "sqlalchemy.url", s.alembic_url)  # не должно бросать
    # configparser вернёт одиночный %, make_url декодирует пароль обратно.
    assert make_url(cp.get("alembic", "sqlalchemy.url")).password == "MKL_@timer555!"


def test_session_secure_auto_follows_env():
    # "" — авто: Secure только в production.
    assert Settings(app_env="production", session_cookie_secure="").session_secure is True
    assert Settings(app_env="development", session_cookie_secure="").session_secure is False


def test_session_secure_explicit_override():
    # Явный false нужен, чтобы поднять прод по HTTP без поломки CSRF/входа.
    assert Settings(app_env="production", session_cookie_secure="false").session_secure is False
    assert Settings(app_env="development", session_cookie_secure="true").session_secure is True


def test_trustedhost_off_by_default(monkeypatch):
    monkeypatch.delenv("APP_ALLOWED_HOSTS", raising=False)
    get_settings.cache_clear()
    from app.main import create_app

    client = TestClient(create_app())
    # Проверка выключена — любой Host проходит.
    assert client.get("/healthz", headers={"host": "evil.example"}).status_code == 200


def test_trustedhost_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("APP_ALLOWED_HOSTS", "good.example")
    get_settings.cache_clear()
    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/healthz", headers={"host": "good.example"}).status_code == 200
    # localhost всегда разрешён — иначе сломался бы healthcheck контейнера.
    assert client.get("/healthz", headers={"host": "localhost"}).status_code == 200
    assert client.get("/healthz", headers={"host": "evil.example"}).status_code == 400


def test_run_foreground_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("BACKUP_AUTO", "false")
    get_settings.cache_clear()
    from app.backup import scheduler

    # Не должно зависнуть/запустить цикл при выключенном авто-backup.
    scheduler.run_foreground()
