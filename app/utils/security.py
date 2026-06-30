"""Хеширование паролей и генерация токенов (ТЗ §4, §41.2).

Пароли хранятся только в виде безопасных хешей (argon2).
"""

from __future__ import annotations

import secrets

from pwdlib import PasswordHash

# Argon2 — современный рекомендованный алгоритм хеширования паролей.
_password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    """Вернуть безопасный хеш пароля."""
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против хеша."""
    return _password_hash.verify(plain, hashed)


def generate_token(nbytes: int = 32) -> str:
    """Криптографически стойкий случайный токен (CSRF, и т. п.)."""
    return secrets.token_urlsafe(nbytes)
