"""Конфигурация приложения из переменных окружения (ТЗ §37)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    def sqlalchemy_url(self) -> str:
        """Итоговый URL подключения к БД для SQLAlchemy."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
