"""Маршруты гостевого портала (просмотр склада клиентами без прав персонала)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import redirect, render, verify_csrf
from app.guests import catalog as catalog_service
from app.guests import service as guest_service
from app.guests.dependencies import GUEST_SESSION_KEY, get_current_guest, require_guest_login
from app.guests.i18n import date_input_locale, get_lang, set_lang, translator
from app.guests.models import GUEST_LEVEL_AVAILABILITY, GuestUser
from app.settings.service import get_company_settings

router = APIRouter(prefix="/guest", tags=["guest"])


def _parse_period(start: str | None, end: str | None) -> tuple[date, date] | None:
    if not start or not end:
        return None
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        return None
    if start_date > end_date:
        return None
    return start_date, end_date


# --- Брендинг --------------------------------------------------------------


@router.get("/branding/logo")
def guest_branding_logo(db: Session = Depends(get_db)) -> FileResponse:
    """Логотип компании для гостевого портала (виден и до входа — на /guest/login)."""
    company = get_company_settings(db)
    if not company.logo_path:
        raise HTTPException(status_code=404)
    target = (Path(get_settings().storage_path) / company.logo_path).resolve()
    if not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(target))


# --- Вход / выход / язык -------------------------------------------------


@router.get("/login")
def guest_login_form(request: Request, db: Session = Depends(get_db)):
    if get_current_guest(request, db) is not None:
        return redirect("/guest/")
    lang = get_lang(request)
    t = translator(lang)
    return render(
        request,
        "guest/login.html",
        {"page_title": t("login_title"), "t": t, "lang": lang, "username": ""},
        db=db,
    )


@router.post("/login", dependencies=[Depends(verify_csrf)])
def guest_login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    lang = get_lang(request)
    t = translator(lang)
    username = username.strip()
    if not username or not password:
        return render(
            request,
            "guest/login.html",
            {
                "page_title": t("login_title"),
                "t": t,
                "lang": lang,
                "username": username,
                "error": t("error_missing_credentials"),
            },
            db=db,
        )
    try:
        guest = guest_service.authenticate_guest(
            db,
            username,
            password,
            ip_address=request.client.host if request.client else None,
        )
    except (guest_service.GuestAccountLocked, guest_service.GuestIpRateLimited):
        return render(
            request,
            "guest/login.html",
            {"page_title": t("login_title"), "t": t, "lang": lang, "username": username},
            db=db,
        )
    except guest_service.GuestAccountDisabled:
        return render(
            request,
            "guest/login.html",
            {
                "page_title": t("login_title"),
                "t": t,
                "lang": lang,
                "username": username,
                "error": t("error_account_disabled"),
            },
            db=db,
        )
    except guest_service.GuestAuthError:
        return render(
            request,
            "guest/login.html",
            {
                "page_title": t("login_title"),
                "t": t,
                "lang": lang,
                "username": username,
                "error": t("error_invalid_credentials"),
            },
            db=db,
        )

    request.session[GUEST_SESSION_KEY] = guest.id
    return redirect("/guest/")


@router.post("/logout")
def guest_logout(request: Request):
    request.session.pop(GUEST_SESSION_KEY, None)
    return redirect("/guest/login")


@router.get("/lang/{lang}")
def guest_set_lang(request: Request, lang: str, next: str = "/guest/login"):
    set_lang(request, lang)
    target = next if next.startswith("/guest") else "/guest/login"
    return redirect(target)


# --- Каталог --------------------------------------------------------------


@router.get("/")
def catalog_page(
    request: Request,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    view: str = "grid",
    db: Session = Depends(get_db),
    guest: GuestUser = Depends(require_guest_login),
):
    lang = get_lang(request)
    t = translator(lang)
    can_search_dates = guest.access_level >= GUEST_LEVEL_AVAILABILITY
    view = view if view in ("grid", "table") else "grid"

    period = _parse_period(start, end) if can_search_dates else None

    rows = []
    for model in catalog_service.visible_models(db, q):
        available = None
        if period:
            available = catalog_service.available_for_period(db, model, *period)
        rows.append(
            {
                "model": model,
                "total": catalog_service.total_stock(db, model),
                "available": available,
            }
        )

    # Сохраняем поиск/даты в ссылках переключения вида (grid/table).
    preserved_params = {k: v for k, v in (("q", q), ("start", start), ("end", end)) if v}
    query_suffix = ("&" + urlencode(preserved_params)) if preserved_params else ""

    return render(
        request,
        "guest/catalog.html",
        {
            "page_title": t("catalog_title"),
            "t": t,
            "lang": lang,
            "date_locale": date_input_locale(lang),
            "guest": guest,
            "rows": rows,
            "q": q or "",
            "start": start or "",
            "end": end or "",
            "can_search_dates": can_search_dates,
            "view": view,
            "query_suffix": query_suffix,
        },
        db=db,
    )


@router.get("/models/{model_id}")
def model_detail_page(
    request: Request,
    model_id: int,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    guest: GuestUser = Depends(require_guest_login),
):
    model = catalog_service.get_visible_model(db, model_id)
    if model is None:
        raise HTTPException(status_code=404)

    lang = get_lang(request)
    t = translator(lang)
    can_search_dates = guest.access_level >= GUEST_LEVEL_AVAILABILITY

    period = _parse_period(start, end) if can_search_dates else None
    available = catalog_service.available_for_period(db, model, *period) if period else None

    return render(
        request,
        "guest/model_detail.html",
        {
            "page_title": model.name,
            "t": t,
            "lang": lang,
            "date_locale": date_input_locale(lang),
            "guest": guest,
            "model": model,
            "total": catalog_service.total_stock(db, model),
            "available": available,
            "start": start or "",
            "end": end or "",
            "can_search_dates": can_search_dates,
        },
        db=db,
    )


@router.get("/models/{model_id}/photo")
def model_photo(
    model_id: int,
    db: Session = Depends(get_db),
    guest: GuestUser = Depends(require_guest_login),
) -> FileResponse:
    model = catalog_service.get_visible_model(db, model_id)
    if model is None or not model.photo_path:
        raise HTTPException(status_code=404)
    target = (Path(get_settings().storage_path) / model.photo_path).resolve()
    if not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(target))
