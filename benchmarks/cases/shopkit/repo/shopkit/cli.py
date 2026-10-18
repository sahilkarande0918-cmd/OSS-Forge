"""Command-line entry point: `python -m shopkit.cli report orders.json`."""
from __future__ import annotations

import argparse
from pathlib import Path

from shopkit.reports import median_order_value, revenue, tax_liability, top_products
from shopkit.storage import StorageError, load_orders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shopkit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="print a sales summary")
    rep.add_argument("orders", type=Path)
    args = parser.parse_args(argv)
    try:
        orders = load_orders(args.orders)
    except StorageError as exc:
        print(f"error: {exc}")
        return 1
    print(f"Orders: {len(orders)}")
    print(f"Revenue: {revenue(orders)}")
    print(f"Median order value: {median_order_value(orders)}")
    print("Top products: " + ", ".join(f"{sku} ({n})" for sku, n in top_products(orders)))
    for state, owed in tax_liability(orders).items():
        print(f"Tax owed {state}: {owed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
