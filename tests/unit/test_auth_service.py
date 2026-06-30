"""Авторизация и ограничение попыток входа (ТЗ §4, §41.2)."""

import pytest

from app.auth import service
from app.auth.service import MAX_FAILED_ATTEMPTS


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
