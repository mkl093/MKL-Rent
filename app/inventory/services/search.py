"""Подсказки поиска по складу при вводе (ТЗ §25).

Три группы: модели (название/производитель), комплекты (название) и экземпляры
(начало штрих-кода или серийного номера). В каждой группе — не больше
SUGGEST_LIMIT строк, совпадения с начала строки идут первыми. Архивные модели
и комплекты не подсказываются.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, selectinload

from app.inventory.models import EquipmentItem, EquipmentModel, Kit
from app.inventory.services.equipment import stock_quantity
from app.inventory.services.items import _sql_normalized_barcode, normalize_barcode

SUGGEST_MIN_LENGTH = 2
SUGGEST_LIMIT = 8

LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Экранировать %, _ и \\ — пользовательский ввод не должен работать как шаблон LIKE."""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def item_code_match(query: str):
    """Условие: штрих-код (нормализованный) или серийный номер экземпляра начинается с query.

    Штрих-код сравнивается без учёта регистра и пробелов — как глобальный поиск
    по штрих-коду (items.find_by_barcode). None — если искать нечего.
    """
    conditions = []
    normalized = normalize_barcode(query)
    if normalized:
        conditions.append(
            _sql_normalized_barcode(EquipmentItem.barcode).like(
                escape_like(normalized) + "%", escape=LIKE_ESCAPE
            )
        )
    text = query.strip()
    if text:
        conditions.append(
            EquipmentItem.serial_number.ilike(escape_like(text) + "%", escape=LIKE_ESCAPE)
        )
    return or_(*conditions) if conditions else None


def highlight(text: str | None, query: str) -> tuple[str, str, str]:
    """Разбить текст на (до, совпадение, после) для подсветки в шаблоне.

    Разметку делает шаблон (с автоэкранированием) — здесь только строки, без HTML.
    Если совпадения нет — всё в первой части.
    """
    text = text or ""
    needle = query.strip()
    lowered = text.lower()
    pos = lowered.find(needle.lower()) if needle else -1
    # lower() может менять длину строки для редких символов — тогда без подсветки.
    if pos < 0 or len(lowered) != len(text):
        return text, "", ""
    end = pos + len(needle)
    return text[:pos], text[pos:end], text[end:]


@dataclass
class ModelSuggestion:
    model: EquipmentModel
    stock: int


@dataclass
class Suggestions:
    query: str = ""
    models: list[ModelSuggestion] = field(default_factory=list)
    kits: list[Kit] = field(default_factory=list)
    items: list[EquipmentItem] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.models or self.kits or self.items)


def suggest(db: Session, query: str | None, limit: int = SUGGEST_LIMIT) -> Suggestions | None:
    """Подсказки для строки поиска. None — запрос короче SUGGEST_MIN_LENGTH."""
    q = (query or "").strip()
    if len(q) < SUGGEST_MIN_LENGTH:
        return None
    escaped = escape_like(q)
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"

    name_first = case((EquipmentModel.name.ilike(prefix, escape=LIKE_ESCAPE), 0), else_=1)
    models = (
        db.execute(
            select(EquipmentModel)
            .options(selectinload(EquipmentModel.category))
            .where(
                EquipmentModel.is_archived.is_(False),
                or_(
                    EquipmentModel.name.ilike(contains, escape=LIKE_ESCAPE),
                    EquipmentModel.manufacturer.ilike(contains, escape=LIKE_ESCAPE),
                ),
            )
            .order_by(name_first, EquipmentModel.name)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    kit_first = case((Kit.name.ilike(prefix, escape=LIKE_ESCAPE), 0), else_=1)
    kits = (
        db.execute(
            select(Kit)
            .where(Kit.is_archived.is_(False), Kit.name.ilike(contains, escape=LIKE_ESCAPE))
            .order_by(kit_first, Kit.name)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    items: list[EquipmentItem] = []
    code_match = item_code_match(q)
    if code_match is not None:
        items = list(
            db.execute(
                select(EquipmentItem)
                .join(EquipmentModel, EquipmentModel.id == EquipmentItem.model_id)
                .options(selectinload(EquipmentItem.model))
                .where(EquipmentModel.is_archived.is_(False), code_match)
                .order_by(EquipmentItem.barcode, EquipmentItem.id)
                .limit(limit)
            )
            .scalars()
            .all()
        )

    return Suggestions(
        query=q,
        models=[ModelSuggestion(model=m, stock=stock_quantity(db, m)) for m in models],
        kits=list(kits),
        items=items,
    )
