"""Customer loyalty points."""
from __future__ import annotations

from decimal import Decimal

from shopkit.config import LOYALTY_POINTS_PER_DOLLAR, LOYALTY_REDEMPTION_VALUE
from shopkit.models import Customer
from shopkit.pricing import round_money


class InsufficientPoints(Exception):
    pass


def points_for_purchase(amount: Decimal) -> int:
    return int(amount) * LOYALTY_POINTS_PER_DOLLAR


def award_points(customer: Customer, amount: Decimal) -> int:
    earned = points_for_purchase(amount)
    customer.loyalty_points += earned
    return earned


def redeem_points(customer: Customer, points: int) -> Decimal:
    """Spend points and return their cash value."""
    if points < 0:
        raise ValueError("points must be non-negative")
    if points > customer.loyalty_points:
        raise InsufficientPoints(f"customer {customer.customer_id} has only {customer.loyalty_points} points")
    customer.loyalty_points -= points
    return round_money(LOYALTY_REDEMPTION_VALUE * points)
