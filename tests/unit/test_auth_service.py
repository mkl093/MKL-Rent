"""Авторизация и ограничение попыток входа (ТЗ §4, §41.2)."""

import pytest

from app.audit.service import list_entries
from app.auth import service
from app.auth.service import MAX_FAILED_ATTEMPTS, MAX_FAILED_ATTEMPTS_PER_IP


def test_authenticate_success(db_session):
    service.create_user(db_session, "admin", "pass123")
    user = service.authenticate(db_session, "admin", "pass123")
    assert user.username == "admin"
    assert user.last_login_at is not None


def test_authenticate_wrong_password(db_session):
    service.create_user(db_session, "admin", "pass123")
    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db_session, "admin", "nope")


def test_authenticate_unknown_user(db_session):
    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db_session, "ghost", "x")


def test_lockout_after_max_attempts(db_session):
    service.create_user(db_session, "admin", "pass123")
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(service.InvalidCredentials):
            service.authenticate(db_session, "admin", "wrong")
    # Теперь учётка заблокирована rate-limit'ом даже с верным паролем.
    with pytest.raises(service.AccountLocked):
        service.authenticate(db_session, "admin", "pass123")


def test_blocked_account(db_session):
    user = service.create_user(db_session, "admin", "pass123")
    service.set_blocked(db_session, user, True)
    with pytest.raises(service.AccountDisabled):
        service.authenticate(db_session, "admin", "pass123")


def test_ip_lockout_across_different_usernames(db_session):
    """Перебор паролей по разным (в т.ч. несуществующим) логинам с одного IP тоже лимитируется."""
    service.create_user(db_session, "admin", "pass123")
    for i in range(MAX_FAILED_ATTEMPTS_PER_IP):
        with pytest.raises(service.InvalidCredentials):
            service.authenticate(db_session, f"ghost{i}", "wrong", ip_address="1.2.3.4")
    # IP заблокирован, даже верный логин/пароль не проходит.
    with pytest.raises(service.IpRateLimited):
        service.authenticate(db_session, "admin", "pass123", ip_address="1.2.3.4")
    # Другой IP не затронут.
    user = service.authenticate(db_session, "admin", "pass123", ip_address="9.9.9.9")
    assert user.username == "admin"


def test_ip_lockout_logs_audit_event(db_session):
    for i in range(MAX_FAILED_ATTEMPTS_PER_IP):
        with pytest.raises(service.InvalidCredentials):
            service.authenticate(db_session, f"ghost{i}", "wrong", ip_address="5.5.5.5")
    entries = list_entries(db_session, event_type="auth_login_blocked")
    assert len(entries) == 1
    assert "5.5.5.5" in entries[0].description


def test_ip_failures_reset_on_success(db_session):
    service.create_user(db_session, "admin", "pass123")
    for _ in range(MAX_FAILED_ATTEMPTS_PER_IP - 1):
        with pytest.raises(service.InvalidCredentials):
            service.authenticate(db_session, "admin", "wrong-once", ip_address="8.8.8.8")
        # Разблокируем аккаунт, чтобы проверять именно IP-счётчик, а не account-lockout.
        service.unlock_user(db_session, service.get_user_by_username(db_session, "admin"))
    service.authenticate(db_session, "admin", "pass123", ip_address="8.8.8.8")
    # Счётчик IP сброшен успешным входом — новая серия неудач не блокирует сразу.
    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db_session, "admin", "wrong", ip_address="8.8.8.8")
