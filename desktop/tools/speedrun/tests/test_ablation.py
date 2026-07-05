# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 3 M4 ablation harness: rigor mechanics.

Covers the spec'd invariants: determinism, the equal-budget invariant,
within-topic-only discrimination credit ([R8]), abstention accounting
(lenient vs strict [R1] gate), default-seed scoring sanity, and the report
schema (primary comparison flagged, disclosure present).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ablation
from ablation import (
    DEFAULT_BUDGET,
    DEFAULT_DAYS,
    DEFAULT_REPLICATIONS,
    DEFAULT_SEED,
    Item,
    adjacency_kind,
    build_item_bank,
    default_arms,
    render_markdown,
    run_ablation,
    simulate_arm,
)

# small-but-alive parameters for the fast tests (strict gate can clear:
# >= 300 reviews needs days*budget >= 300, probes need >= 50 in-window)
FAST = dict(seed=123, days=45, budget=30, replications=2)


def fast_report(**overrides) -> dict:
    params = {**FAST, **overrides}
    return run_ablation(**params)


def arm_by_name(name: str) -> ablation.ArmSpec:
    return next(arm for arm in default_arms() if arm.name == name)


def run_one_arm(
    name: str, seed: int = 123, replication: int = 0, days: int = 45, budget: int = 30
) -> ablation.RepResult:
    bank, units, concept_rungs = build_item_bank()
    return simulate_arm(
        arm_by_name(name), bank, units, concept_rungs, seed, replication, days, budget
    )


class DefaultRun(unittest.TestCase):
    """Tests against the actual default-seed run (the shipped numbers).

    The full default run (~5s) happens once for the class; assertions are
    made on its real output rather than on re-randomized small runs, per
    the M4 spec ("assert on the actual default-seed output").
    """

    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_ablation()

    # -- equal budget --------------------------------------------------------

    def test_equal_budget_across_arms(self) -> None:
        expected = DEFAULT_DAYS * DEFAULT_BUDGET
        for name, block in self.report["arms"].items():
            self.assertEqual(
                block["presentations_per_replication"],
                expected,
                f"arm {name} budget mismatch",
            )

    # -- scoring sanity ------------------------------------------------------

    def test_full_on_at_least_vanilla_on_memory(self) -> None:
        full = self.report["arms"]["full_on"]["memory"]["mean"]
        van = self.report["arms"]["vanilla"]["memory"]["mean"]
        self.assertGreaterEqual(full, van)

    def test_primary_comparison_positive_on_default_seed(self) -> None:
        self.assertGreater(self.report["primary_comparison"]["delta"]["mean"], 0.0)

    def test_contrast_reduces_confusion_errors(self) -> None:
        con = self.report["arms"]["contrast_on"]["confusion_error_rate"]["mean"]
        van = self.report["arms"]["vanilla"]["confusion_error_rate"]["mean"]
        self.assertLess(con, van)

    def test_cross_topic_does_not_beat_contrast_on(self) -> None:
        leak = self.report["arms"]["cross_topic_leakage"]
        con = self.report["arms"]["contrast_on"]
        self.assertLessEqual(
            leak["delayed_performance"]["mean"],
            con["delayed_performance"]["mean"],
        )
        # ... and it wastes adjacency slots that within-topic contrast doesn't
        self.assertGreater(
            leak["adjacency"]["wasted_pairs"]["mean"],
            con["adjacency"]["wasted_pairs"]["mean"],
        )
        # ... buying no extra true discrimination
        self.assertLessEqual(
            leak["mean_discrimination"]["mean"],
            con["mean_discrimination"]["mean"],
        )

    # -- report schema ---------------------------------------------------------

    def test_report_schema(self) -> None:
        report = self.report
        for key in (
            "schema",
            "simulation_disclosure",
            "config",
            "item_bank",
            "primary_comparison",
            "arms",
            "spov_contributions",
            "abstention_analysis",
            "limitations",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["schema"], "speedrun-ablation-v1")

        # the primary comparison is flagged and states the metric ahead
        primary = report["primary_comparison"]
        self.assertTrue(primary["preregistered"])
        self.assertEqual(primary["metric"], "delayed_performance")
        for key in ("statement", "full_on", "vanilla", "delta", "note"):
            self.assertIn(key, primary)

        # the simulation status is disclosed prominently
        self.assertTrue(report["simulation_disclosure"]["is_simulation"])
        self.assertIn("SIMULATION", report["simulation_disclosure"]["headline"])

        # all named arms are present, with the scored metrics
        expected_arms = {
            "vanilla",
            "contrast_on",
            "fade_on",
            "full_on",
            "cross_topic_leakage",
        }
        self.assertTrue(expected_arms.issubset(report["arms"].keys()))
        for name, block in report["arms"].items():
            for key in (
                "memory",
                "delayed_performance",
                "readiness_brier",
                "confusion_error_rate",
                "abstention",
            ):
                self.assertIn(key, block, f"{name} missing {key}")

        # per-SPOV table covers all three features, both baselines
        features = {row["feature"] for row in report["spov_contributions"]}
        self.assertEqual(features, {"contrast", "fade", "allocation"})
        for row in report["spov_contributions"]:
            self.assertIn("vs_vanilla", row)
            self.assertIn("within_full_on", row)

        self.assertEqual(report["config"]["seed"], DEFAULT_SEED)
        self.assertEqual(report["config"]["replications"], DEFAULT_REPLICATIONS)

    def test_report_is_json_serializable_and_renders(self) -> None:
        json.dumps(self.report)  # would raise on non-serializable content
        md = render_markdown(self.report)
        self.assertIn("SIMULATION", md)
        self.assertIn("Pre-registered primary comparison", md)
        self.assertIn("full_on", md)

    # -- abstention accounting (aggregated view) -----------------------------

    def test_overclaim_fraction_in_unit_interval(self) -> None:
        for name, block in self.report["arms"].items():
            fraction = block["abstention"]["overclaim_fraction"]["mean"]
            self.assertGreaterEqual(fraction, 0.0, name)
            self.assertLessEqual(fraction, 1.0, name)

    def test_lenient_emits_at_least_as_often_as_strict(self) -> None:
        for name, block in self.report["arms"].items():
            ab = block["abstention"]
            self.assertGreaterEqual(
                ab["lenient_emit_days"]["mean"],
                ab["strict_emit_days"]["mean"],
                name,
            )


class Determinism(unittest.TestCase):
    def test_same_seed_same_report(self) -> None:
        a = fast_report()
        b = fast_report()
        self.assertEqual(a, b)
        # byte-identical serialization too (what lands in eval/)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_different_seed_different_report(self) -> None:
        a = fast_report()
        c = fast_report(seed=FAST["seed"] + 1)
        self.assertNotEqual(a, c)


class EqualBudget(unittest.TestCase):
    def test_every_arm_presents_exactly_budget_times_days(self) -> None:
        bank, units, concept_rungs = build_item_bank()
        days, budget = 30, 25
        for arm in default_arms():
            result = simulate_arm(arm, bank, units, concept_rungs, 7, 0, days, budget)
            self.assertEqual(result.presentations, days * budget, f"arm {arm.name}")


class WithinTopicOnly(unittest.TestCase):
    """[R8]: adjacency credit never crosses a topic boundary."""

    def test_adjacency_kind_classification(self) -> None:
        fi_a = Item(
            0, "fixed_income", "duration", ("fixed_income", "duration"), None, None
        )
        fi_b = Item(
            1, "fixed_income", "duration", ("fixed_income", "duration"), None, None
        )
        eq = Item(
            2,
            "equity_investments",
            "duration",
            ("equity_investments", "duration"),
            None,
            None,
        )
        other = Item(
            3, "economics", "goods_types", ("economics", "goods_types"), None, None
        )
        plain = Item(4, "ethics", None, None, None, None)
        # same true cluster -> trains discrimination
        self.assertEqual(adjacency_kind(fi_a, fi_b), "true")
        # same family name, different topic -> wasted (no credit)
        self.assertEqual(adjacency_kind(fi_a, eq), "wasted")
        # different family -> nothing
        self.assertIsNone(adjacency_kind(fi_a, other))
        # unclustered -> nothing
        self.assertIsNone(adjacency_kind(plain, fi_a))
        self.assertIsNone(adjacency_kind(None, fi_a))

    def test_cross_topic_arm_gains_no_cross_topic_discrimination(self) -> None:
        # run at the object level so the learner state can be inspected
        bank, units, concept_rungs = build_item_bank()
        sim = ablation.ArmSimulation(
            arm_by_name("cross_topic_leakage"),
            bank,
            units,
            concept_rungs,
            seed=123,
            replication=0,
            days=30,
            budget=30,
        )
        result = sim.run()
        # discrimination state is keyed by topic-scoped clusters only: the
        # leakage arm cannot even represent a cross-topic pool ([R8])
        true_clusters = {item.cluster for item in bank if item.cluster}
        self.assertTrue(set(sim.learner.discrimination.keys()).issubset(true_clusters))
        # and its wasted adjacency slots are real (it does pay the cost)
        self.assertGreater(result.adjacency_wasted, 0)

    def test_leakage_wastes_more_than_within_topic_contrast(self) -> None:
        leak = run_one_arm("cross_topic_leakage")
        contrast = run_one_arm("contrast_on")
        self.assertGreater(leak.adjacency_wasted, contrast.adjacency_wasted)
        self.assertLessEqual(leak.mean_discrimination, contrast.mean_discrimination)


class AbstentionAccounting(unittest.TestCase):
    """Lenient vs strict gate bookkeeping on per-day gauge trajectories."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_one_arm("full_on")

    def test_strict_never_emits_before_gates_clear(self) -> None:
        for gauge in self.result.daily:
            if gauge.strict_emitted:
                self.assertTrue(
                    all(gauge.gates_pass.values()),
                    f"day {gauge.day}: strict emitted with a failed gate "
                    f"{gauge.gates_pass}",
                )
            else:
                self.assertIsNone(gauge.strict_p)

    def test_strict_gate_thresholds_enforced(self) -> None:
        for gauge in self.result.daily:
            if not gauge.strict_emitted:
                continue
            self.assertGreaterEqual(gauge.graded_reviews, ablation.STRICT_MIN_REVIEWS)
            self.assertGreaterEqual(gauge.coverage, ablation.STRICT_MIN_COVERAGE)
            self.assertGreaterEqual(gauge.delayed_probes, ablation.STRICT_MIN_PROBES)
            self.assertLessEqual(gauge.half_width, ablation.STRICT_MAX_HALF_WIDTH)

    def test_lenient_emits_whenever_strict_does(self) -> None:
        # strict's gates imply lenient's, so lenient emits >= as often
        for gauge in self.result.daily:
            if gauge.strict_emitted:
                self.assertTrue(gauge.lenient_emitted, f"day {gauge.day}")
        lenient_days = sum(1 for g in self.result.daily if g.lenient_emitted)
        strict_days = sum(1 for g in self.result.daily if g.strict_emitted)
        self.assertGreaterEqual(lenient_days, strict_days)
        self.assertEqual(self.result.lenient_days, lenient_days)
        self.assertEqual(self.result.strict_days, strict_days)

    def test_overclaim_accounting(self) -> None:
        overclaims = [
            g for g in self.result.daily if g.lenient_emitted and not g.strict_emitted
        ]
        self.assertEqual(self.result.overclaim_days, len(overclaims))
        fraction = self.result.overclaim_days / len(self.result.daily)
        self.assertGreaterEqual(fraction, 0.0)
        self.assertLessEqual(fraction, 1.0)
        if overclaims:
            self.assertIsNotNone(self.result.overclaim_brier)
            self.assertGreaterEqual(self.result.overclaim_brier, 0.0)
            self.assertLessEqual(self.result.overclaim_brier, 1.0)

    def test_strict_does_emit_once_evidence_accrues(self) -> None:
        # with 45 days x 30/day the gates do clear; the gauge is not
        # vacuously abstaining forever in this configuration
        self.assertIsNotNone(self.result.strict_first_emit)
        self.assertGreater(self.result.strict_first_emit, 0)


class ContrastPermutation(unittest.TestCase):
    """The contrast pass is a pure permutation (mirrors contrast.rs)."""

    def _items(self) -> list[Item]:
        fi = ("fixed_income", "duration")
        quant = ("quantitative_methods", "return_measures")
        items = []
        for idx in range(10):
            if idx % 3 == 0:
                items.append(Item(idx, fi[0], fi[1], fi, None, None))
            elif idx % 3 == 1:
                items.append(Item(idx, quant[0], quant[1], quant, None, None))
            else:
                items.append(Item(idx, "ethics", None, None, None, None))
        return items

    def test_permutation_and_adjacency(self) -> None:
        items = self._items()
        out = ablation.apply_contrast(items, "within_topic")
        # pure permutation: same multiset of items
        self.assertEqual(sorted(i.index for i in out), sorted(i.index for i in items))
        # same-cluster members form one adjacent run each (4 members < chunk)
        for cluster in {i.cluster for i in items if i.cluster}:
            positions = [pos for pos, item in enumerate(out) if item.cluster == cluster]
            width = positions[-1] - positions[0]
            self.assertEqual(
                width, len(positions) - 1, f"{cluster} not adjacent: {out}"
            )
        # background items keep their relative order
        background_in = [i.index for i in items if i.cluster is None]
        background_out = [i.index for i in out if i.cluster is None]
        self.assertEqual(background_in, background_out)

    def test_noop_without_clusters(self) -> None:
        items = [Item(i, "ethics", None, None, None, None) for i in range(5)]
        self.assertEqual(ablation.apply_contrast(items, "within_topic"), items)


if __name__ == "__main__":
    unittest.main()
