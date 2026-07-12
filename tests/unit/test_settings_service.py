"""Настройки компании — singleton (ТЗ §27)."""

from decimal import Decimal

from app.settings.schemas import CompanySettingsUpdate
from app.settings.service import get_company_settings, update_company_settings


def test_singleton_created_once(db_session):
    a = get_company_settings(db_session)
    b = get_company_settings(db_session)
    assert a.id == b.id == 1


def test_update_settings(db_session):
    update_company_settings(
        db_session,
        CompanySettingsUpdate(company_name="MKL Rental", default_vat=Decimal("19")),
    )
    settings = get_company_settings(db_session)
    assert settings.company_name == "MKL Rental"
    assert settings.default_vat == Decimal("19")


def test_vat_id_and_tax_number_are_separate(db_session):
    """Ставка VAT, VAT ID и налоговый номер — независимые поля."""
    update_company_settings(
        db_session,
        CompanySettingsUpdate(
            default_vat=Decimal("19"),
            vat_id="DE123456789",
            tax_number="12/345/67890",
        ),
    )
    settings = get_company_settings(db_session)
    assert settings.default_vat == Decimal("19")
    assert settings.vat_id == "DE123456789"
    assert settings.tax_number == "12/345/67890"
