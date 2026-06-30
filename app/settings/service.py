"""Бизнес-логика настроек компании (ТЗ §27)."""

from __future__ import annotations

from sqlalchemy.orm import Session

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
