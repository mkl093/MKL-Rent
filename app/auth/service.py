"""Бизнес-логика авторизации и управления пользователями (ТЗ §4, §41.2).

Логика держится в service-слое, route handlers остаются тонкими (ТЗ §32).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.events import EventType
from app.audit.service import log as audit_log
from app.auth.models import IpLoginLock, User
from app.database import as_utc, utcnow
from app.utils.security import hash_password, verify_password

# Параметры ограничения попыток входа — по аккаунту.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Параметры ограничения попыток входа — по IP (не зависит от того, существует ли
# аккаунт и заблокирован ли он отдельно; шире лимита по аккаунту, т.к. за одним
# IP/NAT может быть несколько сотрудников).
MAX_FAILED_ATTEMPTS_PER_IP = 20
LOCKOUT_MINUTES_PER_IP = 15


class AuthError(Exception):
    """Базовая ошибка авторизации."""


class InvalidCredentials(AuthError):
    """Неверный логин или пароль."""


class AccountLocked(AuthError):
    """Учётная запись временно заблокирована из-за попыток входа."""


class AccountDisabled(AuthError):
    """Учётная запись отключена или заблокирована администратором."""


class IpRateLimited(AuthError):
    """Превышен лимит попыток входа с этого IP-адреса."""


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.username)).scalars().all())


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def create_user(db: Session, username: str, password: str) -> User:
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()


def set_blocked(db: Session, user: User, blocked: bool) -> None:
    user.is_blocked = blocked
    db.commit()


def unlock_user(db: Session, user: User) -> None:
    """Снять временную блокировку по попыткам входа (ТЗ §41.2)."""
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()


def delete_user(db: Session, user: User) -> None:
    db.delete(user)
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
    """Учесть неудачную попытку для IP; при превышении лимита — заблокировать и залогировать."""
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
            f"(последний логин: «{attempted_username}»).",
        )
    else:
        db.commit()


def authenticate(
    db: Session, username: str, password: str, ip_address: str | None = None
) -> User:
    """Проверить учётные данные с учётом блокировок и rate-limit.

    Выполняется в транзакции; счётчики неудач и время блокировки хранятся в БД —
    отдельно по аккаунту (User) и, если передан ip_address, по IP (IpLoginLock).
    Лимит по IP не раскрывает, существует ли аккаунт: считает как неизвестный
    логин, так и неверный пароль.
    """
    now = utcnow()

    ip_lock = _get_or_create_ip_lock(db, ip_address) if ip_address else None
    if ip_lock is not None:
        ip_locked_until = as_utc(ip_lock.locked_until)
        if ip_locked_until is not None and ip_locked_until > now:
            raise IpRateLimited

    user = get_user_by_username(db, username)
    if user is None:
        if ip_lock is not None:
            _register_ip_failure(db, ip_lock, now, username)
        raise InvalidCredentials

    locked_until = as_utc(user.locked_until)
    if locked_until is not None and locked_until > now:
        raise AccountLocked

    if not user.can_login:
        raise AccountDisabled

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
        if ip_lock is not None:
            _register_ip_failure(db, ip_lock, now, username)
        else:
            db.commit()
        raise InvalidCredentials

    # Успех — сбрасываем счётчики (аккаунта и IP).
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if ip_lock is not None:
        ip_lock.failed_count = 0
        ip_lock.locked_until = None
    db.commit()
    return user
