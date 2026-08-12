"""Бизнес-логика настроек компании (ТЗ §27)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.settings.models import SINGLETON_ID, CompanySettings
from app.settings.schemas import CompanySettingsUpdate


def get_company_settings(db: Session) -> CompanySettings:
    """Вернуть настройки компании, создав строку-singleton при отсутствии."""
    settings = db.get(CompanySettings, SINGLETON_ID)
    if settings is None:
        settings = CompanySettings(id=SINGLETON_ID)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_company_settings(db: Session, data: CompanySettingsUpdate) -> CompanySettings:
    """Обновить настройки компании."""
    settings = get_company_settings(db)
    for field, value in data.model_dump().items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings


@dataclass
class StorageStats:
    """Статистика файлового хранилища (диск, где живут мануалы/сертификаты/фото)."""

    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def percent_used(self) -> int:
        if self.total_bytes <= 0:
            return 0
        return round(self.used_bytes / self.total_bytes * 100)


def get_storage_stats() -> StorageStats:
    """Доступный объём диска и путь, где на сервере хранятся файлы."""
    path = Path(get_settings().storage_path)
    path.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(path)
    return StorageStats(
        path=str(path.resolve()), total_bytes=total, used_bytes=used, free_bytes=free
    )
