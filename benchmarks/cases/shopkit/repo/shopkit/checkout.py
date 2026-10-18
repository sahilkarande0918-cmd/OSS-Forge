"""Checkout: turns an order into a priced receipt."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from shopkit.config import TAX_RATES
from shopkit.loyalty import redeem_points
from shopkit.models import Order
from shopkit.pricing import apply_discount, compute_tax, round_money
from shopkit.shipping import shipping_cost


@dataclass
class Receipt:
    order_id: str
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    shipping: Decimal
    loyalty_credit: Decimal
    total: Decimal


def checkout_total(order: Order, coupon_percent: Decimal = Decimal("0"), express: bool = False,
                   use_points: int = 0) -> Receipt:
    """Price an order: discount, then tax on the discounted subtotal, then shipping and loyalty credit."""
    if order.customer.state not in TAX_RATES:
        raise KeyError(f"unsupported state: {order.customer.state}")
    subtotal = order.subtotal
    discounted = apply_discount(subtotal, coupon_percent)
    tax = compute_tax(discounted, TAX_RATES[order.customer.state])
    shipping = shipping_cost(order, express=express)
    credit = redeem_points(order.customer, use_points) if use_points else Decimal("0.00")
    total = round_money(max(Decimal("0"), discounted + tax + shipping - credit))
    return Receipt(order.order_id, subtotal, subtotal - discounted, tax, shipping, credit, total)
