from datetime import date
from decimal import Decimal

import pytest

from shopkit.models import Customer, Order, Product


@pytest.fixture
def mug():
    return Product("MUG-1", "Ceramic Mug", Decimal("12.00"), "kitchen", 0.4)


@pytest.fixture
def lamp():
    return Product("LAMP-2", "Desk Lamp", Decimal("40.00"), "office", 1.2)


@pytest.fixture
def make_order():
    def _make(order_id="O1", state="CA", items=(), placed_on=date(2024, 3, 1)):
        order = Order(order_id, Customer("C1", "ana@example.com", state, 100), placed_on=placed_on)
        for product, qty in items:
            order.add(product, qty)
        return order
    return _make
