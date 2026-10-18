"""Money helpers: rounding, discounts and tax."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def round_money(amount) -> Decimal:
    """Round to whole cents using commercial rounding (half up)."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def apply_discount(price: Decimal, percent_off: Decimal) -> Decimal:
    """Return `price` reduced by `percent_off` percent (e.g. 15 for 15% off)."""
    if not 0 <= percent_off <= 100:
        raise ValueError("percent_off must be between 0 and 100")
    return round_money(price * (Decimal(100) - percent_off) / Decimal(100))


def compute_tax(amount: Decimal, rate_percent: Decimal) -> Decimal:
    """Tax owed on `amount` at `rate_percent` percent (e.g. 7.25 for 7.25%)."""
    if rate_percent < 0:
        raise ValueError("tax rate cannot be negative")
    return round_money(Decimal(amount) * Decimal(rate_percent) / Decimal(100))


def split_evenly(amount: Decimal, parts: int) -> list[Decimal]:
    """Split an amount into `parts` cent-exact shares; the first shares absorb the remainder."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    cents = int(round_money(amount) * 100)
    base, extra = divmod(cents, parts)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(parts)]
