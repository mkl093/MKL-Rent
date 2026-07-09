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

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_allowed_hosts_parsing():
    assert Settings(app_allowed_hosts=" a.com , b.com ,").allowed_hosts == ["a.com", "b.com"]
    assert Settings(app_allowed_hosts="").allowed_hosts == []


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
