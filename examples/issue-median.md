# Sales report shows the wrong median order value

When there is an even number of orders the sales report prints the wrong median.

Steps to reproduce:
1. Create four orders worth 10, 20, 30 and 40 dollars.
2. Run `shopkit report orders.json`.

Expected: `Median order value: 25.00` (the mean of the two middle values)
Actual: `Median order value: 30.00`

With an odd number of orders the value looks right.
