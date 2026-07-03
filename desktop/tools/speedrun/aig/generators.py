# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Parameterized numeric item generators (M1, the guaranteed-runnable path).

Each generator draws random-but-seeded inputs, computes the answer TWICE via
independently written implementations (closed form vs iterative/identity - the
two must agree within 1e-6 relative before an item may be emitted), and builds
error-pattern distractors solve-first by simulating the documented student
errors of the CFA confusable set [R22]. Every distractor records its
misconception id in the item's ``misconceptions`` map.

Emitted dicts conform to desktop/tools/speedrun/ITEM_SCHEMA.md plus a private
``_aig`` key (stripped before writing JSONL) carrying the draw parameters,
both computed answers, numeric choice values, the declared grounding passage
(the synthetic qrel for the retrieval eval) and the retrieval query text.

Determinism: one ``random.Random`` per (seed, generator, kind) stream; the
same seed always reproduces byte-identical items.

Learner-facing math (worked_steps, rationales, cloze_text) is written in
Anki-native MathJax markup. cloze_text uses LINEAR TeX only, so that no "}}"
sequence appears outside the cloze markers themselves (Anki closes each
{{cN::...}} at the first following "}}"; see ladder_schema). Prompts, stems,
titles, choices and the _aig retrieval query text stay plain prose.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_SEED = 20260703

SCHEMA_VERSION = "speedrun-item-v1"

# Relative agreement required between the two independent implementations.
NUMERIC_TOLERANCE = 1e-6
# Distractors must differ from the correct answer and from each other by more
# than this relative margin (checked on display-rounded values).
DISTRACTOR_MARGIN = 0.005

# ---------------------------------------------------------------------------
# Misconception registry (ids recorded in items' `misconceptions` maps; the
# ids double as SPOV3 confusable-edge labels).
# ---------------------------------------------------------------------------

MISCONCEPTIONS: dict[str, str] = {
    "duration.modified_vs_macaulay": (
        "Swaps Macaulay and modified duration - quotes the weighted-average "
        "time figure where the price-sensitivity figure is required (or the "
        r"reverse), missing the \((1 + y/k)\) conversion."
    ),
    "duration.compounding_confusion": (
        r"Divides by \((1 + y)\) on a semiannual-pay bond instead of "
        r"\((1 + y/2)\): "
        "annual-vs-semiannual compounding confusion in the ModDur divisor."
    ),
    "duration.factor_inversion": (
        r"Multiplies by \((1 + y/k)\) instead of dividing when converting "
        "Macaulay to modified duration."
    ),
    "duration.sign_error": (
        r"Drops the minus sign in \(\%\Delta P = -\text{ModDur} \times "
        r"\Delta y\), reporting a price GAIN "
        "when yields rise (or a loss when they fall)."
    ),
    "duration.bp_conversion": (
        "Misconverts basis points to decimal (treats 50bp as 0.05 instead "
        "of 0.005), inflating the estimated move by 10x."
    ),
    "duration.no_discounting": (
        "Weights the cash-flow times by nominal (undiscounted) cash flows "
        "when computing Macaulay duration, skipping present values."
    ),
    "duration.zero_coupon_confusion": (
        "Treats a coupon bond like a zero: reports time-to-maturity as its "
        "Macaulay duration, ignoring that coupons shorten duration."
    ),
    "tvm.rate_per_period": (
        "Uses the stated annual rate with yearly periods, ignoring "
        "intra-year compounding (rate-per-period vs annual confusion)."
    ),
    "tvm.n_off_by_one": (
        "Miscounts the number of compounding periods by one (N-1 periods "
        "of growth instead of N)."
    ),
    "tvm.simple_vs_compound": (
        r"Applies simple interest \(\text{PV}(1 + r \times n)\), "
        "ignoring interest-on-interest."
    ),
    "tvm.sign_convention": (
        "Sets the cash-flow direction backward (PV/FV sign convention): "
        "discounts where the problem compounds, landing on the wrong side "
        "of the cash flow."
    ),
    "tvm.due_vs_ordinary": (
        "Values an ordinary annuity as an annuity due (or the reverse), "
        r"misstating value by one period's interest factor \((1 + i)\)."
    ),
    "tvm.pv_vs_fv": (
        "Values the stream at the wrong date - answers the present-value "
        "question with the future value (or the reverse)."
    ),
    "inventory.fifo_lifo_swap": (
        "Costs the units sold from the wrong end of the layer stack - LIFO "
        "layers when FIFO is asked, or FIFO layers when LIFO is asked."
    ),
    "inventory.wac_confusion": (
        "Applies weighted-average costing where a specific cost-flow "
        "method (FIFO/LIFO) is required."
    ),
    "inventory.ei_vs_cogs": (
        "Reports ending inventory where cost of goods sold is asked (the "
        "other half of the inventory identity)."
    ),
    "inventory.lifo_reserve_direction": (
        "Applies the LIFO reserve in the wrong direction - subtracts it "
        "from LIFO inventory (or adds the reserve change to LIFO COGS) "
        "when restating to FIFO."
    ),
    "inventory.reserve_period_mixup": (
        "Uses the beginning-of-period LIFO reserve where the end-of-period "
        "reserve is required for the balance-sheet restatement."
    ),
    "inventory.reserve_level_vs_change": (
        "Adjusts COGS by the LEVEL of the LIFO reserve instead of its "
        "CHANGE over the period."
    ),
}


@dataclass(frozen=True)
class Distractor:
    value: float
    misconception: str
    why: str


def fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def fmt_money_tex(v: float) -> str:
    """Money for use INSIDE a MathJax span: escaped dollar sign, thousands
    separators brace-grouped so TeX does not add punctuation spacing."""
    return "\\$" + f"{v:,.2f}".replace(",", "{,}")


def fmt_years(v: float) -> str:
    return f"{v:.2f} years"


def fmt_signed_pct(v: float) -> str:
    return f"{v:+.2f}%"


def _rel_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1.0)


def margins_ok(values: list[float], margin: float = DISTRACTOR_MARGIN) -> bool:
    """True when all values pairwise differ by more than `margin` (relative)."""
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            if abs(a - b) <= margin * max(abs(a), abs(b), 1e-9):
                return False
    return True


def _bisect(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Plain bisection; used as an independent root-finding recomputation."""
    flo = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if fm == 0.0:
            return mid
        if (flo < 0) == (fm < 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Generator base
# ---------------------------------------------------------------------------


class ParamGen:
    """A parameterized generator for one concept.

    Subclasses provide two INDEPENDENTLY WRITTEN answer implementations
    (`solve` / `solve_independent`); `validate_draw` enforces their agreement
    within NUMERIC_TOLERANCE, plus distractor margins, before any item is
    assembled.
    """

    name: str = ""
    version: str = "v1"
    cluster: str = ""
    topic: str = ""
    interactivity: str = "high"
    decimals: int = 2
    passage: str = ""  # declared grounding passage id (doc.md#slug)

    @property
    def gen_id(self) -> str:
        return f"param:{self.name}_{self.version}"

    # -- numeric core (subclass) -------------------------------------------
    def draw(self, rng: random.Random) -> dict[str, Any]:
        raise NotImplementedError

    def solve(self, p: dict[str, Any]) -> float:
        raise NotImplementedError

    def solve_independent(self, p: dict[str, Any]) -> float:
        raise NotImplementedError

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        raise NotImplementedError

    def fmt(self, v: float) -> str:
        return f"{v:.{self.decimals}f}"

    # -- rendering (subclass) ----------------------------------------------
    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        raise NotImplementedError

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        raise NotImplementedError

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        raise NotImplementedError

    # -- validation ---------------------------------------------------------
    def validate_draw(self, p: dict[str, Any]) -> tuple[bool, str]:
        """Dual-implementation agreement + distractor distinctness."""
        a = self.solve(p)
        b = self.solve_independent(p)
        if not math.isfinite(a) or not math.isfinite(b):
            return False, "non-finite answer"
        if _rel_diff(a, b) > NUMERIC_TOLERANCE:
            return False, (
                f"independent recomputation disagrees: {a!r} vs {b!r} "
                f"(rel diff {_rel_diff(a, b):.3g} > {NUMERIC_TOLERANCE})"
            )
        rounded = [round(a, self.decimals)]
        for d in self.distractors(p, a):
            if not math.isfinite(d.value):
                return False, f"non-finite distractor ({d.misconception})"
            rounded.append(round(d.value, self.decimals))
        if not margins_ok(rounded):
            return False, "distractor margin violation (<0.5% relative)"
        return True, ""


# ---------------------------------------------------------------------------
# fi::duration generators
# ---------------------------------------------------------------------------


class ModDurationFromMac(ParamGen):
    """Modified duration from a stated Macaulay duration (semiannual-pay)."""

    name = "mod_duration_from_mac"
    cluster = "fi::duration"
    topic = "fixed_income"
    decimals = 2
    passage = "duration.md#modified-duration"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        # Sample a realistic bond, derive its Macaulay duration, then state
        # that (rounded) figure in the stem; the item is self-contained.
        years = rng.randrange(4, 16)
        coupon = rng.randrange(4, 17) * 0.005  # 2%..8% annual
        y = rng.randrange(8, 37) * 0.0025  # 2%..9% annual, semiannual comp
        k = 2
        mac = _macaulay_years(coupon, y, years, k)
        return {"mac": round(mac, 2), "y": round(y, 4), "k": k, "years": years}

    def solve(self, p: dict[str, Any]) -> float:
        return p["mac"] / (1.0 + p["y"] / p["k"])

    def solve_independent(self, p: dict[str, Any]) -> float:
        # Root-find x such that x * (1 + y/k) = mac (no division).
        target = p["mac"]
        factor = 1.0 + p["y"] / p["k"]
        return _bisect(lambda x: x * factor - target, 0.0, target + 1.0)

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        mac, y = p["mac"], p["y"]
        return [
            Distractor(
                mac,
                "duration.modified_vs_macaulay",
                "quotes the Macaulay duration unchanged, skipping the "
                r"division by \((1 + y/2)\)",
            ),
            Distractor(
                mac / (1.0 + y),
                "duration.compounding_confusion",
                r"divides by \((1 + y)\) using the full annual yield "
                r"instead of the semiannual \((1 + y/2)\)",
            ),
            Distractor(
                mac * (1.0 + y / 2.0),
                "duration.factor_inversion",
                r"multiplies by \((1 + y/2)\) instead of dividing",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_years(v)

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        half = p["y"] / 2
        prompt = (
            f"A {p['years']}-year semiannual-pay bond has a Macaulay duration "
            f"of {p['mac']:.2f} years and is priced at a quoted annual yield "
            f"of {y_pct:.2f}%. Compute the bond's modified duration."
        )
        steps = [
            "Modified duration rescales Macaulay duration by one period's "
            r"yield: \[\text{ModDur} = \dfrac{\text{MacDur}}{1 + y/k}\]",
            rf"The bond pays semiannually, so \(k = 2\) and the periodic "
            rf"yield is \(y/k = {y_pct:.2f}\% / 2 = {half * 100:.3f}\% "
            rf"= {half:.5f}\).",
            rf"Divide: \(\text{{ModDur}} = \dfrac{{{p['mac']:.2f}}}"
            rf"{{1 + {half:.5f}}} = \dfrac{{{p['mac']:.2f}}}"
            rf"{{{1 + half:.5f}}} = {answer:.4f}\).",
            f"Rounded: modified duration is {answer:.2f} years, slightly "
            f"below the Macaulay figure as it must be at a positive yield.",
        ]
        rationale = (
            rf"\(\text{{ModDur}} = \dfrac{{\text{{MacDur}}}}{{1 + y/k}} = "
            rf"\dfrac{{{p['mac']:.2f}}}{{1 + {half:.5f}}} = {answer:.2f}\) "
            "years. The divisor uses the "
            "PERIODIC yield (annual quote / 2 for semiannual pay); dividing "
            "by the full annual yield, skipping the division, or multiplying "
            "instead are the classic traps."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        half = p["y"] / 2
        prompt = (
            f"A semiannual-pay bond has a Macaulay duration of "
            f"{p['mac']:.2f} years at a quoted annual yield of {y_pct:.2f}%. "
            "Complete the conversion to modified duration."
        )
        cloze = (
            r"\(\text{ModDur} = \text{MacDur} / (1 + y/k)\) = "
            f"{p['mac']:.2f} / (1 + {{{{c1::{half:.5f}}}}}) = "
            f"{{{{c2::{answer:.2f}}}}} years"
        )
        rationale = (
            rf"\(k = 2\) for semiannual pay, so the divisor is "
            rf"\(1 + {y_pct:.2f}\%/2 = {1 + half:.5f}\); the result "
            rf"{answer:.2f} sits just below the Macaulay duration."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        stem = (
            f"An analyst computes a Macaulay duration of {p['mac']:.2f} years "
            f"for a {p['years']}-year semiannual-pay bond quoted at an annual "
            f"yield of {y_pct:.2f}%. The bond's modified duration is closest "
            "to:"
        )
        rationale = (
            rf"\(\text{{ModDur}} = \dfrac{{\text{{MacDur}}}}{{1 + y/k}} = "
            rf"\dfrac{{{p['mac']:.2f}}}{{1 + {p['y'] / 2:.5f}}} = "
            rf"{answer:.2f}\) years. Semiannual pay "
            "means the divisor uses half the quoted annual yield."
        )
        return {"stem": stem, "rationale": rationale}


class DurationPriceChange(ParamGen):
    """First-order percentage price change from modified duration."""

    name = "duration_price_change"
    cluster = "fi::duration"
    topic = "fixed_income"
    decimals = 2
    passage = "duration.md#approximate-price-change-using-duration"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        d_mod = rng.randrange(20, 121) / 10.0  # 2.0 .. 12.0
        y = rng.randrange(8, 33) * 0.0025  # 2% .. 8%
        d_mac = d_mod * (1.0 + y / 2.0)
        bp = rng.choice([-150, -100, -75, -50, -25, 25, 50, 75, 100, 150])
        price = rng.randrange(880, 1121) / 10.0  # 88.0 .. 112.0 (per 100 par)
        return {
            "d_mod": round(d_mod, 2),
            "d_mac": round(d_mac, 2),
            "bp": bp,
            "price": round(price, 1),
            "y": round(y, 4),
        }

    def solve(self, p: dict[str, Any]) -> float:
        dy = p["bp"] / 10000.0
        return -p["d_mod"] * dy * 100.0

    def solve_independent(self, p: dict[str, Any]) -> float:
        # Money-duration route: dollar change on the full price, then back
        # to percent - a separately written path exercising the same claim.
        dy = p["bp"] * 1e-4
        money_dur = p["d_mod"] * p["price"]
        dollar_change = -money_dur * dy
        return (dollar_change / p["price"]) * 100.0

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        dy = p["bp"] / 10000.0
        return [
            Distractor(
                -answer,
                "duration.sign_error",
                "drops the minus sign in the duration approximation, "
                "reversing the direction of the price move",
            ),
            Distractor(
                -p["d_mac"] * dy * 100.0,
                "duration.modified_vs_macaulay",
                "uses the Macaulay duration in the price-change formula "
                "where modified duration belongs",
            ),
            Distractor(
                answer * 10.0,
                "duration.bp_conversion",
                "converts basis points to decimal with a slipped decimal "
                "point (e.g. 50bp as 0.05), inflating the move tenfold",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_signed_pct(v)

    def _dir(self, p: dict[str, Any]) -> str:
        return "rise" if p["bp"] > 0 else "fall"

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        prompt = (
            f"A bond position with a full price of {p['price']:.1f} (per 100 "
            f"par) has a modified duration of {p['d_mod']:.2f} and a Macaulay "
            f"duration of {p['d_mac']:.2f}. Yields {self._dir(p)} by "
            f"{abs(p['bp'])} basis points. Estimate the percentage change in "
            "price using duration alone."
        )
        dy = p["bp"] / 10000.0
        steps = [
            "The first-order estimate uses MODIFIED duration: "
            r"\[\%\Delta P = -\text{ModDur} \times \Delta y\]",
            rf"Convert the move to decimal: \({p['bp']:+d}\,\text{{bp}} "
            rf"= {dy:+.4f}\).",
            rf"Multiply: \(\%\Delta P = -({p['d_mod']:.2f}) \times "
            rf"({dy:+.4f}) = {answer / 100:+.4f} = {answer:+.2f}\%\).",
            "The sign is negative for a yield rise and positive for a fall; "
            "the Macaulay figure stated in the problem is a decoy.",
        ]
        rationale = (
            rf"\(\%\Delta P = -\text{{ModDur}} \times \Delta y = "
            rf"-({p['d_mod']:.2f}) \times ({dy:+.4f}) = {answer:+.2f}\%\). "
            "Keep the minus sign, use the MODIFIED (not "
            r"Macaulay) duration, and convert basis points as "
            r"\(1\,\text{bp} = 0.0001\)."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        dy = p["bp"] / 10000.0
        prompt = (
            f"A bond has modified duration {p['d_mod']:.2f}. Yields "
            f"{self._dir(p)} by {abs(p['bp'])}bp. Complete the duration "
            "estimate of the percentage price change."
        )
        cloze = (
            r"\(\%\Delta P = -\text{ModDur} \times \Delta y\) = "
            f"-({p['d_mod']:.2f}) × ({{{{c1::{dy:+.4f}}}}}) = "
            f"{{{{c2::{answer:+.2f}%}}}}"
        )
        rationale = (
            rf"\({p['bp']:+d}\,\text{{bp}} = {dy:+.4f}\) in decimal; "
            rf"\(\%\Delta P = -{p['d_mod']:.2f} \times {dy:+.4f} "
            rf"= {answer:+.2f}\%\). Price moves opposite to yield."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        stem = (
            f"A portfolio holds a bond with a modified duration of "
            f"{p['d_mod']:.2f} and a Macaulay duration of {p['d_mac']:.2f}. "
            f"If its yield to maturity {self._dir(p)}s by {abs(p['bp'])} "
            "basis points, the duration-only estimate of the percentage "
            "price change is closest to:"
        )
        dy = p["bp"] / 10000.0
        rationale = (
            rf"\(\%\Delta P = -\text{{ModDur}} \times \Delta y = "
            rf"-({p['d_mod']:.2f}) \times ({dy:+.4f}) = {answer:+.2f}\%\). "
            "Modified (not Macaulay) duration drives the "
            "estimate, and the sign is opposite to the yield move."
        )
        return {"stem": stem, "rationale": rationale}


def _macaulay_years(coupon_annual: float, y_annual: float, years: int, k: int) -> float:
    """Loop-sum Macaulay duration in years (weighted-average time)."""
    i = y_annual / k
    c = coupon_annual / k  # per-period coupon per 1 par
    n = years * k
    pv_total = 0.0
    weighted = 0.0
    for t in range(1, n + 1):
        cf = c + (1.0 if t == n else 0.0)
        pv = cf / (1.0 + i) ** t
        pv_total += pv
        weighted += t * pv
    return (weighted / pv_total) / k


def _macaulay_closed_form(
    coupon_annual: float, y_annual: float, years: int, k: int
) -> float:
    """Closed-form Macaulay duration in years (independent formula)."""
    i = y_annual / k
    c = coupon_annual / k
    n = years * k
    periods = (1 + i) / i - (1 + i + n * (c - i)) / (c * ((1 + i) ** n - 1) + i)
    return periods / k


class MacaulayFromCashflows(ParamGen):
    """Macaulay duration of a short annual-pay bond from its cash flows."""

    name = "macaulay_from_cashflows"
    cluster = "fi::duration"
    topic = "fixed_income"
    decimals = 3
    passage = "duration.md#macaulay-duration"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        years = rng.choice([2, 3])
        coupon = rng.randrange(6, 17) * 0.005  # 3%..8%
        y = rng.randrange(12, 37) * 0.0025  # 3%..9%
        return {"years": years, "coupon": round(coupon, 4), "y": round(y, 4)}

    def solve(self, p: dict[str, Any]) -> float:
        return _macaulay_years(p["coupon"], p["y"], p["years"], 1)

    def solve_independent(self, p: dict[str, Any]) -> float:
        return _macaulay_closed_form(p["coupon"], p["y"], p["years"], 1)

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        y = p["y"]
        n = p["years"]
        c = p["coupon"]
        # Undiscounted weighted-average time.
        cfs = [(t, c + (1.0 if t == n else 0.0)) for t in range(1, n + 1)]
        no_disc = sum(t * cf for t, cf in cfs) / sum(cf for _, cf in cfs)
        return [
            Distractor(
                answer / (1.0 + y),
                "duration.modified_vs_macaulay",
                r"reports the MODIFIED duration (already divided by "
                r"\(1+y\)) where the Macaulay figure is asked",
            ),
            Distractor(
                no_disc,
                "duration.no_discounting",
                "weights the times by raw cash flows without discounting "
                "them to present value",
            ),
            Distractor(
                float(n),
                "duration.zero_coupon_confusion",
                "reports the maturity itself, as if the bond were a zero-coupon bond",
            ),
        ]

    def fmt(self, v: float) -> str:
        return f"{v:.3f} years"

    def _cf_text(self, p: dict[str, Any]) -> str:
        c = p["coupon"] * 100
        parts = [f"{c:.2f} at t={t}" for t in range(1, p["years"])]
        parts.append(f"{100 + c:.2f} at t={p['years']}")
        return ", ".join(parts)

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        prompt = (
            f"An annual-pay bond (par 100) has {p['years']} years to "
            f"maturity, a {p['coupon'] * 100:.2f}% coupon, and yields "
            f"{y_pct:.2f}%. Its cash flows per 100 par are {self._cf_text(p)}."
            " Compute its Macaulay duration."
        )
        i = p["y"]
        rows = []
        pv_total = 0.0
        weighted = 0.0
        n = p["years"]
        c = p["coupon"] * 100
        for t in range(1, n + 1):
            cf = c + (100.0 if t == n else 0.0)
            pv = cf / (1 + i) ** t
            pv_total += pv
            weighted += t * pv
            rows.append(
                rf"\(t={t}\): CF {cf:.2f}, PV {pv:.4f}, "
                rf"\(t \times \text{{PV}}\) {t * pv:.4f}"
            )
        steps = [
            "Discount each cash flow at the yield and weight its time by "
            r"the PV share: \[\text{MacDur} = "
            r"\dfrac{\sum_t t \times \text{PV}_t}{\sum_t \text{PV}_t}\]",
            *rows,
            rf"Totals: \(\sum_t \text{{PV}}_t = {pv_total:.4f}\) (the "
            rf"price), \(\sum_t t \times \text{{PV}}_t = {weighted:.4f}\).",
            rf"\(\text{{MacDur}} = \dfrac{{{weighted:.4f}}}"
            rf"{{{pv_total:.4f}}} = {answer:.3f}\) years.",
        ]
        rationale = (
            rf"\(\text{{MacDur}} = \dfrac{{\sum_t t \times \text{{PV}}_t}}"
            rf"{{\sum_t \text{{PV}}_t}} = {answer:.3f}\) years - a "
            "weighted-average TIME, pulled below maturity by the coupons; "
            "skipping the discounting or reporting maturity itself are the "
            "standard errors."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        i = p["y"]
        n = p["years"]
        c = p["coupon"] * 100
        pv_total = sum(
            (c + (100.0 if t == n else 0.0)) / (1 + i) ** t for t in range(1, n + 1)
        )
        prompt = (
            f"A {n}-year annual-pay bond (par 100, {p['coupon'] * 100:.2f}% "
            f"coupon, {y_pct:.2f}% yield) is being run through the Macaulay "
            "duration calculation. Complete it."
        )
        cloze = (
            r"\(\text{MacDur} = \sum(t \times \text{PV}_t) / "
            r"\sum \text{PV}_t\), with \(\sum \text{PV}_t\) = the "
            f"bond's price = {{{{c1::{pv_total:.4f}}}}}, giving MacDur = "
            f"{{{{c2::{answer:.3f}}}}} years (below the {n}-year maturity)."
        )
        rationale = (
            f"The PV-weighted average time is {answer:.3f} years; the "
            "denominator of the weights is the full price "
            f"({pv_total:.4f} per 100 par)."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        y_pct = p["y"] * 100
        stem = (
            f"A {p['years']}-year annual-pay bond (par 100) carries a "
            f"{p['coupon'] * 100:.2f}% coupon and is priced at a "
            f"{y_pct:.2f}% yield to maturity. Its Macaulay duration is "
            "closest to:"
        )
        rationale = (
            "Discount each cash flow at the yield, weight its time by its "
            rf"PV share, and average: \(\text{{MacDur}} = {answer:.3f}\) "
            "years. Coupons "
            "pull duration below maturity; the undiscounted average and the "
            "maturity itself are decoys."
        )
        return {"stem": stem, "rationale": rationale}


# ---------------------------------------------------------------------------
# qm::tvm generators
# ---------------------------------------------------------------------------


class TvmFvLump(ParamGen):
    """Future value of a single sum with intra-year compounding."""

    name = "tvm_fv_lump"
    cluster = "qm::tvm"
    topic = "quantitative_methods"
    decimals = 2
    passage = "tvm.md#future-value-of-a-single-sum"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        pv = rng.randrange(2, 41) * 500  # 1,000 .. 20,000
        r = rng.randrange(20, 41) * 0.0025  # 5% .. 10%
        m = rng.choice([2, 4, 12])
        n = rng.randrange(2, 11)
        return {"pv": float(pv), "r": round(r, 4), "m": m, "n": n}

    def solve(self, p: dict[str, Any]) -> float:
        return p["pv"] * (1.0 + p["r"] / p["m"]) ** (p["m"] * p["n"])

    def solve_independent(self, p: dict[str, Any]) -> float:
        # Iterative compounding, one period at a time.
        bal = p["pv"]
        for _ in range(p["m"] * p["n"]):
            bal += bal * (p["r"] / p["m"])
        return bal

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        pv, r, m, n = p["pv"], p["r"], p["m"], p["n"]
        return [
            Distractor(
                pv * (1.0 + r) ** n,
                "tvm.rate_per_period",
                "compounds the stated annual rate once a year, ignoring the "
                f"{_freq_name(m)} compounding",
            ),
            Distractor(
                pv * (1.0 + r / m) ** (m * n - 1),
                "tvm.n_off_by_one",
                f"compounds for {m * n - 1} periods instead of {m * n} "
                "(period count off by one)",
            ),
            Distractor(
                pv * (1.0 + r * n),
                "tvm.simple_vs_compound",
                "applies simple interest, ignoring interest-on-interest",
            ),
            Distractor(
                pv / (1.0 + r / m) ** (m * n),
                "tvm.sign_convention",
                "sets the direction backward and DISCOUNTS the deposit "
                "instead of compounding it forward",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_money(v)

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        per = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"An investor deposits {fmt_money(p['pv'])} today in an account "
            f"paying a stated annual rate of {p['r'] * 100:.2f}%, compounded "
            f"{_freq_name(p['m'])}. What is the account balance after "
            f"{p['n']} years?"
        )
        steps = [
            rf"Periodic rate: \(r/m = {p['r'] * 100:.2f}\% / {p['m']} = "
            rf"{per * 100:.4f}\% = {per:.6f}\).",
            rf"Number of periods: \(m \times n = {p['m']} \times {p['n']} "
            rf"= {n_per}\).",
            rf"\[\text{{FV}} = \text{{PV}} \times (1 + r/m)^{{m \times n}} "
            rf"= {fmt_money_tex(p['pv'])} \times (1 + {per:.6f})"
            rf"^{{{n_per}}} = {fmt_money_tex(answer)}\]",
            "Both the rate and the period count must be per-period; using "
            "the annual rate with annual periods understates the balance.",
        ]
        rationale = (
            rf"\(\text{{FV}} = {fmt_money_tex(p['pv'])} \times "
            rf"(1 + {per:.6f})^{{{n_per}}} = {fmt_money_tex(answer)}\). "
            rf"Pair the PERIODIC rate \(r/m\) with \(m \times n\) "
            "periods; simple interest and off-by-one period counts are the "
            "standard slips."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        per = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"{fmt_money(p['pv'])} is invested at {p['r'] * 100:.2f}% "
            f"compounded {_freq_name(p['m'])} for {p['n']} years. Complete "
            "the future-value setup."
        )
        cloze = (
            r"\(\text{FV} = \text{PV} \times (1 + r/m)^{m \times n}\) = "
            f"{fmt_money(p['pv'])} × "
            f"(1 + {{{{c1::{per:.6f}}}}})^{{{{c2::{n_per}}}}} = "
            f"{{{{c3::{fmt_money(answer)}}}}}"
        )
        rationale = (
            f"Periodic rate {per:.6f} with {n_per} periods gives "
            f"{fmt_money(answer)}; rate and period count must share the "
            "same per-period basis."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        stem = (
            f"{fmt_money(p['pv'])} is deposited today at a stated annual "
            f"rate of {p['r'] * 100:.2f}% compounded {_freq_name(p['m'])}. "
            f"The value of the deposit after {p['n']} years is closest to:"
        )
        per = p["r"] / p["m"]
        rationale = (
            rf"\(\text{{FV}} = \text{{PV}} \times (1 + r/m)^{{m \times n}} "
            rf"= {fmt_money_tex(p['pv'])} \times (1 + {per:.6f})"
            rf"^{{{p['m'] * p['n']}}} = {fmt_money_tex(answer)}\). The "
            "periodic rate is the annual rate divided by the compounding "
            r"frequency, applied for \(m \times n\) periods."
        )
        return {"stem": stem, "rationale": rationale}


def _freq_name(m: int) -> str:
    return {1: "annually", 2: "semiannually", 4: "quarterly", 12: "monthly"}[m]


def _freq_period(m: int) -> str:
    return {1: "year", 2: "half-year", 4: "quarter", 12: "month"}[m]


class TvmAnnuityPv(ParamGen):
    """Present value of an ordinary annuity."""

    name = "tvm_annuity_pv"
    cluster = "qm::tvm"
    topic = "quantitative_methods"
    decimals = 2
    passage = "tvm.md#present-value-of-an-ordinary-annuity"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        pmt = rng.randrange(4, 41) * 50  # 200 .. 2,000
        r = rng.randrange(12, 37) * 0.0025  # 3% .. 9%
        m = rng.choice([1, 2, 4])
        n = rng.randrange(3, 16)
        return {"pmt": float(pmt), "r": round(r, 4), "m": m, "n": n}

    def solve(self, p: dict[str, Any]) -> float:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        return p["pmt"] * (1.0 - (1.0 + i) ** -n_per) / i

    def solve_independent(self, p: dict[str, Any]) -> float:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        return math.fsum(p["pmt"] / (1.0 + i) ** t for t in range(1, n_per + 1))

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        fv = p["pmt"] * ((1.0 + i) ** n_per - 1.0) / i
        return [
            Distractor(
                answer * (1.0 + i),
                "tvm.due_vs_ordinary",
                "values the payments as an annuity DUE (payments at the "
                "start of each period) when they arrive at period end",
            ),
            Distractor(
                p["pmt"] * (1.0 - (1.0 + i) ** -(n_per - 1)) / i,
                "tvm.n_off_by_one",
                f"discounts only {n_per - 1} payments instead of {n_per}",
            ),
            Distractor(
                fv,
                "tvm.pv_vs_fv",
                "accumulates the payments to the FINAL date (a future "
                "value) when the question asks for value TODAY",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_money(v)

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"A contract pays {fmt_money(p['pmt'])} at the END of each "
            f"{_freq_period(p['m'])} for {p['n']} years. The discount rate "
            f"is {p['r'] * 100:.2f}% (stated annual, compounded "
            f"{_freq_name(p['m'])}). What is the contract worth today?"
        )
        steps = [
            rf"Periodic rate \(i = {p['r'] * 100:.2f}\%/{p['m']} = "
            rf"{i:.6f}\); periods \(N = {p['m']} \times {p['n']} = "
            rf"{n_per}\).",
            "Ordinary annuity (end-of-period payments): "
            r"\[\text{PV} = \text{PMT} \times \dfrac{1 - (1+i)^{-N}}{i}\]",
            rf"\(\text{{PV}} = {fmt_money_tex(p['pmt'])} \times "
            rf"\dfrac{{1 - (1 + {i:.6f})^{{-{n_per}}}}}{{{i:.6f}}} = "
            rf"{fmt_money_tex(answer)}\).",
            r"No \((1+i)\) timing bump applies - that factor belongs to an "
            "annuity due, not an ordinary annuity.",
        ]
        rationale = (
            rf"\(\text{{PV(ordinary)}} = \text{{PMT}} \times "
            rf"\dfrac{{1 - (1+i)^{{-N}}}}{{i}} = {fmt_money_tex(answer)}\) "
            rf"with \(i = {i:.6f}\), \(N = {n_per}\). End-of-period timing "
            r"means NO extra \((1+i)\) factor; miscounting N or "
            "accumulating to the final "
            "date instead are the other classic errors."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"{fmt_money(p['pmt'])} arrives at the end of each "
            f"{_freq_period(p['m'])} for {p['n']} years; the rate is "
            f"{p['r'] * 100:.2f}% compounded {_freq_name(p['m'])}. Complete "
            "the present-value setup."
        )
        cloze = (
            r"\(\text{PV} = \text{PMT} \times [1 - (1+i)^{-N}] / i\) "
            "with i = "
            f"{{{{c1::{i:.6f}}}}} and N = {{{{c2::{n_per}}}}}, giving PV = "
            f"{{{{c3::{fmt_money(answer)}}}}}"
        )
        rationale = (
            rf"\(i = r/m = {i:.6f}\) and \(N = m \times n = {n_per}\); "
            f"the ordinary-"
            f"annuity factor prices the stream at {fmt_money(answer)} one "
            "period before the first payment."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        stem = (
            f"An investment promises {fmt_money(p['pmt'])} at the end of "
            f"each {_freq_period(p['m'])} for {p['n']} years. At a discount "
            f"rate of {p['r'] * 100:.2f}% (stated annual, compounded "
            f"{_freq_name(p['m'])}), the value of the investment today is "
            "closest to:"
        )
        i = p["r"] / p["m"]
        rationale = (
            rf"\(\text{{PV}} = \text{{PMT}} \times "
            rf"\dfrac{{1 - (1+i)^{{-N}}}}{{i}}\) with \(i = {i:.6f}\) and "
            rf"\(N = {p['m'] * p['n']}\): {fmt_money(answer)}. "
            "End-of-period "
            r"payments make this an ORDINARY annuity - no \((1+i)\) bump."
        )
        return {"stem": stem, "rationale": rationale}


class TvmAnnuityFvSavings(ParamGen):
    """Future value of an ordinary annuity (savings plan)."""

    name = "tvm_annuity_fv"
    cluster = "qm::tvm"
    topic = "quantitative_methods"
    decimals = 2
    passage = "tvm.md#future-value-of-an-ordinary-annuity"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        pmt = rng.randrange(2, 21) * 100  # 200 .. 2,000
        r = rng.randrange(12, 33) * 0.0025  # 3% .. 8%
        m = rng.choice([4, 12])
        n = rng.randrange(2, 9)
        return {"pmt": float(pmt), "r": round(r, 4), "m": m, "n": n}

    def solve(self, p: dict[str, Any]) -> float:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        return p["pmt"] * ((1.0 + i) ** n_per - 1.0) / i

    def solve_independent(self, p: dict[str, Any]) -> float:
        i = p["r"] / p["m"]
        bal = 0.0
        for _ in range(p["m"] * p["n"]):
            bal = bal * (1.0 + i) + p["pmt"]
        return bal

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        pv = p["pmt"] * (1.0 - (1.0 + i) ** -n_per) / i
        return [
            Distractor(
                answer * (1.0 + i),
                "tvm.due_vs_ordinary",
                "accumulates the deposits as an annuity DUE (start-of-"
                "period deposits) when they occur at period end",
            ),
            Distractor(
                p["pmt"] * ((1.0 + i) ** (n_per - 1) - 1.0) / i,
                "tvm.n_off_by_one",
                f"accumulates only {n_per - 1} deposits instead of {n_per}",
            ),
            Distractor(
                pv,
                "tvm.pv_vs_fv",
                "discounts the deposits to TODAY (a present value) when "
                "the question asks for the balance at the goal date",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_money(v)

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"A saver deposits {fmt_money(p['pmt'])} at the END of each "
            f"{_freq_period(p['m'])} into an account earning "
            f"{p['r'] * 100:.2f}% (stated annual, compounded "
            f"{_freq_name(p['m'])}). What is the balance immediately after "
            f"the final deposit, {p['n']} years from now?"
        )
        steps = [
            rf"Periodic rate \(i = {p['r'] * 100:.2f}\%/{p['m']} = "
            rf"{i:.6f}\); deposits \(N = {p['m']} \times {p['n']} = "
            rf"{n_per}\).",
            "Ordinary-annuity accumulation: "
            r"\[\text{FV} = \text{PMT} \times \dfrac{(1+i)^N - 1}{i}\] "
            "valued at the date of the LAST deposit.",
            rf"\(\text{{FV}} = {fmt_money_tex(p['pmt'])} \times "
            rf"\dfrac{{(1 + {i:.6f})^{{{n_per}}} - 1}}{{{i:.6f}}} = "
            rf"{fmt_money_tex(answer)}\).",
            "The final deposit earns no interest (it just arrived); an "
            "annuity-due setup would wrongly credit every deposit one "
            "extra period.",
        ]
        rationale = (
            rf"\(\text{{FV(ordinary)}} = \text{{PMT}} \times "
            rf"\dfrac{{(1+i)^N - 1}}{{i}} = {fmt_money_tex(answer)}\) "
            rf"with \(i = {i:.6f}\), \(N = {n_per}\). Valuing at the goal "
            "date (not "
            "today) and end-of-period timing are the two things to hold "
            "straight."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        i = p["r"] / p["m"]
        n_per = p["m"] * p["n"]
        prompt = (
            f"{fmt_money(p['pmt'])} is saved at the end of each "
            f"{_freq_period(p['m'])} for {p['n']} years at "
            f"{p['r'] * 100:.2f}% compounded {_freq_name(p['m'])}. Complete "
            "the accumulation."
        )
        cloze = (
            r"\(\text{FV} = \text{PMT} \times [(1+i)^N - 1] / i\) = "
            f"{fmt_money(p['pmt'])} x [(1 + {{{{c1::{i:.6f}}}}})^"
            f"{{{{c2::{n_per}}}}} - 1] / {i:.6f} = "
            f"{{{{c3::{fmt_money(answer)}}}}}"
        )
        rationale = (
            rf"\(i = {i:.6f}\), \(N = {n_per}\): the deposits grow to "
            f"{fmt_money(answer)} at the date of the last deposit."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        stem = (
            f"To fund a goal, {fmt_money(p['pmt'])} is deposited at the end "
            f"of each {_freq_period(p['m'])} for {p['n']} years at a stated "
            f"annual rate of {p['r'] * 100:.2f}% compounded "
            f"{_freq_name(p['m'])}. The accumulated balance at the final "
            "deposit is closest to:"
        )
        i = p["r"] / p["m"]
        rationale = (
            rf"\(\text{{FV}} = \text{{PMT}} \times "
            rf"\dfrac{{(1+i)^N - 1}}{{i}}\) with \(i = {i:.6f}\), "
            rf"\(N = {p['m'] * p['n']}\): {fmt_money(answer)}. "
            "The stream is "
            "ordinary (end-of-period) and is valued at the goal date."
        )
        return {"stem": stem, "rationale": rationale}


# ---------------------------------------------------------------------------
# fsa::inventory generators
# ---------------------------------------------------------------------------


def _walk_layers(layers: list[tuple[int, float]], units: int) -> float:
    """Cost the first `units` units walking the given layer order."""
    cost = 0.0
    remaining = units
    for qty, unit_cost in layers:
        take = min(qty, remaining)
        cost += take * unit_cost
        remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise ValueError("not enough units in layers")
    return cost


class InventoryCogs(ParamGen):
    """COGS under FIFO or LIFO from layered purchases (rising prices)."""

    name = "inventory_cogs"
    cluster = "fsa::inventory"
    topic = "financial_statement_analysis"
    decimals = 2
    passage = "inventory.md#fifo-cost-flow"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        u0 = rng.randrange(2, 7) * 100
        u1 = rng.randrange(2, 7) * 100
        u2 = rng.randrange(2, 7) * 100
        c0 = rng.randrange(16, 41) * 0.5  # 8.00 .. 20.00
        c1 = c0 + rng.randrange(2, 7) * 0.5  # strictly rising prices
        c2 = c1 + rng.randrange(2, 7) * 0.5
        # Sales cross into at least the second layer but leave stock.
        lo = u0 + 100
        hi = u0 + u1 + u2 - 100
        sold = rng.randrange(lo // 100, hi // 100 + 1) * 100
        method = rng.choice(["FIFO", "LIFO"])
        return {
            "layers": [[u0, c0], [u1, c1], [u2, c2]],
            "sold": sold,
            "method": method,
        }

    def _front(self, p: dict[str, Any]) -> list[tuple[int, float]]:
        return [(int(u), float(c)) for u, c in p["layers"]]

    def _back(self, p: dict[str, Any]) -> list[tuple[int, float]]:
        return list(reversed(self._front(p)))

    def solve(self, p: dict[str, Any]) -> float:
        order = self._front(p) if p["method"] == "FIFO" else self._back(p)
        return _walk_layers(order, p["sold"])

    def solve_independent(self, p: dict[str, Any]) -> float:
        # Inventory identity: COGS = goods available - ending inventory,
        # with EI costed by walking the layer stack from the OPPOSITE end.
        layers = self._front(p)
        avail_units = sum(u for u, _ in layers)
        avail_cost = sum(u * c for u, c in layers)
        ei_units = avail_units - p["sold"]
        ei_order = self._back(p) if p["method"] == "FIFO" else layers
        ei_cost = _walk_layers(ei_order, ei_units)
        return avail_cost - ei_cost

    def _wac_cogs(self, p: dict[str, Any]) -> float:
        layers = self._front(p)
        avail_units = sum(u for u, _ in layers)
        avail_cost = sum(u * c for u, c in layers)
        return p["sold"] * (avail_cost / avail_units)

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        other = "LIFO" if p["method"] == "FIFO" else "FIFO"
        swapped = dict(p, method=other)
        layers = self._front(p)
        avail_cost = sum(u * c for u, c in layers)
        return [
            Distractor(
                self.solve(swapped),
                "inventory.fifo_lifo_swap",
                f"costs the units sold from the {other} end of the layer "
                f"stack although {p['method']} is required",
            ),
            Distractor(
                self._wac_cogs(p),
                "inventory.wac_confusion",
                "spreads the average cost of goods available over the "
                "units sold (weighted-average costing) instead of "
                f"{p['method']} layers",
            ),
            Distractor(
                avail_cost - answer,
                "inventory.ei_vs_cogs",
                f"reports the {p['method']} ENDING INVENTORY - the other "
                "half of the inventory identity - instead of COGS",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_money(v)

    def _layers_text(self, p: dict[str, Any]) -> str:
        (u0, c0), (u1, c1), (u2, c2) = self._front(p)
        return (
            f"beginning inventory {u0} units at {fmt_money(c0)}; purchases "
            f"of {u1} units at {fmt_money(c1)} and then {u2} units at "
            f"{fmt_money(c2)}"
        )

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        m = p["method"]
        prompt = (
            f"A retailer using {m} has {self._layers_text(p)}. During the "
            f"period it sells {p['sold']} units. Compute cost of goods sold."
        )
        order = self._front(p) if m == "FIFO" else self._back(p)
        walk = []
        remaining = p["sold"]
        for qty, cost in order:
            take = min(qty, remaining)
            if take:
                walk.append(
                    rf"take \({take} \times {fmt_money_tex(cost)} = "
                    rf"{fmt_money_tex(take * cost)}\)"
                )
            remaining -= take
            if remaining == 0:
                break
        end_note = "oldest layers first" if m == "FIFO" else "newest layers first"
        steps = [
            f"{m} costs sales from the {end_note}.",
            *walk,
            rf"\(\text{{COGS}} = {fmt_money_tex(answer)}\).",
            "Cross-check via the identity: goods available - ending "
            "inventory (costed from the opposite end) reproduces the same "
            "figure.",
        ]
        rationale = (
            rf"{m} takes the {end_note}: \(\text{{COGS}} = "
            rf"{fmt_money_tex(answer)}\). "
            "Costing from the wrong end, averaging the layers, or reporting "
            "ending inventory instead are the classic errors."
        )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        m = p["method"]
        layers = self._front(p)
        avail_cost = sum(u * c for u, c in layers)
        ei = avail_cost - answer
        prompt = (
            f"A firm using {m} has {self._layers_text(p)} and sells "
            f"{p['sold']} units. Complete the costing."
        )
        end_note = "OLDEST" if m == "FIFO" else "NEWEST"
        cloze = (
            f"{m} assigns the {{{{c1::{end_note.lower()}}}}} costs to the "
            f"units sold, giving COGS = {{{{c2::{fmt_money(answer)}}}}} and "
            f"ending inventory = {{{{c3::{fmt_money(ei)}}}}} (identity: "
            f"available {fmt_money(avail_cost)} = COGS + EI)."
        )
        rationale = (
            rf"\(\text{{COGS}} = {fmt_money_tex(answer)}\); the rest of the "
            f"{fmt_money(avail_cost)} of goods available "
            f"({fmt_money(ei)}) stays on the balance sheet."
        )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        stem = (
            f"A company accounts for inventory under {p['method']}. It has "
            f"{self._layers_text(p)}, and sells {p['sold']} units during the "
            "period. Cost of goods sold for the period is closest to:"
        )
        end_note = (
            "oldest layers first" if p["method"] == "FIFO" else "newest layers first"
        )
        rationale = (
            rf"{p['method']} costs sales from the {end_note}, giving "
            rf"\(\text{{COGS}} = {fmt_money_tex(answer)}\). The other "
            "method's figure, the weighted-average figure, and the "
            "ending-inventory figure are the standard traps."
        )
        return {"stem": stem, "rationale": rationale}


class LifoReserve(ParamGen):
    """LIFO->FIFO restatement via the LIFO reserve (level and change)."""

    name = "lifo_reserve"
    cluster = "fsa::inventory"
    topic = "financial_statement_analysis"
    decimals = 2
    passage = "inventory.md#lifo-reserve"

    def draw(self, rng: random.Random) -> dict[str, Any]:
        bi_l = rng.randrange(40, 121) * 1000  # LIFO beginning inventory
        purchases = rng.randrange(100, 301) * 1000
        cogs_l = purchases + rng.randrange(-30, 21) * 500  # LIFO COGS
        r_begin = rng.randrange(10, 41) * 500
        delta = rng.choice([-1, 1, 1]) * rng.randrange(4, 25) * 500
        r_end = r_begin + delta
        variant = rng.choice(["ei", "cogs"])
        if r_end <= 0:
            r_end = r_begin + abs(delta)
        return {
            "bi_l": float(bi_l),
            "purchases": float(purchases),
            "cogs_l": float(cogs_l),
            "r_begin": float(r_begin),
            "r_end": float(r_end),
            "variant": variant,
        }

    def _ei_l(self, p: dict[str, Any]) -> float:
        return p["bi_l"] + p["purchases"] - p["cogs_l"]

    def solve(self, p: dict[str, Any]) -> float:
        if p["variant"] == "ei":
            return self._ei_l(p) + p["r_end"]
        return p["cogs_l"] - (p["r_end"] - p["r_begin"])

    def solve_independent(self, p: dict[str, Any]) -> float:
        # Rebuild the FIFO books from the identity: restate BOTH inventory
        # endpoints with the reserve, then apply BI + purchases - EI.
        bi_f = p["bi_l"] + p["r_begin"]
        ei_f = self._ei_l(p) + p["r_end"]
        cogs_f = bi_f + p["purchases"] - ei_f
        if p["variant"] == "ei":
            return bi_f + p["purchases"] - cogs_f
        return cogs_f

    def distractors(self, p: dict[str, Any], answer: float) -> list[Distractor]:
        ei_l = self._ei_l(p)
        if p["variant"] == "ei":
            return [
                Distractor(
                    ei_l - p["r_end"],
                    "inventory.lifo_reserve_direction",
                    "SUBTRACTS the LIFO reserve from LIFO inventory; the "
                    "reserve must be ADDED to reach the FIFO figure",
                ),
                Distractor(
                    ei_l + p["r_begin"],
                    "inventory.reserve_period_mixup",
                    "adds the BEGINNING reserve; the balance-sheet "
                    "restatement needs the END-of-period reserve",
                ),
            ]
        d_r = p["r_end"] - p["r_begin"]
        return [
            Distractor(
                p["cogs_l"] + d_r,
                "inventory.lifo_reserve_direction",
                "ADDS the reserve increase to LIFO COGS; a rising reserve "
                "means FIFO COGS is LOWER, so the change is subtracted",
            ),
            Distractor(
                p["cogs_l"] - p["r_end"],
                "inventory.reserve_level_vs_change",
                "subtracts the reserve LEVEL; the income-statement "
                "restatement uses the CHANGE in the reserve",
            ),
        ]

    def fmt(self, v: float) -> str:
        return fmt_money(v)

    def _facts(self, p: dict[str, Any]) -> str:
        return (
            f"beginning LIFO inventory {fmt_money(p['bi_l'])}, purchases "
            f"{fmt_money(p['purchases'])}, LIFO cost of goods sold "
            f"{fmt_money(p['cogs_l'])}, LIFO reserve {fmt_money(p['r_begin'])} "
            f"at the start of the year and {fmt_money(p['r_end'])} at year-end"
        )

    def render_worked(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        ei_l = self._ei_l(p)
        if p["variant"] == "ei":
            ask = "restate year-end inventory to a FIFO basis"
        else:
            ask = "restate cost of goods sold to a FIFO basis"
        prompt = (
            f"A US firm reports under LIFO: {self._facts(p)}. For peer "
            f"comparison, {ask}."
        )
        d_r = p["r_end"] - p["r_begin"]
        if p["variant"] == "ei":
            steps = [
                r"Ending LIFO inventory from the identity: \(\text{EI} = "
                rf"\text{{BI}} + \text{{purchases}} - \text{{COGS}} = "
                rf"{fmt_money_tex(p['bi_l'])} + {fmt_money_tex(p['purchases'])} "
                rf"- {fmt_money_tex(p['cogs_l'])} = {fmt_money_tex(ei_l)}\).",
                r"\[\text{FIFO inventory} = \text{LIFO inventory} + "
                r"\text{LIFO reserve}\] (the reserve is ADDED, and it is "
                "the END-of-period reserve).",
                rf"\(\text{{FIFO EI}} = {fmt_money_tex(ei_l)} + "
                rf"{fmt_money_tex(p['r_end'])} = {fmt_money_tex(answer)}\).",
            ]
            rationale = (
                r"\(\text{FIFO inventory} = \text{LIFO inventory} + "
                rf"\text{{LIFO reserve}} = {fmt_money_tex(ei_l)} + "
                rf"{fmt_money_tex(p['r_end'])} = {fmt_money_tex(answer)}\). "
                "Add (never subtract) the end-of-period reserve."
            )
        else:
            steps = [
                rf"Change in LIFO reserve: \({fmt_money_tex(p['r_end'])} - "
                rf"{fmt_money_tex(p['r_begin'])} = {fmt_money_tex(d_r)}\).",
                r"\[\text{FIFO COGS} = \text{LIFO COGS} - "
                r"\Delta\text{LIFO reserve}\] - a rising reserve means "
                "FIFO expensed LESS.",
                rf"\(\text{{FIFO COGS}} = {fmt_money_tex(p['cogs_l'])} - "
                rf"{fmt_money_tex(d_r)} = {fmt_money_tex(answer)}\).",
            ]
            rationale = (
                r"\(\text{FIFO COGS} = \text{LIFO COGS} - "
                rf"\Delta\text{{reserve}} = {fmt_money_tex(p['cogs_l'])} - "
                rf"{fmt_money_tex(d_r)} = {fmt_money_tex(answer)}\). Use the "
                "CHANGE in the reserve with a minus sign for an increase - "
                "not the level, not a plus."
            )
        return {"prompt": prompt, "worked_steps": steps, "rationale": rationale}

    def render_cloze(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        ei_l = self._ei_l(p)
        d_r = p["r_end"] - p["r_begin"]
        prompt = (
            f"A LIFO reporter shows {self._facts(p)}. Complete the FIFO restatement."
        )
        if p["variant"] == "ei":
            cloze = (
                f"FIFO inventory = LIFO inventory {{{{c1::+}}}} LIFO reserve "
                f"= {fmt_money(ei_l)} + {fmt_money(p['r_end'])} = "
                f"{{{{c2::{fmt_money(answer)}}}}} (use the end-of-period "
                "reserve)."
            )
            rationale = (
                "The reserve is ADDED to LIFO inventory; using the "
                rf"end-of-period reserve gives \({fmt_money_tex(answer)}\)."
            )
        else:
            cloze = (
                f"FIFO COGS = LIFO COGS {{{{c1::-}}}} (change in LIFO "
                f"reserve) = {fmt_money(p['cogs_l'])} - {fmt_money(d_r)} = "
                f"{{{{c2::{fmt_money(answer)}}}}}"
            )
            rationale = (
                "The income-statement restatement subtracts the CHANGE in "
                rf"the reserve, giving \({fmt_money_tex(answer)}\)."
            )
        return {"prompt": prompt, "cloze_text": cloze, "rationale": rationale}

    def render_mcq(self, p: dict[str, Any], answer: float) -> dict[str, Any]:
        if p["variant"] == "ei":
            ask = "its year-end inventory restated to a FIFO basis is closest to:"
        else:
            ask = "its cost of goods sold restated to a FIFO basis is closest to:"
        stem = f"A company reporting under LIFO discloses {self._facts(p)}. {ask}"
        if p["variant"] == "ei":
            rationale = (
                r"\(\text{FIFO inventory} = \text{LIFO inventory} + "
                rf"\text{{END-of-period reserve}} = "
                rf"{fmt_money_tex(self._ei_l(p))} + "
                rf"{fmt_money_tex(p['r_end'])} = {fmt_money_tex(answer)}\)."
            )
        else:
            d_r = p["r_end"] - p["r_begin"]
            rationale = (
                r"\(\text{FIFO COGS} = \text{LIFO COGS} - "
                rf"\Delta\text{{LIFO reserve}} = "
                rf"{fmt_money_tex(p['cogs_l'])} - {fmt_money_tex(d_r)} = "
                rf"{fmt_money_tex(answer)}\)."
            )
        return {"stem": stem, "rationale": rationale}


# ---------------------------------------------------------------------------
# Compare items ([R20] side-by-side for the tightest confusables)
# ---------------------------------------------------------------------------


def compare_items() -> list[dict[str, Any]]:
    """Deterministic side-by-side compare items (duration trio, FIFO/LIFO)."""
    items = [
        {
            "schema": SCHEMA_VERSION,
            "kind": "compare",
            "rung": "compare",
            "topic": "fixed_income",
            "cluster": "fi::duration",
            "interactivity": "low",
            "title": "Compare: Macaulay vs modified duration",
            "left_title": "Macaulay duration",
            "left_body": (
                "Weighted-average TIME to the bond's cash flows, weights = "
                "PV share of each flow. Measured in years. Equals maturity "
                "for a zero-coupon bond."
            ),
            "right_title": "Modified duration",
            "right_body": (
                "Price SENSITIVITY: the approximate percentage price fall "
                r"for a 1-unit yield rise. Equals \(\text{Macaulay} / "
                r"(1 + y/k)\). "
                "Always below Macaulay at positive yields."
            ),
            "discriminator": (
                "One of these is a time in years, the other a rate "
                "sensitivity. Which is which - and what single factor "
                "converts the first into the second?"
            ),
            "rationale": (
                "Macaulay is the PV-weighted average time; dividing it by "
                r"\((1 + y/k)\) - one period's yield - rescales it into "
                "modified "
                "duration, the price-sensitivity measure used in "
                r"\(\%\Delta P = -\text{ModDur} \times \Delta y\). "
                "Quoting one for the other is the "
                "classic duration-family error."
            ),
            "source": {},
            "provenance": {
                "generator": "param:compare_duration_trio_v1",
                "gates": [],
                "graded": False,
            },
            "_aig": {
                "generator": "param:compare_duration_trio_v1",
                "declared_passage": "duration.md#modified-duration",
                "query": (
                    "Macaulay duration weighted average time versus modified "
                    "duration price sensitivity divide by one plus yield"
                ),
            },
        },
        {
            "schema": SCHEMA_VERSION,
            "kind": "compare",
            "rung": "compare",
            "topic": "fixed_income",
            "cluster": "fi::duration",
            "interactivity": "low",
            "title": "Compare: modified vs effective duration",
            "left_title": "Modified duration",
            "left_body": (
                "Yield-based: differentiates the price-yield function of "
                "the bond's OWN fixed cash flows. Correct for option-free "
                "bonds."
            ),
            "right_title": "Effective duration",
            "right_body": (
                "Curve-based: reprices the bond after bumping the benchmark "
                "curve up and down; captures cash flows that CHANGE with "
                "rates (callable, putable, MBS)."
            ),
            "discriminator": (
                "A callable bond trades near its call price. Which duration "
                "measure remains valid, and why does the other break down?"
            ),
            "rationale": (
                "Effective duration remains valid: it reprices the bond "
                "under curve shifts, letting the call change the cash "
                "flows. Modified duration assumes FIXED cash flows, so it "
                "overstates the price upside a call would cap. For "
                "option-free bonds the two are close."
            ),
            "source": {},
            "provenance": {
                "generator": "param:compare_duration_trio_v1",
                "gates": [],
                "graded": False,
            },
            "_aig": {
                "generator": "param:compare_duration_trio_v1",
                "declared_passage": "duration.md#effective-duration",
                "query": (
                    "effective duration callable bond cash flows change "
                    "with rates versus modified duration fixed cash flows"
                ),
            },
        },
        {
            "schema": SCHEMA_VERSION,
            "kind": "compare",
            "rung": "compare",
            "topic": "financial_statement_analysis",
            "cluster": "fsa::inventory",
            "interactivity": "low",
            "title": "Compare: FIFO vs LIFO under rising prices",
            "left_title": "FIFO",
            "left_body": (
                "Oldest costs -> COGS; newest costs stay in ending "
                "inventory. Balance sheet nearest replacement cost. Rising "
                "prices: LOWER COGS, higher income, higher inventory."
            ),
            "right_title": "LIFO",
            "right_body": (
                "Newest costs -> COGS; oldest costs stay in ending "
                "inventory. Income statement matches current costs. Rising "
                "prices: HIGHER COGS, lower income and taxes, lower "
                "inventory. US GAAP only."
            ),
            "discriminator": (
                "Purchase prices are rising. Which method reports the "
                "higher gross margin, and which balance sheet better "
                "approximates replacement cost - and why are those two "
                "different methods?"
            ),
            "rationale": (
                "FIFO wins on both counts, for opposite reasons: it sends "
                "the OLD cheap costs to COGS (higher margin) and keeps the "
                "NEW costs on the balance sheet (near replacement cost). "
                "LIFO does the reverse on each statement. The two methods "
                "split the same goods-available cost differently - swap "
                "them and every comparison flips."
            ),
            "source": {},
            "provenance": {
                "generator": "param:compare_fifo_lifo_v1",
                "gates": [],
                "graded": False,
            },
            "_aig": {
                "generator": "param:compare_fifo_lifo_v1",
                "declared_passage": "inventory.md#lifo-cost-flow",
                "query": (
                    "FIFO versus LIFO rising prices cost of goods sold "
                    "gross margin ending inventory replacement cost"
                ),
            },
        },
    ]
    return items


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

GENERATORS: list[ParamGen] = [
    ModDurationFromMac(),
    DurationPriceChange(),
    MacaulayFromCashflows(),
    TvmFvLump(),
    TvmAnnuityPv(),
    TvmAnnuityFvSavings(),
    InventoryCogs(),
    LifoReserve(),
]

# Independent solvers keyed by generator id, used by the solve-check gate to
# recompute MCQ answers without touching the primary implementation.
INDEPENDENT_SOLVERS: dict[str, Callable[[dict[str, Any]], float]] = {
    g.gen_id: g.solve_independent for g in GENERATORS
}
GENERATOR_DECIMALS: dict[str, int] = {g.gen_id: g.decimals for g in GENERATORS}

DEFAULT_COUNTS = {"worked": 2, "cloze": 2, "mcq": 3}

_KIND_TITLE = {"worked": "worked", "cloze": "faded", "mcq": "MCQ"}
_KIND_RUNG = {"worked": "worked", "cloze": "faded", "mcq": "solve"}

_TITLE_HUMAN = {
    "mod_duration_from_mac": "Modified duration from Macaulay",
    "duration_price_change": "Duration price-change estimate",
    "macaulay_from_cashflows": "Macaulay duration from cash flows",
    "tvm_fv_lump": "FV of a single sum",
    "tvm_annuity_pv": "PV of an ordinary annuity",
    "tvm_annuity_fv": "FV of a savings annuity",
    "inventory_cogs": "FIFO/LIFO cost of goods sold",
    "lifo_reserve": "LIFO reserve restatement",
}


def _draw_valid(
    gen: ParamGen, rng: random.Random, max_attempts: int = 300
) -> dict[str, Any]:
    """Redraw until the dual-implementation and margin checks pass."""
    last_reason = ""
    for _ in range(max_attempts):
        params = gen.draw(rng)
        ok, reason = gen.validate_draw(params)
        if ok:
            return params
        last_reason = reason
    raise RuntimeError(
        f"{gen.gen_id}: no valid draw in {max_attempts} attempts: {last_reason}"
    )


def _base_item(gen: ParamGen, kind: str, idx: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "rung": _KIND_RUNG[kind],
        "topic": gen.topic,
        "cluster": gen.cluster,
        "interactivity": gen.interactivity,
        "title": f"{_TITLE_HUMAN[gen.name]} - {_KIND_TITLE[kind]} {idx + 1}",
        "source": {},
        "provenance": {"generator": gen.gen_id, "gates": [], "graded": False},
    }


def build_item(
    gen: ParamGen, kind: str, idx: int, rng: random.Random
) -> dict[str, Any]:
    """Draw parameters and assemble one schema-conforming item dict."""
    params = _draw_valid(gen, rng)
    answer = gen.solve(params)
    item = _base_item(gen, kind, idx)
    aig: dict[str, Any] = {
        "generator": gen.gen_id,
        "params": params,
        "answer": answer,
        "answer_check": gen.solve_independent(params),
        "decimals": gen.decimals,
        "declared_passage": gen.passage,
    }

    if kind == "worked":
        rendered = gen.render_worked(params, answer)
        item.update(rendered)
        aig["query"] = rendered["prompt"]
    elif kind == "cloze":
        rendered = gen.render_cloze(params, answer)
        item.update(rendered)
        aig["query"] = rendered["prompt"]
    elif kind == "mcq":
        rendered = gen.render_mcq(params, answer)
        pool = gen.distractors(params, answer)
        chosen = rng.sample(pool, 2)
        options: list[tuple[float, Distractor | None]] = [(answer, None)] + [
            (d.value, d) for d in chosen
        ]
        rng.shuffle(options)
        letters = ["A", "B", "C"]
        choices: dict[str, str] = {}
        distractor_rationales: dict[str, str] = {}
        misconceptions: dict[str, str] = {}
        correct_letter = ""
        choice_values: dict[str, float] = {}
        for letter, (value, dis) in zip(letters, options):
            choices[letter] = gen.fmt(round(value, gen.decimals))
            choice_values[letter] = round(value, gen.decimals)
            if dis is None:
                correct_letter = letter
            else:
                distractor_rationales[letter] = (
                    f"Incorrect - this {dis.why}. ({MISCONCEPTIONS[dis.misconception]})"
                )
                misconceptions[letter] = dis.misconception
        item["stem"] = rendered["stem"]
        item["choices"] = choices
        item["correct"] = correct_letter
        item["distractor_rationales"] = distractor_rationales
        item["misconceptions"] = misconceptions
        item["rationale"] = rendered["rationale"]
        aig["query"] = rendered["stem"]
        aig["choice_values"] = choice_values
        aig["correct_value"] = round(answer, gen.decimals)
    else:
        raise ValueError(f"unknown kind {kind}")

    item["_aig"] = aig
    return item


def generate_all(
    seed: int = DEFAULT_SEED, counts: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """All parameterized items plus the compare set, deterministically."""
    counts = counts or DEFAULT_COUNTS
    items: list[dict[str, Any]] = []
    for gen in GENERATORS:
        for kind, n in counts.items():
            rng = random.Random(f"{seed}:{gen.name}:{kind}")
            for idx in range(n):
                items.append(build_item(gen, kind, idx, rng))
    items.extend(compare_items())
    return items


def strip_private(item: dict[str, Any]) -> dict[str, Any]:
    """Drop private underscore keys before writing JSONL."""
    return {k: v for k, v in item.items() if not k.startswith("_")}
