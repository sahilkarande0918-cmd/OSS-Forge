from decimal import Decimal

import pytest

from shopkit.pricing import apply_discount, compute_tax, round_money, split_evenly


def test_round_money_half_up():
    assert round_money(Decimal("2.345")) == Decimal("2.35")


def test_apply_discount():
    assert apply_discount(Decimal("80.00"), Decimal("25")) == Decimal("60.00")
    with pytest.raises(ValueError):
        apply_discount(Decimal("10"), Decimal("120"))


def test_compute_tax_uses_percent():
    assert compute_tax(Decimal("100.00"), Decimal("8")) == Decimal("8.00")
    assert compute_tax(Decimal("200.00"), Decimal("7.25")) == Decimal("14.50")


def test_split_evenly():
    assert split_evenly(Decimal("10.00"), 3) == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
