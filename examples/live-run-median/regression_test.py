from decimal import Decimal
import pytest
from shopkit.models import Order, Product, Customer
from shopkit.reports import median_order_value

def test_median_order_value_even_number_of_orders(make_order):
    """Regression test for even number of orders median calculation."""
    p = Product("TEST", "Test Product", Decimal("10.00"))
    
    # Create orders with subtotals: 10.00, 20.00, 30.00, 40.00
    orders = [
        make_order(order_id="O1", items=[(p, 1)]),  # 10.00
        make_order(order_id="O2", items=[(p, 2)]),  # 20.00
        make_order(order_id="O3", items=[(p, 3)]),  # 30.00
        make_order(order_id="O4", items=[(p, 4)]),  # 40.00
    ]
    
    # The median of [10, 20, 30, 40] should be (20 + 30) / 2 = 25.00
    # Note: The make_order fixture adds items via order.add(product, qty)
    # We need to adjust the subtotal manually if we want specific values easily
    # but we can just use different products or quantities.
    
    # Re-creating logic to ensure exact values as requested by issue
    o1 = make_order(order_id="1", items=[(Product("A", "A", Decimal("10.00")), 1)])
    o2 = make_order(order_id="2", items=[(Product("B", "B", Decimal("20.00")), 1)])
    o3 = make_order(order_id="3", items=[(Product("C", "C", Decimal("30.00")), 1)])
    o4 = make_order(order_id="4", items=[(Product("D", "D", Decimal("40.00")), 1)])
    
    assert median_order_value([o1, o2, o3, o4]) == Decimal("25.00")
