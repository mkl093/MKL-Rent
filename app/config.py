"""Конфигурация приложения из переменных окружения (ТЗ §37)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Настройки приложения. Значения читаются из окружения и файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Приложение ---
    app_env: str = "development"
    app_secret_key: str = "change-me-in-production"
    app_base_url: str = "http://localhost:8000"
    app_timezone: str = "Europe/Berlin"
    # Разрешённые Host-заголовки (защита от Host-header injection за прокси, ТЗ §41.2).
    # Список через запятую; пусто — проверка отключена (dev). В проде укажите домен(ы).
    app_allowed_hosts: str = ""
    # Флаг Secure у сессионной cookie: "" (авто) = включён в production, выключен иначе.
    # Явные "true"/"false" переопределяют — нужно, чтобы временно поднять прод по HTTP
    # (без TLS Secure-cookie не доходит до сервера и ломает CSRF/вход). ТЗ §41.2.
    session_cookie_secure: str = ""
    # Движок генерации PDF: auto | weasyprint | xhtml2pdf.
    # auto — WeasyPrint при наличии (Docker), иначе xhtml2pdf (локально на Windows).
    pdf_engine: str = "auto"

    # Локальный HTTPS для камеры со смартфона (ТЗ §24, §41.2).
    # Если не заданы — берутся certs/cert.pem и certs/key.pem при их наличии.
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None

    # --- База данных (части собираются в database_url) ---
    database_name: str = "rental"
    database_user: str = "rental"
    database_password: str = "rental"
    database_host: str = "localhost"
    database_port: int = 5432
    # Необязательный полный override (sqlite/postgres). Если задан — используется как есть.
    database_url: str | None = None

    # --- Хранение файлов и backup ---
    storage_path: str = "./storage"
    backup_path: str = "./backups"
    backup_time: str = "03:00"
    backup_retention_days: int = 14
    # Автоматический ежедневный backup по расписанию (планировщик внутри приложения).
    # В production (Docker) включается через BACKUP_AUTO=true.
    backup_auto: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def allowed_hosts(self) -> list[str]:
        """Список разрешённых Host из app_allowed_hosts (пустой — проверка выключена)."""
        return [h.strip() for h in self.app_allowed_hosts.split(",") if h.strip()]

    @property
    def session_secure(self) -> bool:
        """Итоговое значение Secure для сессионной cookie ("" — авто по окружению)."""
        value = self.session_cookie_secure.strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
        return self.is_production

    @property
    def sqlalchemy_url(self) -> str:
        """Итоговый URL подключения к БД для SQLAlchemy.

        Логин/пароль экранируются через URL.create — иначе спецсимволы в пароле
        (@ : / ! # ? …) ломают разбор DSN и хост определяется неверно.
        """
        if self.database_url:
            return self.database_url
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)

    @property
    def alembic_url(self) -> str:
        """URL для alembic (config.set_main_option).

        В sqlalchemy_url спецсимволы пароля percent-кодируются (%40, %21…). Alembic
        прогоняет значение через configparser, где `%` — синтаксис интерполяции,
        поэтому удваиваем его в `%%` (configparser вернёт обратно один `%`).
        """
        return self.sqlalchemy_url.replace("%", "%%")


@lru_cache
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
