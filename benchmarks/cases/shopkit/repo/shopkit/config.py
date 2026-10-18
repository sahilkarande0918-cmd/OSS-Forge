"""Static configuration for shopkit deployments."""
from decimal import Decimal

CURRENCY = "USD"

# Sales-tax rates per US state, expressed as fractions (0.0725 == 7.25%).
TAX_RATES = {
    "CA": Decimal("0.0725"),
    "NY": Decimal("0.04"),
    "TX": Decimal("0.0625"),
    "WA": Decimal("0.065"),
    "OR": Decimal("0"),
}

FREE_SHIPPING_THRESHOLD = Decimal("75.00")
SHIPPING_RATE_PER_KG = Decimal("4.50")
MIN_SHIPPING_FEE = Decimal("5.00")

LOW_STOCK_THRESHOLD = 5
LOYALTY_POINTS_PER_DOLLAR = 1
LOYALTY_REDEMPTION_VALUE = Decimal("0.01")
