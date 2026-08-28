"""Аутентификация и управление гостевыми аккаунтами.

Логика ограничения попыток входа зеркалит app.auth.service (по аккаунту — своя
таблица, по IP — общая app.auth.models.IpLoginLock, см. app/guests/models.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import IpLoginLock
from app.auth.service import LOCKOUT_MINUTES_PER_IP, MAX_FAILED_ATTEMPTS_PER_IP
from app.database import as_utc, utcnow
from app.guests.models import GUEST_LEVEL_VIEW, GuestUser
from app.utils.security import hash_password, verify_password

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class GuestAuthError(Exception):
    """Базовая ошибка авторизации гостя."""


class GuestInvalidCredentials(GuestAuthError):
    """Неверный логин или пароль."""


class GuestAccountLocked(GuestAuthError):
    """Аккаунт временно заблокирован из-за попыток входа."""


class GuestAccountDisabled(GuestAuthError):
    """Аккаунт отключён администратором."""


class GuestIpRateLimited(GuestAuthError):
    """Превышен лимит попыток входа с этого IP-адреса."""


def get_guest_by_username(db: Session, username: str) -> GuestUser | None:
    return db.execute(select(GuestUser).where(GuestUser.username == username)).scalar_one_or_none()


def get_guest_by_id(db: Session, guest_id: int) -> GuestUser | None:
    return db.get(GuestUser, guest_id)


def list_guests(db: Session) -> list[GuestUser]:
    return list(db.execute(select(GuestUser).order_by(GuestUser.display_name)).scalars().all())


def create_guest(
    db: Session,
    username: str,
    password: str,
    display_name: str,
    access_level: int = GUEST_LEVEL_VIEW,
) -> GuestUser:
    guest = GuestUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        access_level=access_level,
    )
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return guest


def set_password(db: Session, guest: GuestUser, new_password: str) -> None:
    guest.password_hash = hash_password(new_password)
    guest.failed_login_count = 0
    guest.locked_until = None
    db.commit()


def set_access_level(db: Session, guest: GuestUser, access_level: int) -> None:
    guest.access_level = access_level
    db.commit()


def set_blocked(db: Session, guest: GuestUser, blocked: bool) -> None:
    guest.is_blocked = blocked
    db.commit()


def unlock_guest(db: Session, guest: GuestUser) -> None:
    guest.failed_login_count = 0
    guest.locked_until = None
    db.commit()


def delete_guest(db: Session, guest: GuestUser) -> None:
    db.delete(guest)
    db.commit()


def _get_or_create_ip_lock(db: Session, ip_address: str) -> IpLoginLock:
    lock = db.execute(
        select(IpLoginLock).where(IpLoginLock.ip_address == ip_address)
    ).scalar_one_or_none()
    if lock is None:
        lock = IpLoginLock(ip_address=ip_address, failed_count=0)
        db.add(lock)
    return lock


def _register_ip_failure(
    db: Session, ip_lock: IpLoginLock, now: datetime, attempted_username: str
) -> None:
    ip_lock.failed_count += 1
    if ip_lock.failed_count >= MAX_FAILED_ATTEMPTS_PER_IP:
        ip_lock.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES_PER_IP)
        ip_lock.failed_count = 0
        audit_log(
            db,
            None,
            EventType.AUTH_LOGIN_BLOCKED,
            f"IP {ip_lock.ip_address} заблокирован на {LOCKOUT_MINUTES_PER_IP} мин. "
            f"после {MAX_FAILED_ATTEMPTS_PER_IP} неудачных попыток входа "
            f"(гостевой вход, последний логин: «{attempted_username}»).",
        )
    else:
        db.commit()


def authenticate_guest(
    db: Session, username: str, password: str, ip_address: str | None = None
) -> GuestUser:
    """Проверить учётные данные гостя с учётом блокировок и rate-limit (см. app.auth.service)."""
    now = utcnow()

    ip_lock = _get_or_create_ip_lock(db, ip_address) if ip_address else None
    if ip_lock is not None:
        ip_locked_until = as_utc(ip_lock.locked_until)
        if ip_locked_until is not None and ip_locked_until > now:
            raise GuestIpRateLimited

    guest = get_guest_by_username(db, username)
    if guest is None:
        if ip_lock is not None:
            _register_ip_failure(db, ip_lock, now, username)
        raise GuestInvalidCredentials

    locked_until = as_utc(guest.locked_until)
    if locked_until is not None and locked_until > now:
        raise GuestAccountLocked

    if not guest.can_login:
        raise GuestAccountDisabled

    if not verify_password(password, guest.password_hash):
        guest.failed_login_count += 1
        if guest.failed_login_count >= MAX_FAILED_ATTEMPTS:
            guest.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            guest.failed_login_count = 0
        if ip_lock is not None:
            _register_ip_failure(db, ip_lock, now, username)
        else:
            db.commit()
        raise GuestInvalidCredentials

    guest.failed_login_count = 0
    guest.locked_until = None
    guest.last_login_at = now
    if ip_lock is not None:
        ip_lock.failed_count = 0
        ip_lock.locked_until = None
    db.commit()
    return guest
