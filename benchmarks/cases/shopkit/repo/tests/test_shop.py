from decimal import Decimal

import pytest

from shopkit.catalog import Catalog
from shopkit.inventory import Inventory, OutOfStock
from shopkit.loyalty import InsufficientPoints, redeem_points
from shopkit.reports import revenue, tax_liability, top_products
from shopkit.shipping import shipping_cost
from shopkit.storage import load_orders, save_orders
from shopkit.utils import chunked, parse_date


def test_inventory_reserve_all_or_nothing(mug, lamp, make_order):
    inv = Inventory()
    inv.add(mug, 3)
    inv.add(lamp, 1)
    with pytest.raises(OutOfStock):
        inv.reserve(make_order(items=[(mug, 2), (lamp, 2)]))
    assert inv.available("MUG-1") == 3
    inv.reserve(make_order(items=[(mug, 2)]))
    assert inv.available("MUG-1") == 1
    assert [p.sku for p in inv.low_stock()] == ["LAMP-2", "MUG-1"]


def test_shipping_free_over_threshold(mug, lamp, make_order):
    assert shipping_cost(make_order(items=[(lamp, 2)])) == Decimal("0.00")
    assert shipping_cost(make_order(items=[(mug, 1)])) == Decimal("5.00")


def test_reports(mug, lamp, make_order):
    orders = [make_order("O1", "CA", [(mug, 2)]), make_order("O2", "NY", [(lamp, 1), (mug, 1)])]
    assert revenue(orders) == Decimal("76.00")
    assert top_products(orders) == [("MUG-1", 3), ("LAMP-2", 1)]
    assert tax_liability(orders) == {"CA": Decimal("1.74"), "NY": Decimal("2.08")}


def test_loyalty_redeem(make_order):
    customer = make_order().customer
    assert redeem_points(customer, 50) == Decimal("0.50")
    with pytest.raises(InsufficientPoints):
        redeem_points(customer, 500)


def test_storage_roundtrip(tmp_path, mug, make_order):
    path = tmp_path / "orders.json"
    save_orders(path, [make_order(items=[(mug, 2)])])
    loaded = load_orders(path)
    assert loaded[0].subtotal == Decimal("24.00")


def test_utils():
    assert list(chunked(range(5), 2)) == [[0, 1], [2, 3], [4]]
    assert parse_date("03/01/2024").isoformat() == "2024-03-01"


def test_catalog_search(mug, lamp):
    catalog = Catalog([mug, lamp])
    assert catalog.search("desk") == [lamp]
