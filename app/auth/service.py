"""Бизнес-логика авторизации и управления пользователями (ТЗ §4, §41.2).

Логика держится в service-слое, route handlers остаются тонкими (ТЗ §32).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import as_utc, utcnow
from app.utils.security import hash_password, verify_password

# Параметры ограничения попыток входа.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class AuthError(Exception):
    """Базовая ошибка авторизации."""


class InvalidCredentials(AuthError):
    """Неверный логин или пароль."""


class AccountLocked(AuthError):
    """Учётная запись временно заблокирована из-за попыток входа."""


class AccountDisabled(AuthError):
    """Учётная запись отключена или заблокирована администратором."""


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


def authenticate(db: Session, username: str, password: str) -> User:
    """Проверить учётные данные с учётом блокировок и rate-limit.

    Выполняется в транзакции; счётчик неудач и время блокировки хранятся в БД.
    """
    user = get_user_by_username(db, username)
    if user is None:
        raise InvalidCredentials

    now = utcnow()
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
        db.commit()
        raise InvalidCredentials

    # Успех — сбрасываем счётчики.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    return user
