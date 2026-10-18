# Checkout charges almost no sales tax

A $200.00 cart shipped to California should get 7.25% sales tax ($14.50) for a receipt total of $214.50
(shipping is free over $75).

Expected: tax 14.50, total 214.50
Actual: tax 0.15, total 200.15

The tax report (`tax_liability`) shows correct numbers for the same orders, so the rates themselves seem fine.
