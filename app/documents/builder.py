"""Сборка PDF-документов (ТЗ §26).

HTML-шаблоны рендерятся Jinja2 и конвертируются в PDF через WeasyPrint.
Локально (Windows без GTK) WeasyPrint может быть недоступен — рендеринг HTML и
локализация при этом полностью работают и тестируются; PDF собирается в Docker.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.documents import l10n
from app.documents.enums import DocumentType, Language
from app.documents.models import GeneratedDocument
from app.estimates.service import get_or_create_estimate, grouped_lines
from app.estimates.service import totals as estimate_totals
from app.packing.calc import compute_line, compute_totals
from app.packing.service import get_packing
from app.projects.models import Project
from app.settings.service import get_company_settings

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "documents"
_FONTS_DIR = Path(__file__).parent.parent / "static" / "fonts"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


class PdfUnavailable(RuntimeError):
    """WeasyPrint и его системные библиотеки недоступны в этом окружении."""


# --- Подписи интерфейса PDF (ТЗ §26.1) ----------------------------------

LABELS = {
    "ru": {
        "estimate": "Смета",
        "packing": "Упаковочный лист",
        "number": "№",
        "date": "Дата",
        "project": "Проект",
        "rental_dates": "Даты аренды",
        "customer": "Заказчик",
        "name": "Наименование",
        "qty": "Кол-во",
        "price": "Цена",
        "coef": "Коэф.",
        "total": "Сумма",
        "discount": "Скидка",
        "vat": "VAT",
        "grand_total": "Итого",
        "category": "Категория",
        "plan": "План",
        "fact": "Факт",
        "serial": "Серийный №",
        "barcode": "Штрих-код",
        "packed": "В упак.",
        "unpacked": "Без упак.",
        "packages": "Упаковок",
        "eq_weight": "Вес обор.",
        "pack_weight": "Вес упак.",
        "weight": "Вес",
        "vol_loose": "Об. б/у",
        "vol_pack": "Об. упак.",
        "volume": "Об. всего",
        "comment": "Комментарий",
        "shortage": "Причина недокомплекта",
        "total_weight": "Общий вес",
        "total_volume": "Общий объём",
    },
    "en": {
        "estimate": "Estimate",
        "packing": "Packing list",
        "number": "No.",
        "date": "Date",
        "project": "Project",
        "rental_dates": "Rental dates",
        "customer": "Customer",
        "name": "Name",
        "qty": "Qty",
        "price": "Price",
        "coef": "Coef.",
        "total": "Total",
        "discount": "Discount",
        "vat": "VAT",
        "grand_total": "Grand total",
        "category": "Category",
        "plan": "Plan",
        "fact": "Fact",
        "serial": "Serial No.",
        "barcode": "Barcode",
        "packed": "Packed",
        "unpacked": "Loose",
        "packages": "Packages",
        "eq_weight": "Eq. weight",
        "pack_weight": "Pack. weight",
        "weight": "Weight",
        "vol_loose": "Vol. loose",
        "vol_pack": "Vol. pack",
        "volume": "Vol. total",
        "comment": "Comment",
        "shortage": "Shortage reason",
        "total_weight": "Total weight",
        "total_volume": "Total volume",
    },
}


def _font_uri(name: str) -> str:
    return (_FONTS_DIR / name).resolve().as_uri()


def _logo_uri(company) -> str | None:
    if not company.logo_path:
        return None
    path = Path(get_settings().storage_path) / company.logo_path
    return path.resolve().as_uri() if path.exists() else None


def _l10n_helpers(lang: str) -> dict:
    return {
        "fmt_date": lambda d: l10n.format_date(d, lang),
        "fmt_money": lambda v: l10n.format_money(v, lang),
        "fmt_weight": lambda v: l10n.format_weight(v, lang),
        "fmt_volume": lambda v: l10n.format_volume(v, lang),
        "fmt_percent": lambda v: l10n.format_percent(v, lang),
        "L": LABELS[lang],
        "lang": lang,
        "font_regular": _font_uri("DejaVuSans.ttf"),
        "font_bold": _font_uri("DejaVuSans-Bold.ttf"),
    }


# --- Контекст и отпечаток данных (для устаревания, ТЗ §26.6) -------------


def _estimate_render(
    db: Session, project: Project, lang: str, fontface: bool = True
) -> tuple[str, str]:
    company = get_company_settings(db)
    estimate = get_or_create_estimate(db, project)
    groups = grouped_lines(estimate)
    totals = estimate_totals(db, estimate, project)

    fingerprint = json.dumps(
        {
            "company": [company.company_name, company.address, company.vat_id, company.pdf_footer],
            "project": [
                project.number,
                project.name,
                str(project.start_date),
                str(project.end_date),
                project.customer,
                str(project.vat),
            ],
            "estimate": [estimate.number, str(estimate.discount_percent)],
            "lines": [
                [
                    ln.name,
                    ln.quantity,
                    str(ln.unit_price),
                    str(ln.coefficient),
                    str(ln.discount_percent),
                    ln.comment,
                    g.category_name,
                ]
                for g in groups
                for ln in g.lines
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    html = _env.get_template("estimate_pdf.html").render(
        company=company,
        project=project,
        estimate=estimate,
        groups=groups,
        totals=totals,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _packing_render(
    db: Session, project: Project, lang: str, fontface: bool = True
) -> tuple[str, str]:
    company = get_company_settings(db)
    packing = get_packing(db, project)
    if packing is None:
        raise PdfUnavailable("Packing-лист не создан")

    ordered = sorted(
        packing.lines,
        key=lambda ln: (
            ln.is_custom,
            ln.category_name or "",
            ln.subcategory_name or "",
            ln.sort_order,
            ln.id,
        ),
    )
    rows = [(ln, compute_line(ln)) for ln in ordered]
    totals = compute_totals(packing.lines)

    fingerprint = json.dumps(
        {
            "company": [company.company_name, company.address, company.pdf_footer],
            "project": [
                project.number,
                project.name,
                str(project.start_date),
                str(project.end_date),
            ],
            "packing": [packing.number, packing.status.value, packing.shortage_comment],
            "lines": [
                [
                    ln.name,
                    ln.planned_quantity,
                    ln.fact_quantity,
                    ln.packed_quantity,
                    str(ln.unit_weight_kg),
                    ln.length_mm,
                    ln.width_mm,
                    ln.height_mm,
                    sorted(si.barcode for si in ln.serial_items),
                ]
                for ln in ordered
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    html = _env.get_template("packing_pdf.html").render(
        company=company,
        project=project,
        packing=packing,
        rows=rows,
        totals=totals,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_html(
    db: Session, project: Project, doc_type: DocumentType, lang: str, *, fontface: bool = True
) -> tuple[str, str]:
    if doc_type == DocumentType.ESTIMATE:
        return _estimate_render(db, project, lang, fontface)
    return _packing_render(db, project, lang, fontface)


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 — нет системных библиотек GTK/Pango
        return False


def resolve_engine() -> str:
    """Выбрать движок PDF: weasyprint (Docker) или xhtml2pdf (локально на Windows)."""
    pref = get_settings().pdf_engine.lower()
    if pref in ("weasyprint", "xhtml2pdf"):
        return pref
    return "weasyprint" if _weasyprint_available() else "xhtml2pdf"


_fonts_registered = False


def _register_xhtml2pdf_fonts() -> None:
    """Зарегистрировать кириллический DejaVu Sans для xhtml2pdf (один раз)."""
    global _fonts_registered
    if _fonts_registered:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from xhtml2pdf import default as x2p_default

    pdfmetrics.registerFont(TTFont("DejaVu", str(_FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        "DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold"
    )
    x2p_default.DEFAULT_FONT["dejavu"] = "DejaVu"
    _fonts_registered = True


def _render_weasyprint(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001
        raise PdfUnavailable(
            "WeasyPrint недоступен (нужны системные библиотеки GTK/Pango)."
        ) from exc
    return HTML(string=html).write_pdf()


def _render_xhtml2pdf(html: str) -> bytes:
    import io

    from xhtml2pdf import pisa

    _register_xhtml2pdf_fonts()
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        raise PdfUnavailable("Не удалось собрать PDF через xhtml2pdf")
    return buffer.getvalue()


def _html_to_pdf(html: str) -> bytes:
    if resolve_engine() == "weasyprint":
        return _render_weasyprint(html)
    return _render_xhtml2pdf(html)


# --- Генерация и статус (ТЗ §26.6) --------------------------------------


def _storage_dir(project_id: int) -> Path:
    path = Path(get_settings().storage_path) / "documents" / f"project_{project_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_document(
    db: Session, project_id: int, doc_type: DocumentType, lang: str
) -> GeneratedDocument | None:
    return db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.project_id == project_id,
            GeneratedDocument.doc_type == doc_type.value,
            GeneratedDocument.language == lang,
        )
    ).scalar_one_or_none()


def generate(db: Session, project: Project, doc_type: DocumentType, lang: str) -> GeneratedDocument:
    """Сгенерировать PDF и заменить предыдущую версию того же типа+языка (ТЗ §26.6)."""
    # @font-face нужен WeasyPrint; xhtml2pdf использует шрифт из своего реестра.
    html, fingerprint = render_html(
        db, project, doc_type, lang, fontface=resolve_engine() == "weasyprint"
    )
    pdf = _html_to_pdf(html)

    filename = f"{doc_type.value}_{lang}.pdf"
    target = _storage_dir(project.id) / filename
    target.write_bytes(pdf)
    rel = f"documents/project_{project.id}/{filename}"

    doc = get_document(db, project.id, doc_type, lang)
    if doc is None:
        doc = GeneratedDocument(
            project_id=project.id, doc_type=doc_type.value, language=lang, file_path=rel
        )
        db.add(doc)
    doc.file_path = rel
    doc.content_hash = fingerprint
    doc.generated_at = utcnow()
    db.commit()
    db.refresh(doc)
    return doc


@dataclass
class DocStatus:
    doc_type: DocumentType
    language: Language
    available: bool  # можно ли сгенерировать (для packing — есть ли лист)
    generated: bool
    stale: bool
    document: GeneratedDocument | None


def status(db: Session, project: Project) -> list[DocStatus]:
    """Статусы всех 4 сочетаний: сгенерирован ли и не устарел ли (ТЗ §26.6)."""
    packing_exists = get_packing(db, project) is not None
    result: list[DocStatus] = []
    for doc_type in DocumentType:
        available = doc_type == DocumentType.ESTIMATE or packing_exists
        current_hash = None
        if available:
            try:
                _, current_hash = render_html(db, project, doc_type, "ru")
            except PdfUnavailable:
                available = False
        for lang in Language:
            doc = get_document(db, project.id, doc_type, lang)
            stale = bool(doc and current_hash and doc.content_hash != current_hash)
            result.append(DocStatus(doc_type, lang, available, doc is not None, stale, doc))
    return result
