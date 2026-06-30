"""Локализация дат, чисел, веса, объёма и валюты для PDF (ТЗ §26.3).

RU: 30.06.2026 · 125,4 кг · 1 250,00 €
EN: 30 June 2026 · 125.4 kg · €1,250.00
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

EN_MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def format_date(value: date | None, lang: str) -> str:
    if value is None:
        return "—"
    if lang == "ru":
        return value.strftime("%d.%m.%Y")
    return f"{value.day} {EN_MONTHS[value.month]} {value.year}"


def _split(value: Decimal, decimals: int) -> tuple[bool, str, str]:
    q = Decimal(value).quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)
    neg = q < 0
    s = f"{abs(q):.{decimals}f}"
    int_part, frac = s.split(".") if decimals else (s, "")
    return neg, int_part, frac


def _group(int_part: str, sep: str) -> str:
    n = len(int_part)
    chunks = [int_part[max(0, i - 3) : i] for i in range(n, 0, -3)][::-1]
    return sep.join(chunks)


def format_money(value: Decimal, lang: str) -> str:
    neg, int_part, frac = _split(value, 2)
    if lang == "ru":
        body = f"{_group(int_part, ' ')},{frac} €"
    else:
        body = f"€{_group(int_part, ',')}.{frac}"
    return f"-{body}" if neg else body


def _decimal_str(value: Decimal, decimals: int, lang: str) -> str:
    neg, int_part, frac = _split(value, decimals)
    dot = "," if lang == "ru" else "."
    body = f"{int_part}{dot}{frac}" if decimals else int_part
    return f"-{body}" if neg else body


def format_weight(value: Decimal, lang: str) -> str:
    unit = "кг" if lang == "ru" else "kg"
    return f"{_decimal_str(value, 1, lang)} {unit}"


def format_volume(value: Decimal, lang: str) -> str:
    unit = "м³" if lang == "ru" else "m³"
    return f"{_decimal_str(value, 3, lang)} {unit}"


def format_percent(value: Decimal, lang: str) -> str:
    # Целое если без дробной части, иначе один знак.
    if value == value.to_integral_value():
        return f"{int(value)}%"
    return f"{_decimal_str(value, 1, lang)}%"
