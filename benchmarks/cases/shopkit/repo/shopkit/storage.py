"""JSON persistence for orders."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from shopkit.models import Customer, Order, Product
from shopkit.utils import parse_date


class StorageError(Exception):
    pass


def order_to_dict(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "placed_on": order.placed_on.isoformat(),
        "status": order.status,
        "customer": {"customer_id": order.customer.customer_id, "email": order.customer.email,
                     "state": order.customer.state, "loyalty_points": order.customer.loyalty_points},
        "items": [{"sku": i.product.sku, "name": i.product.name, "price": str(i.product.price),
                   "category": i.product.category, "quantity": i.quantity} for i in order.items],
    }


def order_from_dict(data: dict) -> Order:
    try:
        customer = Customer(**data["customer"])
        order = Order(data["order_id"], customer, placed_on=parse_date(data["placed_on"]),
                      status=data.get("status", "pending"))
        for item in data["items"]:
            product = Product(item["sku"], item["name"], Decimal(item["price"]), item.get("category", "general"))
            order.add(product, int(item["quantity"]))
        return order
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"invalid order record: {exc}") from exc


def save_orders(path: Path, orders: list[Order]) -> None:
    path.write_text(json.dumps([order_to_dict(o) for o in orders], indent=2), encoding="utf-8")


def load_orders(path: Path) -> list[Order]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StorageError(f"orders file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageError(f"orders file is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise StorageError("orders file must contain a JSON list")
    return [order_from_dict(r) for r in raw]
