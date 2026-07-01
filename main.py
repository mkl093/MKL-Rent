"""Тонкая точка входа. Реальное приложение — в app.main:app.

Запуск для разработки:
    uvicorn app.main:app --reload
или:
    python main.py

Если существуют certs/cert.pem и certs/key.pem (или заданы SSL_CERTFILE/SSL_KEYFILE),
запуск идёт по HTTPS — это нужно, чтобы камера сканера работала со смартфона (ТЗ §24).
Сгенерировать сертификат: python -m scripts.make_cert
"""

from pathlib import Path

from app.config import get_settings
from app.main import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    cert = settings.ssl_certfile or "certs/cert.pem"
    key = settings.ssl_keyfile or "certs/key.pem"
    use_ssl = Path(cert).exists() and Path(key).exists()

    if use_ssl:
        print(f"HTTPS: {cert} / {key}")
    else:
        print("HTTP (без TLS). Для камеры со смартфона: python -m scripts.make_cert")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        ssl_certfile=cert if use_ssl else None,
        ssl_keyfile=key if use_ssl else None,
    )
