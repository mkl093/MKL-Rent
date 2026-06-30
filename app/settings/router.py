"""Маршруты страницы настроек компании (ТЗ §27)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.settings.schemas import CompanySettingsUpdate
from app.settings.service import update_company_settings
from app.templating import flash

router = APIRouter(prefix="/settings", tags=["settings"])


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@router.get("")
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return render(request, "settings/index.html", {"page_title": "Настройки"}, db=db, user=user)


@router.post("", dependencies=[Depends(verify_csrf)])
def settings_save(
    request: Request,
    company_name: str = Form(""),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    website: str | None = Form(None),
    vat_id: str | None = Form(None),
    bank_details: str | None = Form(None),
    pdf_footer: str | None = Form(None),
    default_vat: str = Form("0"),
    timezone: str = Form("Europe/Berlin"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    try:
        vat = Decimal(default_vat.replace(",", "."))
    except (InvalidOperation, AttributeError):
        vat = Decimal("0")

    data = CompanySettingsUpdate(
        company_name=company_name.strip(),
        address=_clean(address),
        phone=_clean(phone),
        email=_clean(email),
        website=_clean(website),
        vat_id=_clean(vat_id),
        bank_details=_clean(bank_details),
        pdf_footer=_clean(pdf_footer),
        default_vat=vat,
        timezone=timezone.strip() or "Europe/Berlin",
    )
    update_company_settings(db, data)
    flash(request, "Настройки сохранены.", "success")
    return redirect("/settings")
