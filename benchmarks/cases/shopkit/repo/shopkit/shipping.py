"""Shipping cost estimation."""
from __future__ import annotations

from decimal import Decimal

from shopkit.config import FREE_SHIPPING_THRESHOLD, MIN_SHIPPING_FEE, SHIPPING_RATE_PER_KG
from shopkit.models import Order
from shopkit.pricing import round_money

EXPRESS_MULTIPLIER = Decimal("1.8")


def shipping_cost(order: Order, express: bool = False) -> Decimal:
    """Weight-based shipping; free above the threshold unless express delivery is requested."""
    if not order.items:
        return Decimal("0.00")
    if order.subtotal >= FREE_SHIPPING_THRESHOLD and not express:
        return Decimal("0.00")
    cost = max(MIN_SHIPPING_FEE, SHIPPING_RATE_PER_KG * Decimal(str(order.total_weight)))
    if express:
        cost *= EXPRESS_MULTIPLIER
    return round_money(cost)


def estimate_delivery_days(state: str, express: bool = False) -> int:
    west = {"CA", "OR", "WA"}
    days = 3 if state in west else 5
    return max(1, days - 2) if express else days
