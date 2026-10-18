"""Product catalog with search and category browsing."""
from __future__ import annotations

from decimal import Decimal

from shopkit.models import Product
from shopkit.utils import slugify


class Catalog:
    def __init__(self, products: list[Product] | None = None):
        self._by_sku: dict[str, Product] = {}
        for p in products or []:
            self.add(p)

    def add(self, product: Product) -> None:
        if product.sku in self._by_sku:
            raise ValueError(f"duplicate sku {product.sku}")
        self._by_sku[product.sku] = product

    def get(self, sku: str) -> Product:
        return self._by_sku[sku]

    def search(self, query: str) -> list[Product]:
        words = query.lower().split()
        return [p for p in self._by_sku.values() if all(w in p.name.lower() for w in words)]

    def by_category(self, category: str) -> list[Product]:
        return sorted((p for p in self._by_sku.values() if p.category == category), key=lambda p: p.price)

    def price_range(self, low: Decimal, high: Decimal) -> list[Product]:
        return [p for p in self._by_sku.values() if low <= p.price <= high]

    def url_for(self, sku: str) -> str:
        product = self.get(sku)
        return f"/products/{slugify(product.name)}-{product.sku.lower()}"
