# Benchmark: suite `smoke` (context)

- Date: 20261018T084503Z
- Model: `n/a`
- Context budget: 2500 tokens
- Command: `python -m oss_forge benchmark --suite smoke --mode context --budget 2500`

| case | repo tokens | OSS Forge hit (rank) / tokens | path-order hit / tokens | keyword-grep hit / tokens |
|---|---|---|---|---|
| A-median-logic | 5277 | yes (#1) / 2001 | no / 2487 | yes / 2416 |
| B-checkout-tax-multifile | 5277 | yes (#2) / 2408 | yes / 2487 | yes / 2275 |
| D-slugify-digits | 5277 | yes (#2) / 1601 | no / 2487 | no / 2321 |
