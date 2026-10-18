from decimal import Decimal

from shopkit.models import Customer, Order, Product
from shopkit.reports import median_order_value


def _order(i, amount):
    order = Order(f"O{i}", Customer("C", "c@example.com", "CA"))
    order.add(Product(f"S{i}", "Thing", Decimal(amount)))
    return order


def test_median_even_count():
    orders = [_order(i, a) for i, a in enumerate(["10", "20", "30", "40"])]
    assert median_order_value(orders) == Decimal("25.00")


def test_median_odd_count():
    orders = [_order(i, a) for i, a in enumerate(["30", "10", "20"])]
    assert median_order_value(orders) == Decimal("20.00")
