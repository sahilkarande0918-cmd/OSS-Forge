"""Stock tracking."""
from __future__ import annotations

from collections import defaultdict

from shopkit.config import LOW_STOCK_THRESHOLD
from shopkit.models import Order, Product


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self):
        self._stock: dict[str, int] = defaultdict(int)
        self._products: dict[str, Product] = {}

    def add(self, product: Product, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._products[product.sku] = product
        self._stock[product.sku] += quantity

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve(self, order: Order) -> None:
        """Reserve stock for every line of an order, all-or-nothing."""
        for item in order.items:
            if self.available(item.product.sku) < item.quantity:
                raise OutOfStock(f"not enough {item.product.sku}: need {item.quantity}, "
                                 f"have {self.available(item.product.sku)}")
        for item in order.items:
            self._stock[item.product.sku] -= item.quantity

    def low_stock(self, threshold: int = LOW_STOCK_THRESHOLD) -> list[Product]:
        return sorted((self._products[sku] for sku, qty in self._stock.items() if qty <= threshold),
                      key=lambda p: p.sku)

    def restock_plan(self, target: int = 20) -> dict[str, int]:
        return {p.sku: target - self.available(p.sku) for p in self.low_stock()}
