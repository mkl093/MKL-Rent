"""Хеширование паролей."""

from app.utils.security import generate_token, hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret-пароль")
    assert hashed != "s3cret-пароль"
    assert verify_password("s3cret-пароль", hashed)
    assert not verify_password("wrong", hashed)


def test_hash_is_salted_unique():
    assert hash_password("same") != hash_password("same")


def test_generate_token_unique():
    assert generate_token() != generate_token()
