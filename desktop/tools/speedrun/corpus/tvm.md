<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Time value of money reference (grounding corpus)

Original reference notes on time-value-of-money mechanics, written for the
Anki Speedrun grounding corpus. Standard textbook material restated in our own
words; no third-party text is reproduced. Passages are split on `##` headings;
the passage id is `tvm.md#<slug>`.

## Future value of a single sum

A single amount PV invested at a stated annual rate r, compounded m times per
year for n years, grows to:

    FV = PV * (1 + r/m)^(m*n)

The rate per period is r/m and the number of periods is m*n; both must be on
the same per-period basis. Two classic errors: using the stated annual rate as
if it were the periodic rate (skipping the division by m), and miscounting the
number of periods by one (for example compounding 11 quarters instead of 12,
or treating a deposit made today as if it earned one fewer or one extra
period). Simple interest, PV * (1 + r*n), ignores interest-on-interest and
always understates the compound result when n exceeds one period.

## Present value of a single sum

Discounting reverses compounding. The present value of a cash flow FV due in
n years at stated annual rate r compounded m times per year is:

    PV = FV / (1 + r/m)^(m*n)

Discounting divides; compounding multiplies. Confusing the direction - growing
a future amount instead of shrinking it - produces an answer on the wrong side
of the cash flow and is easy to spot because a present value must be smaller
than the future amount whenever the rate is positive. The same
periodic-rate/period-count discipline applies as for future value.

## Present value of an ordinary annuity

An ordinary annuity pays a level amount PMT at the END of each of N periods.
With periodic rate i, the present value one period before the first payment
is:

    PV = PMT * [ 1 - (1+i)^-N ] / i

Equivalently, the present value is the sum of PMT * (1+i)^-t for t from 1 to
N. The bracketed term is the annuity discount factor. When the rate is quoted
annually but payments are monthly or semiannual, first convert to the periodic
rate i = r/m and the period count N = m*n.

## Annuity due versus ordinary annuity

An annuity due pays at the BEGINNING of each period. Each payment arrives one
period earlier than in the ordinary annuity, so every present- and
future-value figure is larger by exactly one period of interest:

    PV(due) = PV(ordinary) * (1 + i)
    FV(due) = FV(ordinary) * (1 + i)

Treating a due stream as ordinary (or the reverse) misstates value by the
factor (1+i) - roughly the periodic rate in percentage terms. Rent and lease
payments are the standard annuity-due examples; bond coupons are ordinary.

## Future value of an ordinary annuity

Saving a level amount PMT at the end of each of N periods at periodic rate i
accumulates, at the date of the final payment, to:

    FV = PMT * [ (1+i)^N - 1 ] / i

Equivalently the sum of PMT * (1+i)^(N-t) for t from 1 to N. This is the
savings-plan formula: it answers how much a program of level deposits is worth
at the end. Asking instead what those deposits are worth today is a present
value question; mixing the two directions (valuing at time zero when the goal
date is time N) is a frequent setup error.

## Perpetuity

A perpetuity pays PMT at the end of every period forever. Its present value at
periodic rate i is:

    PV = PMT / i

The formula prices the stream one period before the first payment. Preferred
stock with a fixed dividend is the canonical perpetuity application.

## Rates: periodic, stated annual, and effective annual

The stated (nominal) annual rate r with m compounding periods per year implies
a periodic rate of r/m. The effective annual rate (EAR) restates the actual
one-year growth:

    EAR = (1 + r/m)^m - 1

EAR exceeds the stated rate whenever compounding is more frequent than annual.
Time-value calculations must pair the periodic rate with the number of
periods; pairing an annual rate with monthly periods (or the reverse) is the
rate-per-period confusion that produces answers that are too large or too
small by a compounding factor.

## Cash flow sign convention

Calculator and spreadsheet TVM functions treat cash outflows and inflows with
opposite signs: money you pay out (a deposit, the price of a bond) is entered
negative, and money you receive (a redemption, a withdrawal) comes back
positive. Solving for FV given a positive PV returns a negative number for
this reason. Dropping or flipping a sign does not change the magnitude but
reverses the direction of the cash flow, and on multi-flow problems (PV, PMT
and FV together) a wrong sign changes the magnitude of the solved quantity as
well.
