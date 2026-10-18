"""Customer-facing message templates."""
from __future__ import annotations

from shopkit.checkout import Receipt
from shopkit.config import CURRENCY
from shopkit.models import Order
from shopkit.shipping import estimate_delivery_days
from shopkit.utils import mask_email


def format_money(amount) -> str:
    return f"{amount:,.2f} {CURRENCY}"


def order_confirmation(order: Order, receipt: Receipt, express: bool = False) -> str:
    days = estimate_delivery_days(order.customer.state, express)
    lines = [f"Thanks for your order {order.order_id}!", ""]
    for item in order.items:
        lines.append(f"  {item.quantity} x {item.product.name:<24} {format_money(item.subtotal)}")
    lines += [
        "",
        f"  Subtotal: {format_money(receipt.subtotal)}",
        f"  Discount: -{format_money(receipt.discount)}",
        f"  Tax:      {format_money(receipt.tax)}",
        f"  Shipping: {format_money(receipt.shipping)}",
        f"  Total:    {format_money(receipt.total)}",
        "",
        f"Estimated delivery in {days} business day{'s' if days != 1 else ''}.",
    ]
    return "\n".join(lines)


def shipping_notice(order: Order, tracking_number: str) -> str:
    return (f"Order {order.order_id} for {mask_email(order.customer.email)} has shipped. "
            f"Tracking number: {tracking_number}")
