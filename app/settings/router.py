"""Маршруты страницы настроек компании (ТЗ §27)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.auth.models import User
from app.database import get_db
from app.dependencies import redirect, render, require_login, verify_csrf
from app.settings.schemas import CompanySettingsUpdate
from app.settings.service import update_company_settings
from app.templating import flash
from app.utils.images import ImageError, delete_photo, save_logo

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
async def settings_save(
    request: Request,
    company_name: str = Form(""),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    website: str | None = Form(None),
    vat_id: str | None = Form(None),
    tax_number: str | None = Form(None),
    bank_details: str | None = Form(None),
    pdf_footer: str | None = Form(None),
    default_vat: str = Form("0"),
    timezone: str = Form("Europe/Berlin"),
    logo: UploadFile | None = File(None),
    remove_logo: str | None = Form(None),
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
        tax_number=_clean(tax_number),
        bank_details=_clean(bank_details),
        pdf_footer=_clean(pdf_footer),
        default_vat=vat,
        timezone=timezone.strip() or "Europe/Berlin",
    )
    settings = update_company_settings(db, data)

    # Логотип компании (для сметы/packing и веба) — отдельно от текстовых полей.
    if remove_logo:
        delete_photo(settings.logo_path)
        settings.logo_path = None
        db.commit()
    elif logo is not None and logo.filename:
        raw = await logo.read()
        try:
            rel = save_logo(raw)
        except ImageError as exc:
            flash(request, f"Логотип не загружен: {exc}", "warning")
        else:
            delete_photo(settings.logo_path)
            settings.logo_path = rel
            db.commit()

    flash(request, "Настройки сохранены.", "success")
    return redirect("/settings")
