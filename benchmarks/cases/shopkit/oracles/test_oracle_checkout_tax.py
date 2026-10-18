from decimal import Decimal

from shopkit.checkout import checkout_total
from shopkit.models import Customer, Order, Product


def _cart(state, amount):
    order = Order("O1", Customer("C1", "c@example.com", state))
    order.add(Product("TV-1", "Small TV", Decimal(amount), weight_kg=3.0))
    return order


def test_checkout_tax_ca():
    receipt = checkout_total(_cart("CA", "200.00"))
    assert receipt.tax == Decimal("14.50")
    assert receipt.total == Decimal("214.50")


def test_checkout_tax_with_coupon_ny():
    receipt = checkout_total(_cart("NY", "100.00"), coupon_percent=Decimal("10"))
    assert receipt.tax == Decimal("3.60")
    assert receipt.total == Decimal("93.60")
