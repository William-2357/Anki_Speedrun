<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Bond duration reference (grounding corpus)

Original reference notes on the duration family, written for the Anki Speedrun
grounding corpus. Standard textbook material restated in our own words; no
third-party text is reproduced. Passages are split on `##` headings; the
passage id is `duration.md#<slug>`.

## Macaulay duration

Macaulay duration is the weighted-average time to receipt of a bond's cash
flows, where each weight is the present value of that cash flow divided by the
bond's full price. For a bond with cash flow CF_t at period t and periodic
discount factor v = 1/(1+i):

    MacDur (in periods) = [ sum over t of t * CF_t * v^t ] / [ sum over t of CF_t * v^t ]

Divide by the number of coupon periods per year (k) to express Macaulay
duration in years. A useful closed form for a level-coupon bond paying coupon
rate c per period with yield i per period over N periods is:

    MacDur (in periods) = (1+i)/i - [ 1+i+N*(c-i) ] / [ c*((1+i)^N - 1) + i ]

For a zero-coupon bond, Macaulay duration equals its time to maturity. Adding
coupons always shortens Macaulay duration below maturity because earlier cash
flows pull the weighted-average time forward. Macaulay duration is measured in
time units (years), not in percent.

## Modified duration

Modified duration rescales Macaulay duration into a price-sensitivity measure.
With an annual yield y quoted with k compounding periods per year:

    ModDur = MacDur / (1 + y/k)

Modified duration is the (negative of the) percentage change in the bond's
price for a one-unit change in its yield to maturity, per the first-order
approximation. Because of the division by (1 + y/k), modified duration is
always smaller than Macaulay duration whenever the yield is positive. A very
common error is quoting the Macaulay figure where the modified figure is
required, which overstates rate sensitivity by the factor (1 + y/k). A second
common error is dividing by (1 + y) when the bond pays semiannually: with
semiannual compounding the divisor must use the semiannual yield, that is
(1 + y/2).

## Approximate price change using duration

The first-order estimate of the percentage full-price change for a change in
yield of delta-y (in decimal) is:

    %ΔPrice ≈ -ModDur * Δy

The minus sign is essential: prices and yields move inversely, so a yield
increase produces a price decline. Students frequently drop the sign and
report a price gain when yields rise. The estimate uses modified duration, not
Macaulay duration; using Macaulay in this formula overstates the move. The
approximation is linear, so it ignores convexity and becomes less accurate for
large yield changes; for small moves (a few basis points) it is very close.

## Money duration and PVBP

Money duration (also called dollar duration) is modified duration multiplied
by the full price of the position: MoneyDur = ModDur * P. It converts the
percentage sensitivity into currency terms: ΔPrice ≈ -MoneyDur * Δy. The price
value of a basis point (PVBP) is the money change for a one-basis-point yield
move: PVBP = MoneyDur * 0.0001. Money duration and PVBP are stated in currency
units, unlike modified duration which is stated per unit of yield.

## Effective duration

Effective duration measures price sensitivity using a parallel shift of the
benchmark yield curve rather than the bond's own yield to maturity:

    EffDur = [ PV(-Δcurve) - PV(+Δcurve) ] / ( 2 * Δcurve * PV(0) )

It is computed by repricing the bond after bumping the curve up and down.
Effective duration is the appropriate measure for bonds whose cash flows change
when rates change - callable bonds, putable bonds, and mortgage-backed
securities - because yield-based (modified) duration assumes fixed cash flows.
For an option-free bond, effective duration and modified duration are close;
for a callable bond near its call price they diverge sharply, with effective
duration falling as the call caps price appreciation.

## Convexity adjustment

Duration alone linearizes the price-yield curve. The second-order correction
adds a convexity term:

    %ΔPrice ≈ -ModDur * Δy + 0.5 * Convexity * (Δy)^2

For an option-free bond convexity is positive, so the duration-only estimate
understates the price rise when yields fall and overstates the price fall when
yields rise. The convexity adjustment matters most for large yield changes and
long-maturity bonds.

## Compounding conventions for duration

Yields on semiannual-pay bonds are conventionally quoted as twice the
semiannual yield (a nominal annual rate). When converting Macaulay to modified
duration, the divisor must match the compounding of the quoted yield: divide
by (1 + y/2) for semiannual-pay, by (1 + y) for annual-pay, and in general by
(1 + y/k) for k periods per year. Mixing conventions - for example applying an
annual divisor to a semiannual-pay bond - shifts the modified duration by
roughly half the periodic yield and is a classic exam trap.
