<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Inventory costing reference (grounding corpus)

Original reference notes on inventory cost-flow methods, written for the Anki
Speedrun grounding corpus. Standard textbook material restated in our own
words; no third-party text is reproduced. Passages are split on `##` headings;
the passage id is `inventory.md#<slug>`.

## FIFO cost flow

First-in, first-out assigns the OLDEST costs to cost of goods sold and leaves
the NEWEST costs in ending inventory. Units sold are costed layer by layer
starting from beginning inventory, then the earliest purchase, and so on.
Under FIFO the balance-sheet inventory figure is closest to current
replacement cost, because the most recent purchase costs remain on the balance
sheet. When purchase prices are rising, FIFO reports lower cost of goods sold,
higher gross profit, higher pre-tax income, and higher ending inventory than
LIFO.

## LIFO cost flow

Last-in, first-out assigns the NEWEST costs to cost of goods sold and leaves
the OLDEST costs in ending inventory. Units sold are costed starting from the
most recent purchase layer and working backward. Under LIFO the income
statement matches current costs against current revenues, but the
balance-sheet inventory can carry very stale costs. When purchase prices are
rising, LIFO reports higher cost of goods sold, lower gross profit, lower
pre-tax income and lower taxes, and lower ending inventory than FIFO. LIFO is
permitted under US GAAP but prohibited under IFRS. Swapping the two methods -
costing sales from the oldest layers when LIFO is required, or the newest when
FIFO is required - reverses every one of these comparisons.

## Weighted average cost

The weighted average cost method spreads the total cost of goods available for
sale evenly over the units available:

    WAC per unit = (cost of beginning inventory + cost of purchases) / units available

Cost of goods sold is units sold times the weighted average unit cost, and
ending inventory is units remaining times the same unit cost. In a period of
rising prices the weighted-average results sit between FIFO and LIFO: cost of
goods sold is higher than FIFO's but lower than LIFO's, and ending inventory
is lower than FIFO's but higher than LIFO's.

## The inventory identity

For any cost-flow method, the cost of goods available for sale is fixed by
purchases and beginning inventory, and it splits between cost of goods sold
and ending inventory:

    COGS = beginning inventory + purchases - ending inventory

The cost-flow choice only decides WHERE the available cost goes - to the
income statement or to the balance sheet - not the total. This identity gives
an independent cross-check: computing ending inventory from the opposite end
of the layer stack and subtracting from goods available must reproduce the
directly-computed cost of goods sold. Confusing the two outputs - reporting
ending inventory where cost of goods sold is asked - is a mechanical but
common error.

## LIFO reserve

The LIFO reserve is the amount by which inventory reported under LIFO would
increase if restated to FIFO:

    FIFO inventory = LIFO inventory + LIFO reserve

The reserve is ADDED to LIFO inventory to reach the FIFO figure; subtracting
it goes the wrong direction and understates the restated inventory. For the
income statement, the restatement uses the CHANGE in the reserve over the
period:

    FIFO COGS = LIFO COGS - (increase in LIFO reserve)

A rising reserve (typical when prices rise) means FIFO cost of goods sold is
LOWER than LIFO's; a falling reserve reverses the sign. Analysts add the
reserve to equity (after tax effects) when comparing a LIFO firm with FIFO
peers.

## LIFO liquidation

A LIFO liquidation occurs when a LIFO firm sells more units than it purchases
in the period, dipping into old, low-cost layers. Cost of goods sold then
mixes in stale low costs, inflating gross margin in a way that does not
reflect current economics and is not sustainable. A shrinking LIFO reserve is
the standard signal of a liquidation in progress; disclosures quantify the
effect on income.
