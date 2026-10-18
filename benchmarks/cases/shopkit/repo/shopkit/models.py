"""Core domain models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    category: str = "general"
    weight_kg: float = 0.5

    def __post_init__(self):
        if self.price < 0:
            raise ValueError(f"price must be non-negative for {self.sku}")


@dataclass
class LineItem:
    product: Product
    quantity: int = 1

    @property
    def subtotal(self) -> Decimal:
        return self.product.price * self.quantity


@dataclass
class Customer:
    customer_id: str
    email: str
    state: str
    loyalty_points: int = 0


@dataclass
class Order:
    order_id: str
    customer: Customer
    items: list[LineItem] = field(default_factory=list)
    placed_on: date = field(default_factory=date.today)
    status: str = "pending"

    def add(self, product: Product, quantity: int = 1) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        for item in self.items:
            if item.product.sku == product.sku:
                item.quantity += quantity
                return
        self.items.append(LineItem(product, quantity))

    @property
    def subtotal(self) -> Decimal:
        return sum((i.subtotal for i in self.items), Decimal("0"))

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def total_weight(self) -> float:
        return sum(i.product.weight_kg * i.quantity for i in self.items)
