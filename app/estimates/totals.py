"""Расчёт итогов сметы: подытог, скидка, VAT (ТЗ §16.4, §16.7)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Округлить денежную величину до копеек (2 знака, half-up)."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass
class EstimateTotals:
    subtotal: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    after_discount: Decimal
    vat_percent: Decimal
    vat_amount: Decimal
    total: Decimal

    @property
    def has_discount(self) -> bool:
        """Скидка показывается только если больше 0% (ТЗ §16.7)."""
        return self.discount_percent > 0


def compute_totals(
    line_totals: list[Decimal], discount_percent: Decimal, vat_percent: Decimal
) -> EstimateTotals:
    subtotal = money(sum(line_totals, Decimal("0")))
    discount_amount = money(subtotal * discount_percent / Decimal("100"))
    after_discount = money(subtotal - discount_amount)
    vat_amount = money(after_discount * vat_percent / Decimal("100"))
    total = money(after_discount + vat_amount)
    return EstimateTotals(
        subtotal=subtotal,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        after_discount=after_discount,
        vat_percent=vat_percent,
        vat_amount=vat_amount,
        total=total,
    )
