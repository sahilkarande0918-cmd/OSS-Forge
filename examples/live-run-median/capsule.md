# Proof-of-Fix Capsule — Sales report shows the wrong median order value

**Status: ✅ VERIFIED** — regression test fails on 2e1a256532 and passes with the patch; 11/11 existing tests pass with no new failures

- Run: `20261018-145324-sales-report-shows-the-wrong-m` (2026-10-18T09:25:24+00:00) · oss-forge 0.1.0
- Repository: `shopkit-demo-317cecf975` 
- Base commit: `2e1a25653290565bfe91fe3bb49c5d7bc89ec05a`
- Issue: examples\issue-median.md
- Model: `gemma-4-26b-a4b-it` · API calls: 3 · tokens in/out: 10733/5153
- Context: 2485 tokens from 9 file(s) (repository ≈ 5277 tokens)
- Sandbox: host: host execution explicitly approved (--allow-host-exec); env scrubbed

## Verification checks

- ✔ patch_applies
- ✔ regression_fails_before
- ✔ regression_passes_after
- ✔ existing_suite_ran
- ✔ no_new_failures
- ✔ only_expected_files_changed
- ✔ syntax_ok
- ✔ whitespace_ok

## Reproduction

REPRODUCED: assertion fails on the original code

## Root cause (model explanation)

The `median_order_value` function calculates the median by simply selecting the middle element at `len(values) // 2`, which is incorrect for an even number of elements where the median should be the average of the two middle elements.

## Relevant source locations

- `shopkit/cli.py:11-28` `main`
- `shopkit/pricing.py:9-11` `round_money`
- `shopkit/reports.py:17-23` `median_order_value`
- `shopkit/models.py:40-67` `Order`
- `shopkit/reports.py:42-47` `tax_liability`
- `shopkit/reports.py:13-14` `revenue`
- `shopkit/reports.py:26-32` `top_products`
- `shopkit/reports.py:35-39` `revenue_by_day`

## Test evidence

| run | runner | exit | passed | failed | errors | seconds |
|---|---|---|---|---|---|---|
| reproduction | host | 1 | 0 | 0 | 0 | 0.68 |
| regression_before | host | 1 | 0 | 1 | 0 | 0.821 |
| suite_before | host | 0 | 11 | 0 | 0 | 0.936 |
| regression_after | host | 0 | 1 | 0 | 0 | 0.71 |
| suite_after | host | 0 | 11 | 0 | 0 | 0.776 |

## Patch

```diff
diff --git a/shopkit/reports.py b/shopkit/reports.py
index 64f8e68..2a75a73 100644
--- a/shopkit/reports.py
+++ b/shopkit/reports.py
@@ -20,6 +20,8 @@ def median_order_value(orders: list[Order]) -> Decimal:
         raise ValueError("no orders")
     values = sorted(o.subtotal for o in orders)
     mid = len(values) // 2
+    if len(values) % 2 == 0:
+        return round_money((values[mid - 1] + values[mid]) / 2)
     return round_money(values[mid])
```

## Regression test (`tests/test_median_regression.py`)

```python
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
```

## Limitations

- A passing regression test shows the reported symptom is gone for the tested inputs; it does not prove the issue is fixed for every input or platform. Review the diff.
- Tests ran on the host (approved), not in a container; results depend on the local environment.
- Root-cause explanation and hypotheses are model-generated and unverified beyond the tests listed.

## Reproduce this evidence

```bash
python -m oss_forge verify --run-id 20261018-145324-sales-report-shows-the-wrong-m --repo <path-to-repo> --execute
# or manually:
git worktree add --detach ../verify 2e1a25653290565bfe91fe3bb49c5d7bc89ec05a
cp <OSS_FORGE_HOME>/runs/20261018-145324-sales-report-shows-the-wrong-m/regression_test.py ../verify/tests/test_median_regression.py
cd ../verify && python -m pytest tests/test_median_regression.py   # expected: FAIL (bug present)
git apply <OSS_FORGE_HOME>/runs/20261018-145324-sales-report-shows-the-wrong-m/patch.diff
python -m pytest   # expected: PASS
```
