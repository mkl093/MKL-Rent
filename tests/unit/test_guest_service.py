"""Аутентификация и блокировки гостевых аккаунтов (зеркалит test_auth_service.py)."""

import pytest

from app.guests import service
from app.guests.models import GUEST_LEVEL_AVAILABILITY, GUEST_LEVEL_VIEW


def test_authenticate_success(db_session):
    service.create_guest(db_session, "acme", "pass123", "Acme Events")
    guest = service.authenticate_guest(db_session, "acme", "pass123")
    assert guest.username == "acme"
    assert guest.access_level == GUEST_LEVEL_VIEW
    assert guest.last_login_at is not None


def test_authenticate_wrong_password(db_session):
    service.create_guest(db_session, "acme", "pass123", "Acme Events")
    with pytest.raises(service.GuestInvalidCredentials):
        service.authenticate_guest(db_session, "acme", "nope")


def test_authenticate_unknown_user(db_session):
    with pytest.raises(service.GuestInvalidCredentials):
        service.authenticate_guest(db_session, "ghost", "x")


def test_lockout_after_max_attempts(db_session):
    service.create_guest(db_session, "acme", "pass123", "Acme Events")
    for _ in range(service.MAX_FAILED_ATTEMPTS):
        with pytest.raises(service.GuestInvalidCredentials):
            service.authenticate_guest(db_session, "acme", "wrong")
    with pytest.raises(service.GuestAccountLocked):
        service.authenticate_guest(db_session, "acme", "pass123")


def test_blocked_account(db_session):
    guest = service.create_guest(db_session, "acme", "pass123", "Acme Events")
    service.set_blocked(db_session, guest, True)
    with pytest.raises(service.GuestAccountDisabled):
        service.authenticate_guest(db_session, "acme", "pass123")


def test_access_level_is_cumulative_flag_only(db_session):
    guest = service.create_guest(
        db_session, "acme", "pass123", "Acme Events", GUEST_LEVEL_AVAILABILITY
    )
    assert guest.access_level == GUEST_LEVEL_AVAILABILITY


def test_ip_lockout_shared_with_staff_login(db_session):
    """IP-блокировка общая с персоналом (app.auth.models.IpLoginLock) — перебор
    через гостевую форму тоже должен блокировать IP для входа персонала."""
    from app.auth import service as auth_service

    auth_service.create_user(db_session, "admin", "pass123")
    for i in range(service.MAX_FAILED_ATTEMPTS_PER_IP):
        with pytest.raises(service.GuestInvalidCredentials):
            service.authenticate_guest(db_session, f"ghost{i}", "wrong", ip_address="3.3.3.3")
    with pytest.raises(auth_service.IpRateLimited):
        auth_service.authenticate(db_session, "admin", "pass123", ip_address="3.3.3.3")
