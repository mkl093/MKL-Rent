"""Вход персонала через веб-интерфейс (ТЗ §4)."""

import re


def _csrf(client, url="/login") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_login_empty_fields_does_not_crash(client, db_session):
    """Пустые логин/пароль раньше приводили к 422 от FastAPI вместо страницы с ошибкой."""
    token = _csrf(client)
    resp = client.post("/login", data={"username": "", "password": "", "csrf_token": token})
    assert resp.status_code == 200
    assert "Введите логин и пароль" in resp.text


def test_login_missing_fields_does_not_crash(client, db_session):
    token = _csrf(client)
    resp = client.post("/login", data={"csrf_token": token})
    assert resp.status_code == 200
