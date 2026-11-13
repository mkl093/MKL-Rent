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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.documents import l10n
from app.documents.enums import DocumentType, Language
from app.documents.models import GeneratedDocument
from app.estimates.models import Estimate, ProjectSection
from app.estimates.service import get_or_create_estimate, grouped_lines
from app.estimates.service import totals as estimate_totals
from app.packing import combined
from app.packing.calc import compute_category_breakdown, compute_line, compute_totals
from app.packing.models import PackingList
from app.packing.service import (
    accessory_totals,
    backfill_quantity_barcodes,
    list_packings,
)
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
        "picking": "Лист комплектации",
        "transport": "Packing-лист по машинам",
        "location": "Место хранения",
        "vehicle": "Машина",
        "plate": "Гос. номер",
        "max_weight": "Грузоподъёмность",
        "positions": "Позиций",
        "unassigned": "Не распределено",
        "number": "№",
        "date": "Дата",
        "project": "Проект",
        "rental_dates": "Даты аренды",
        "shipped": "Отгрузка",
        "returned": "Возврат",
        "customer": "Заказчик",
        "name": "Наименование",
        "qty": "Кол-во",
        "price": "Цена",
        "coef": "Коэф.",
        "total": "Сумма",
        "discount": "Скидка",
        "after_discount": "После скидки",
        "vat": "VAT",
        "vat_id": "VAT ID",
        "tax_number": "Налоговый номер",
        "grand_total": "Итого",
        "stand": "Стенд",
        "stands": "Стенды",
        "all_stands": "все стенды",
        "group_subtotal": "Итого по разделу",
        "quote_open": "«",
        "quote_close": "»",
        "coef_estimate": "Коэффициент",
        "category": "Категория",
        "plan": "План",
        "fact": "Факт",
        "serial": "Серийный №",
        "barcode": "Штрих-код",
        "packed": "В упак.",
        "unpacked": "Без упак.",
        "packages": "Упак.",
        "packages_unit": "уп.",
        "eq_weight": "Вес обор.",
        "pack_weight": "Вес упак.",
        "eq_short": "обор.",
        "pack_short": "упак.",
        "weight": "Вес",
        "vol_loose": "Об. б/у",
        "dimensions": "Габариты кейса/ед. (ДхШхВ), мм",
        "volume": "Об. всего",
        "comment": "Комментарий",
        "shortage": "Причина недокомплекта",
        "total_weight": "Общий вес",
        "total_volume": "Общий объём",
        "power_peak": "Энергопотр. пик.",
        "power_nominal": "Энергопотр. ном.",
        "total_power_peak": "Энергопотребление пиковое",
        "total_power_nominal": "Энергопотребление номинальное",
        "breakdown": "Итоги по категориям",
        "accessories": "Аксессуары (комплектация)",
        "accessory": "Аксессуар",
        "additional": "Дополнительно",
        "no_category": "Без категории",
        "kits": "Комплекты",
        "other": "Прочее",
        "kit": "комплект",
        "accessory_kits": "Комплекты аксессуаров",
        "accessory_kit": "комплект аксессуаров",
    },
    "en": {
        "estimate": "Estimate",
        "packing": "Packing list",
        "picking": "Picking list",
        "transport": "Packing list by vehicle",
        "location": "Location",
        "vehicle": "Vehicle",
        "plate": "Plate no.",
        "max_weight": "Max. weight",
        "positions": "Positions",
        "unassigned": "Unassigned",
        "number": "No.",
        "date": "Date",
        "project": "Project",
        "rental_dates": "Rental dates",
        "shipped": "Shipped",
        "returned": "Returned",
        "customer": "Customer",
        "name": "Name",
        "qty": "Qty",
        "price": "Price",
        "coef": "Coef.",
        "total": "Total",
        "discount": "Discount",
        "after_discount": "After discount",
        "vat": "VAT",
        "vat_id": "VAT ID",
        "tax_number": "Tax number",
        "grand_total": "Grand total",
        "stand": "Stand",
        "stands": "Stands",
        "all_stands": "all stands",
        "group_subtotal": "Subtotal",
        "quote_open": "“",
        "quote_close": "”",
        "coef_estimate": "Rental coefficient",
        "category": "Category",
        "plan": "Plan",
        "fact": "Fact",
        "serial": "Serial No.",
        "barcode": "Barcode",
        "packed": "Packed",
        "unpacked": "Loose",
        "packages": "Pkgs",
        "packages_unit": "pkgs",
        "eq_weight": "Eq. weight",
        "pack_weight": "Pack. weight",
        "eq_short": "eq.",
        "pack_short": "pack.",
        "weight": "Weight",
        "vol_loose": "Vol. loose",
        "dimensions": "Case/unit dims (LxWxH), mm",
        "volume": "Vol. total",
        "comment": "Comment",
        "shortage": "Shortage reason",
        "total_weight": "Total weight",
        "total_volume": "Total volume",
        "power_peak": "Peak power",
        "power_nominal": "Nom. power",
        "total_power_peak": "Total peak power",
        "total_power_nominal": "Total nominal power",
        "breakdown": "Totals by category",
        "accessories": "Accessories (kit)",
        "accessory": "Accessory",
        "additional": "Additional",
        "no_category": "No category",
        "kits": "Kits",
        "other": "Other",
        "kit": "kit",
        "accessory_kits": "Accessory kits",
        "accessory_kit": "accessory kit",
    },
    "de": {
        "estimate": "Angebot",
        "packing": "Packliste",
        "picking": "Kommissionierliste",
        "transport": "Packliste je Fahrzeug",
        "location": "Lagerplatz",
        "vehicle": "Fahrzeug",
        "plate": "Kennzeichen",
        "max_weight": "Zuladung",
        "positions": "Positionen",
        "unassigned": "Nicht zugeordnet",
        "number": "Nr.",
        "date": "Datum",
        "project": "Projekt",
        "rental_dates": "Mietzeitraum",
        "shipped": "Versand",
        "returned": "Rückgabe",
        "customer": "Kunde",
        "name": "Bezeichnung",
        "qty": "Menge",
        "price": "Preis",
        "coef": "Koeff.",
        "total": "Summe",
        "discount": "Rabatt",
        "after_discount": "Nach Rabatt",
        "vat": "MwSt.",
        "vat_id": "USt-IdNr.",
        "tax_number": "Steuernummer",
        "grand_total": "Gesamt",
        "stand": "Stand",
        "stands": "Stände",
        "all_stands": "alle Stände",
        "group_subtotal": "Zwischensumme",
        "quote_open": "„",
        "quote_close": "“",
        "coef_estimate": "Mietkoeffizient",
        "category": "Kategorie",
        "plan": "Plan",
        "fact": "Ist",
        "serial": "Seriennr.",
        "barcode": "Barcode",
        "packed": "verpackt",
        "unpacked": "lose",
        "packages": "Kolli",
        "packages_unit": "Kolli",
        "eq_weight": "Ger.-Gew.",
        "pack_weight": "Verp.-Gew.",
        "eq_short": "Ger.",
        "pack_short": "Verp.",
        "weight": "Gewicht",
        "vol_loose": "Vol. lose",
        "dimensions": "Kolli-/Gerätemaße (LxBxH), mm",
        "volume": "Vol. ges.",
        "comment": "Kommentar",
        "shortage": "Fehlmengengrund",
        "total_weight": "Gesamtgewicht",
        "total_volume": "Gesamtvolumen",
        "power_peak": "Spitzenleist.",
        "power_nominal": "Nennleist.",
        "total_power_peak": "Gesamt-Spitzenleistung",
        "total_power_nominal": "Gesamt-Nennleistung",
        "breakdown": "Summen nach Kategorie",
        "accessories": "Zubehör (Bestückung)",
        "accessory": "Zubehör",
        "additional": "Sonstiges",
        "no_category": "Ohne Kategorie",
        "kits": "Sets",
        "other": "Sonstiges",
        "kit": "Set",
        "accessory_kits": "Zubehör-Sets",
        "accessory_kit": "Zubehör-Set",
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
        "fmt_power": lambda v: l10n.format_power(v, lang),
        "fmt_percent": lambda v: l10n.format_percent(v, lang),
        "fmt_coef": lambda v: l10n.format_coefficient(v, lang),
        "L": LABELS[lang],
        "lang": lang,
        "font_regular": _font_uri("DejaVuSans.ttf"),
        "font_bold": _font_uri("DejaVuSans-Bold.ttf"),
    }


# --- Контекст и отпечаток данных (для устаревания, ТЗ §26.6) -------------


def default_estimate(db: Session, project: Project) -> Estimate:
    """Смета «по умолчанию» для документов: активная версия первого стенда."""
    return get_or_create_estimate(db, project)


def _estimate_render(
    db: Session,
    project: Project,
    lang: str,
    fontface: bool = True,
    estimate: Estimate | None = None,
) -> tuple[str, str]:
    from app.estimates.versions import DEFAULT_SECTION_NAME

    company = get_company_settings(db)
    if estimate is None:
        estimate = default_estimate(db, project)
    section = estimate.section
    sections_count = db.scalar(
        select(func.count(ProjectSection.id)).where(ProjectSection.project_id == project.id)
    )
    # Название стенда — если стендов несколько или стенд переименован; номер
    # версии — если у стенда больше одной версии.
    show_section = sections_count > 1 or section.name != DEFAULT_SECTION_NAME
    show_version = len(section.versions) > 1
    title_number = estimate.display_number if show_version else section.number
    groups = grouped_lines(
        estimate,
        kit_label=LABELS[lang]["kits"],
        accessory_kit_label=LABELS[lang]["accessory_kits"],
        custom_label=LABELS[lang]["other"],
        no_category_label=LABELS[lang]["no_category"],
    )
    totals = estimate_totals(db, estimate, project)

    fingerprint = json.dumps(
        {
            "company": [
                company.company_name,
                company.address,
                company.vat_id,
                company.tax_number,
                company.pdf_footer,
            ],
            "project": [
                project.number,
                project.name,
                str(project.start_date),
                str(project.end_date),
                str(project.shipped_date),
                str(project.returned_date),
                project.customer,
                str(project.vat),
            ],
            "estimate": [
                title_number,
                section.name if show_section else None,
                str(estimate.discount_percent),
                str(estimate.rental_coefficient),
                estimate.hide_prices,
            ],
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
        title_number=title_number,
        section_name=section.name if show_section else None,
        groups=groups,
        totals=totals,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _section_title(db: Session, project: Project, packing: PackingList) -> str | None:
    """Название стенда для шапки документа — если стендов несколько или стенд переименован."""
    from app.estimates.versions import DEFAULT_SECTION_NAME

    sections_count = db.scalar(
        select(func.count(ProjectSection.id)).where(ProjectSection.project_id == project.id)
    )
    name = packing.section.name
    return name if sections_count > 1 or name != DEFAULT_SECTION_NAME else None


def _stands_meta(packings: list[PackingList]) -> str:
    """«A (PL-…), B (PL-…)» — листы стендов в шапке общего листа."""
    return ", ".join(f"{pl.section.name} ({pl.number})" for pl in packings)


def _project_packings(db: Session, project: Project) -> list[PackingList]:
    packings = list_packings(db, project)
    if not packings:
        raise PdfUnavailable("Packing-лист не создан")
    return packings


def _packing_render(
    db: Session,
    project: Project,
    lang: str,
    fontface: bool = True,
    packing: PackingList | None = None,
) -> tuple[str, str]:
    """Packing-лист стенда (``packing``) или общий лист проекта (``packing=None``)."""
    company = get_company_settings(db)
    labels = {
        "custom_label": LABELS[lang]["additional"],
        "kit_label": LABELS[lang]["kits"],
        "accessory_kit_label": LABELS[lang]["accessory_kits"],
        "no_category_label": LABELS[lang]["no_category"],
    }
    combined_view = packing is None
    packings = _project_packings(db, project) if combined_view else [packing]
    if not project.is_archived:
        # Факт мог быть выставлен до появления штрих-кода в наличии/до правки
        # логики авто-подбора — довязываем недостающие заготовки перед сборкой
        # документа, а не только по явному скану/правке количества.
        for pl in packings:
            backfill_quantity_barcodes(db, pl)

    if combined_view:
        cp = combined.build(db, packings, **labels)
        ordered = cp.lines
        rows = [(c, c.calc) for c in ordered]
        totals, breakdown, accessories = cp.totals, cp.breakdown, cp.accessories
        shortages = cp.shortages
        title_number, section_name, stands = project.number, None, _stands_meta(packings)
        status_value = cp.status.value
    else:
        ordered = _packing_ordered_lines(packing)
        rows = [(ln, compute_line(ln)) for ln in ordered]
        totals = compute_totals(packing.lines)
        breakdown = compute_category_breakdown(packing.lines, **labels)
        accessories = accessory_totals(db, packing)
        shortages = [(None, packing.shortage_comment)] if packing.shortage_comment else []
        title_number, stands = packing.number, None
        section_name = _section_title(db, project, packing)
        status_value = packing.status.value

    # Перечень комплектации для строк-комплектов (структура «Комплект»).
    from app.accessory_kits import service as accessory_kit_service
    from app.inventory.services import kits as kit_service

    kit_content: dict[int, list] = {}
    accessory_kit_content: dict[int, list] = {}
    for ln in ordered:
        if ln.kit_id is not None:
            kit = kit_service.get_kit(db, ln.kit_id)
            if kit is not None:
                kit_content[ln.id] = kit_service.content_groups(kit)
        elif ln.accessory_kit_id is not None:
            ak = accessory_kit_service.get(db, ln.accessory_kit_id)
            if ak is not None:
                accessory_kit_content[ln.id] = ak.lines

    data = {
        "company": [company.company_name, company.address, company.pdf_footer],
        "project": [
            project.number,
            project.name,
            str(project.start_date),
            str(project.end_date),
            str(project.shipped_date),
            str(project.returned_date),
        ],
        "packing": [
            title_number,
            status_value,
            shortages if combined_view else packing.shortage_comment,
            section_name,
        ],
        "accessories": [[g.category_name, g.items] for g in accessories],
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
                ln.has_packing,
                ln.pack_length_mm,
                ln.pack_width_mm,
                ln.pack_height_mm,
                ln.has_power,
                ln.power_peak_w,
                ln.power_nominal_w,
                sorted((si.barcode, si.confirmed_by_scan) for si in ln.serial_items),
                [
                    [g.model_name, [it.barcode or it.serial_number or "" for it in g.items]]
                    for g in kit_content.get(ln.id, [])
                ],
                [
                    [cl.name, cl.quantity]
                    for cl in accessory_kit_content.get(ln.id, [])
                ],
            ]
            for ln in ordered
        ],
    }
    if combined_view:
        # Общий лист: стенды, распределение строк и упаковка как сумма по стендам.
        data["stands"] = stands
        data["shares"] = [
            [c.stands_text, c.comment, c.calc.packages, str(c.calc.total_weight)]
            for c in ordered
        ]
    fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True)
    html = _env.get_template("packing_pdf.html").render(
        company=company,
        project=project,
        title_number=title_number,
        combined=combined_view,
        stands=stands,
        section_name=section_name,
        shortages=shortages,
        rows=rows,
        totals=totals,
        breakdown=breakdown,
        accessories=accessories,
        kit_content=kit_content,
        accessory_kit_content=accessory_kit_content,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _packing_ordered_lines(packing) -> list:
    return sorted(
        packing.lines,
        key=lambda ln: (
            ln.is_custom,
            ln.category_name or "",
            ln.subcategory_name or "",
            ln.sort_order,
            ln.id,
        ),
    )


def _storage_locations(db: Session, ordered: list) -> dict[int, str]:
    """Место хранения по строкам packing-листа: живьём из модели/комплекта (не снимок)."""
    from app.inventory.models import EquipmentModel, Kit

    model_ids = {ln.model_id for ln in ordered if ln.model_id is not None}
    kit_ids = {ln.kit_id for ln in ordered if ln.kit_id is not None}
    model_locations: dict[int, str] = {}
    if model_ids:
        rows = db.execute(
            select(EquipmentModel.id, EquipmentModel.storage_location).where(
                EquipmentModel.id.in_(model_ids)
            )
        ).all()
        model_locations = {mid: loc or "" for mid, loc in rows}
    kit_locations: dict[int, str] = {}
    if kit_ids:
        rows = db.execute(select(Kit.id, Kit.storage_location).where(Kit.id.in_(kit_ids))).all()
        kit_locations = {kid: loc or "" for kid, loc in rows}

    result: dict[int, str] = {}
    for ln in ordered:
        if ln.model_id is not None:
            result[ln.id] = model_locations.get(ln.model_id, "") or "—"
        elif ln.kit_id is not None:
            result[ln.id] = kit_locations.get(ln.kit_id, "") or "—"
        else:
            result[ln.id] = "—"
    return result


def _picking_render(
    db: Session,
    project: Project,
    lang: str,
    fontface: bool = True,
    packing: PackingList | None = None,
) -> tuple[str, str]:
    """Лист комплектации стенда (``packing``) или общий по проекту (``packing=None``)."""
    company = get_company_settings(db)
    combined_view = packing is None
    if combined_view:
        packings = _project_packings(db, project)
        ordered = combined.combine_lines(packings)
        title_number, section_name, stands = project.number, None, _stands_meta(packings)
    else:
        ordered = _packing_ordered_lines(packing)
        title_number, stands = packing.number, None
        section_name = _section_title(db, project, packing)

    locations = _storage_locations(db, ordered)
    rows = [
        (
            ln,
            locations[ln.id],
            1 if ln.is_kit else ln.planned_quantity,
            1 if ln.is_kit else ln.fact_quantity,
        )
        for ln in ordered
    ]

    data = {
        "company": [company.company_name, company.address, company.pdf_footer],
        "project": [project.number, project.name],
        "packing": [title_number, section_name],
        "lines": [[ln.name, planned, fact, loc] for ln, loc, planned, fact in rows],
    }
    if combined_view:
        data["stands"] = stands
        data["shares"] = [ln.stands_lines(True) for ln in ordered]
    fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True)
    html = _env.get_template("picking_pdf.html").render(
        company=company,
        project=project,
        title_number=title_number,
        combined=combined_view,
        stands=stands,
        section_name=section_name,
        rows=rows,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _transport_render(
    db: Session, project: Project, lang: str, fontface: bool = True
) -> tuple[str, str]:
    from app.accessory_kits import service as accessory_kit_service
    from app.transport import service as transport_service

    company = get_company_settings(db)
    packings = list_packings(db, project)
    if not packings:
        raise PdfUnavailable("Packing-лист не создан")
    # Машины общие на проект: при нескольких стендах в заголовке — номер проекта.
    title_number = packings[0].number if len(packings) == 1 else project.number

    board = transport_service.board(db, project)
    if not board.vehicles or not any(v.assignments for v in board.vehicles):
        raise PdfUnavailable("Оборудование не распределено по машинам")

    # Содержимое комплектов аксессуаров, назначенных в машины — отдельной
    # таблицей на листе каждой машины (кабелярка едет одним неделимым кейсом).
    accessory_kit_content: dict[int, list] = {}
    for vb in board.vehicles:
        for row in vb.assignments:
            if row.line.accessory_kit_id is not None and row.line.id not in accessory_kit_content:
                ak = accessory_kit_service.get(db, row.line.accessory_kit_id)
                if ak is not None:
                    accessory_kit_content[row.line.id] = ak.lines

    data = {
        "company": [company.company_name, company.address, company.pdf_footer],
        "project": [project.number, project.name],
        "packing": [pl.number for pl in packings],
        "vehicles": [
            [
                v.project_vehicle.name,
                v.project_vehicle.plate_number,
                str(v.project_vehicle.max_weight_kg),
                [
                    [
                        a.line.name,
                        a.assignment.quantity,
                        [[cl.name, cl.quantity] for cl in accessory_kit_content.get(a.line.id, [])],
                    ]
                    for a in v.assignments
                ],
            ]
            for v in board.vehicles
        ],
        "unassigned": [[u.line.name, u.remaining] for u in board.unassigned],
    }
    stands = None
    if board.multi_section:
        # Несколько стендов: строки машин и пула сгруппированы по стендам.
        stands = _stands_meta(packings)
        data["stands"] = stands
        data["sections"] = [
            [[a.section_name for a in v.assignments] for v in board.vehicles],
            [u.section_name for u in board.unassigned],
        ]
    fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True)
    html = _env.get_template("transport_pdf.html").render(
        company=company,
        project=project,
        title_number=title_number,
        stands=stands,
        board=board,
        accessory_kit_content=accessory_kit_content,
        generated_at=utcnow(),
        logo_uri=_logo_uri(company),
        fontface=fontface,
        **_l10n_helpers(lang),
    )
    return html, _hash(fingerprint)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_RENDERERS = {
    DocumentType.ESTIMATE: _estimate_render,
    DocumentType.PACKING: _packing_render,
    DocumentType.PICKING: _picking_render,
    DocumentType.TRANSPORT: _transport_render,
}


def render_html(
    db: Session,
    project: Project,
    doc_type: DocumentType,
    lang: str,
    *,
    fontface: bool = True,
    estimate: Estimate | None = None,
    packing: PackingList | None = None,
) -> tuple[str, str]:
    """HTML документа и отпечаток данных. ``estimate`` — версия сметы для PDF сметы
    (по умолчанию — активная версия первого стенда); ``packing`` — лист стенда для
    packing-листа и листа комплектации (без него — общий лист проекта)."""
    if doc_type == DocumentType.ESTIMATE:
        return _estimate_render(db, project, lang, fontface, estimate)
    if doc_type in _PACKING_DOCS:
        return _RENDERERS[doc_type](db, project, lang, fontface, packing)
    return _RENDERERS[doc_type](db, project, lang, fontface)


_PACKING_DOCS = (DocumentType.PACKING, DocumentType.PICKING)


def doc_scope(
    doc_type: DocumentType,
    estimate: Estimate | None = None,
    packing: PackingList | None = None,
) -> str:
    """Область хранения документа: смета — по версии, packing/picking — по листу
    стенда (общий лист и packing-лист по машинам — по проекту)."""
    if doc_type == DocumentType.ESTIMATE and estimate is not None:
        return f"estimate:{estimate.id}"
    if doc_type in _PACKING_DOCS and packing is not None:
        return f"packing:{packing.id}"
    return "project"


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


def _x2p_link_callback(uri: str, rel: str) -> str:
    """Разрешить URI ресурса в локальный путь для xhtml2pdf.

    xhtml2pdf на Windows не умеет забирать данные из ``file:///C:/...`` URI
    (логотип в шапке документа), поэтому переводим такой URI в путь ФС.
    """
    import os
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        # file:///C:/... → C:/... (на Windows убираем ведущий слэш перед буквой диска)
        if os.name == "nt" and path.startswith("/") and path[2:3] == ":":
            path = path[1:]
        return path
    return uri


def _render_xhtml2pdf(html: str) -> bytes:
    import io

    from xhtml2pdf import pisa

    _register_xhtml2pdf_fonts()
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8", link_callback=_x2p_link_callback)
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
    db: Session,
    project_id: int,
    doc_type: DocumentType,
    lang: str,
    scope: str = "project",
) -> GeneratedDocument | None:
    return db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.project_id == project_id,
            GeneratedDocument.scope == scope,
            GeneratedDocument.doc_type == doc_type.value,
            GeneratedDocument.language == lang,
        )
    ).scalar_one_or_none()


def generate(
    db: Session,
    project: Project,
    doc_type: DocumentType,
    lang: str,
    *,
    estimate: Estimate | None = None,
    packing: PackingList | None = None,
) -> GeneratedDocument:
    """Сгенерировать PDF и заменить предыдущую версию того же сочетания (ТЗ §26.6).

    PDF сметы хранится по версии сметы (``estimate``, по умолчанию — активная
    версия первого стенда), packing-лист и лист комплектации — по листу стенда
    (``packing``; без него — общий лист проекта по всем стендам), остальное — по
    проекту.
    """
    if doc_type == DocumentType.ESTIMATE and estimate is None:
        estimate = default_estimate(db, project)
    # @font-face нужен WeasyPrint; xhtml2pdf использует шрифт из своего реестра.
    html, fingerprint = render_html(
        db,
        project,
        doc_type,
        lang,
        fontface=resolve_engine() == "weasyprint",
        estimate=estimate,
        packing=packing,
    )
    pdf = _html_to_pdf(html)

    scope = doc_scope(doc_type, estimate, packing)
    if scope == "project" and doc_type in _PACKING_DOCS:
        # Общий лист проекта. Имя {type}_{lang}.pdf занято PDF листа стенда,
        # перенесённым миграцией 0031 из scope "project", — его не перезаписываем.
        filename = f"{doc_type.value}_all_{lang}.pdf"
    elif scope == "project":
        filename = f"{doc_type.value}_{lang}.pdf"
    elif doc_type == DocumentType.ESTIMATE:
        filename = f"{doc_type.value}_{estimate.id}_{lang}.pdf"
    else:
        filename = f"{doc_type.value}_pl{packing.id}_{lang}.pdf"
    target = _storage_dir(project.id) / filename
    target.write_bytes(pdf)
    rel = f"documents/project_{project.id}/{filename}"

    doc = get_document(db, project.id, doc_type, lang, scope)
    if doc is None:
        doc = GeneratedDocument(
            project_id=project.id,
            scope=scope,
            doc_type=doc_type.value,
            language=lang,
            file_path=rel,
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
    estimate: Estimate | None = None  # версия сметы (для PDF сметы)
    packing: PackingList | None = None  # лист стенда (packing-лист, лист комплектации)


def status(db: Session, project: Project) -> list[DocStatus]:
    """Статусы документов проекта целиком — packing-лист по машинам (ТЗ §26.6).

    PDF смет — по версиям (estimate_status), packing-лист и лист комплектации —
    по листам стендов (packing_status).
    """
    packing_exists = bool(list_packings(db, project))
    result: list[DocStatus] = []
    for doc_type in DocumentType:
        if doc_type == DocumentType.ESTIMATE or doc_type in _PACKING_DOCS:
            continue
        available = packing_exists
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


def estimate_status(db: Session, project: Project, estimate: Estimate) -> list[DocStatus]:
    """Статусы PDF версии сметы на всех языках: сформирован ли, не устарел ли."""
    _, current_hash = render_html(db, project, DocumentType.ESTIMATE, "ru", estimate=estimate)
    scope = doc_scope(DocumentType.ESTIMATE, estimate)
    result: list[DocStatus] = []
    for lang in Language:
        doc = get_document(db, project.id, DocumentType.ESTIMATE, lang, scope)
        stale = bool(doc and doc.content_hash != current_hash)
        result.append(
            DocStatus(DocumentType.ESTIMATE, lang, True, doc is not None, stale, doc, estimate)
        )
    return result


def packing_status(db: Session, project: Project, packing: PackingList) -> list[DocStatus]:
    """Статусы packing-листа и листа комплектации стенда на всех языках."""
    result: list[DocStatus] = []
    for doc_type in _PACKING_DOCS:
        _, current_hash = render_html(db, project, doc_type, "ru", packing=packing)
        scope = doc_scope(doc_type, packing=packing)
        for lang in Language:
            doc = get_document(db, project.id, doc_type, lang, scope)
            stale = bool(doc and doc.content_hash != current_hash)
            result.append(
                DocStatus(doc_type, lang, True, doc is not None, stale, doc, packing=packing)
            )
    return result


def combined_status(db: Session, project: Project) -> list[DocStatus]:
    """Статусы общего packing-листа и листа комплектации проекта на всех языках."""
    result: list[DocStatus] = []
    for doc_type in _PACKING_DOCS:
        try:
            _, current_hash = render_html(db, project, doc_type, "ru")
            available = True
        except PdfUnavailable:
            current_hash, available = None, False
        for lang in Language:
            doc = get_document(db, project.id, doc_type, lang, "project")
            stale = bool(doc and current_hash and doc.content_hash != current_hash)
            result.append(DocStatus(doc_type, lang, available, doc is not None, stale, doc))
    return result
