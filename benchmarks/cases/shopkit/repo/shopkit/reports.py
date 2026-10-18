"""Sales reporting."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from shopkit.config import TAX_RATES
from shopkit.models import Order
from shopkit.pricing import round_money


def revenue(orders: list[Order]) -> Decimal:
    return round_money(sum((o.subtotal for o in orders), Decimal("0")))


def median_order_value(orders: list[Order]) -> Decimal:
    """Median of order subtotals."""
    if not orders:
        raise ValueError("no orders")
    values = sorted(o.subtotal for o in orders)
    mid = len(values) // 2
    return round_money(values[mid])


def top_products(orders: list[Order], n: int = 3) -> list[tuple[str, int]]:
    """Best-selling SKUs by units sold, ties broken by SKU."""
    units = Counter()
    for order in orders:
        for item in order.items:
            units[item.product.sku] += item.quantity
    return sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def revenue_by_day(orders: list[Order]) -> dict[date, Decimal]:
    days: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in orders:
        days[order.placed_on] += order.subtotal
    return {d: round_money(v) for d, v in sorted(days.items())}


def tax_liability(orders: list[Order]) -> dict[str, Decimal]:
    """Estimated sales tax owed per state (rates are fractions)."""
    owed: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in orders:
        owed[order.customer.state] += order.subtotal * TAX_RATES[order.customer.state]
    return {state: round_money(v) for state, v in sorted(owed.items())}
