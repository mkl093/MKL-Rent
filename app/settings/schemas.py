"""Pydantic-схемы настроек компании."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class CompanySettingsUpdate(BaseModel):
    company_name: str = Field(default="", max_length=255)
    address: str | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    vat_id: str | None = Field(default=None, max_length=100)
    bank_details: str | None = Field(default=None)
    pdf_footer: str | None = Field(default=None)
    default_vat: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    timezone: str = Field(default="Europe/Berlin", max_length=64)
