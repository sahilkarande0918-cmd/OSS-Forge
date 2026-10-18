## Problem
Sales report shows the wrong median order value

## Root cause
The `median_order_value` function calculates the median by simply selecting the middle element at `len(values) // 2`, which is incorrect for an even number of elements where the median should be the average of the two middle elements.

## Changes
- `shopkit/reports.py` (+2/-0)

## Tests
- Added regression test `tests/test_median_regression.py`
- Regression test on base commit (expected to fail): exit 1, 0 passed / 1 failed (host)
- Regression test with this patch: exit 0, 1 passed / 0 failed (host)
- Existing test suite with this patch: exit 0, 11 passed / 0 failed (host)

## Verification
**VERIFIED** — regression test fails on 2e1a256532 and passes with the patch; 11/11 existing tests pass with no new failures
Base commit `2e1a25653290`; evidence in the OSS Forge Proof-of-Fix capsule.

## Related issue
examples\issue-median.md

## Known limitations
- A passing regression test shows the reported symptom is gone for the tested inputs; it does not prove the issue is fixed for every input or platform. Review the diff.
- Tests ran on the host (approved), not in a container; results depend on the local environment.
- Root-cause explanation and hypotheses are model-generated and unverified beyond the tests listed.