"""Настройки компании (ТЗ §27). Singleton — одна строка с id=1."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin
from app.utils.timezone import DEFAULT_TIMEZONE

SINGLETON_ID = 1


class CompanySettings(Base, TimestampMixin):
    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SINGLETON_ID)

    company_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_footer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Деньги/ставки — только Decimal/Numeric, без float (ТЗ §45.4).
    default_vat: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE, nullable=False)
