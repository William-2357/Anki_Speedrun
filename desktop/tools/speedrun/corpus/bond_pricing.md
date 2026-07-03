<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Bond pricing reference (grounding corpus)

Original reference notes on bond price mechanics, written for the Anki
Speedrun grounding corpus. These passages give the retrieval index adjacent
but distinct material to discriminate against the duration and TVM passages.
Standard textbook material restated in our own words; no third-party text is
reproduced. Passage id is `bond_pricing.md#<slug>`.

## Price as discounted cash flows

A bond's price is the present value of its promised cash flows - the coupons
and the final principal - discounted at the yield to maturity per period. For
a bond paying coupon C per period for N periods with redemption value F and
periodic yield i:

    Price = C * [ 1 - (1+i)^-N ] / i + F * (1+i)^-N

The coupon stream is an ordinary annuity and the principal is a single sum, so
bond pricing is a direct application of the two time-value building blocks.

## Price-yield relationship

Bond price and yield move in opposite directions: raising the discount rate
lowers every present value. The relationship is curved (convex), not linear -
successive equal yield increases produce successively smaller price declines.
A bond priced above par carries a coupon rate above its yield (premium); below
par, below its yield (discount); exactly at par the coupon rate equals the
yield. As a bond approaches maturity its price pulls to par regardless of
where it started.

## Semiannual conventions

Most fixed-rate government and corporate bonds pay coupons twice a year. The
quoted (nominal) annual yield is twice the semiannual periodic yield, and the
quoted annual coupon rate is twice the semiannual coupon payment rate. Pricing
a 10-year semiannual bond therefore uses N = 20 periods, a periodic coupon of
half the annual coupon, and a periodic yield of half the quoted yield.
Applying annual figures to semiannual bonds (or halving only one of the coupon
and the yield) misprices the bond and distorts any duration statistic computed
from it.

## Zero-coupon bonds

A zero-coupon bond makes a single payment at maturity and sells at a discount
to face value. Its price is F * (1+i)^-N. With no interim cash flows, its
Macaulay duration equals its maturity, making zeros the cleanest instrument
for matching a single dated liability. Zeros are more sensitive to yield
changes than coupon bonds of the same maturity, precisely because all value
sits at the final date.

## Accrued interest and full price

Between coupon dates the buyer compensates the seller for the coupon accrued
since the last payment date. The full (dirty) price is the quoted (clean)
price plus accrued interest: full = clean + accrued. Duration and price-change
calculations operate on the full price, since that is the amount actually
invested.
