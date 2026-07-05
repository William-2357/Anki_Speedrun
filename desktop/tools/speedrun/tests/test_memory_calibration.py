# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for memory_calibration: the engine-pinned forgetting curve,
Brier/log-loss/binning/ECE math, bootstrap determinism, the holdout
selection rule, the sqlite truncation + leakage guard, the SVG chart
structure, and report writing. stdlib only (no pylib needed); run with:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memory_calibration as mc  # noqa: E402
from memory_calibration import (  # noqa: E402
    MS_PER_DAY,
    CalibrationError,
    HoldoutObs,
    RevlogRow,
    adaptive_cutoff,
    bin_index,
    bootstrap_brier_ci,
    brier_score,
    build_truncated_copy,
    calibration_bins,
    choose_cutoff,
    evaluate_tier,
    expected_calibration_error,
    is_eligible,
    leakage_check,
    log_loss,
    metrics_block,
    reliability_svg,
    retrievability,
    select_holdout,
    strip_fsrs_card_state,
    write_reports,
)


class RetrievabilityFormulaTest(unittest.TestCase):
    """Pins the exact curve verified in fsrs-5.2.0 src/inference.rs:60-63;
    expected values below are the crate's own unit-test fixtures
    (src/inference.rs:1373-1407), so upstream drift breaks this test."""

    def test_zero_elapsed_is_certain_recall(self):
        for stability in (0.1, 1.0, 250.0):
            self.assertEqual(
                retrievability(0.0, stability, mc.FSRS6_DEFAULT_DECAY), 1.0
            )

    def test_matches_crate_unit_test_fixtures(self):
        # fsrs-5.2.0 src/inference.rs:1373-1384 (f32, hence 1e-6 tolerance)
        self.assertAlmostEqual(retrievability(1.0, 1.0, 0.2), 0.9, places=9)
        self.assertAlmostEqual(retrievability(2.0, 1.0, 0.2), 0.84028935, delta=1e-6)
        self.assertAlmostEqual(retrievability(3.0, 1.0, 0.2), 0.7985001, delta=1e-6)
        # seconds arithmetic (src/inference.rs:1387-1407): seconds / 86400
        self.assertAlmostEqual(
            retrievability(3600 / 86400, 1.0, 0.2), 0.9943189, delta=1e-6
        )

    def test_elapsed_equals_stability_gives_090_for_any_decay(self):
        for decay in (0.1, mc.FSRS6_DEFAULT_DECAY, 0.5, 1.0, 2.0):
            for stability in (0.5, 3.0, 365.0):
                self.assertAlmostEqual(
                    retrievability(stability, stability, decay), 0.9, places=12
                )

    def test_monotone_decreasing_in_elapsed(self):
        previous = 1.0
        for elapsed in (0.5, 1.0, 5.0, 30.0, 400.0):
            value = retrievability(elapsed, 10.0, mc.FSRS6_DEFAULT_DECAY)
            self.assertLess(value, previous)
            previous = value

    def test_negative_elapsed_clamped_to_zero(self):
        self.assertEqual(retrievability(-1.0, 5.0, 0.2), 1.0)

    def test_rejects_nonpositive_stability_or_decay(self):
        with self.assertRaises(ValueError):
            retrievability(1.0, 0.0, 0.2)
        with self.assertRaises(ValueError):
            retrievability(1.0, 1.0, 0.0)

    def test_engine_constants_pinned(self):
        # fsrs-5.2.0 src/inference.rs:25 and :27-49
        self.assertEqual(mc.FSRS6_DEFAULT_DECAY, 0.1542)
        self.assertEqual(len(mc.FSRS6_DEFAULT_PARAMETERS), 21)
        self.assertEqual(mc.FSRS6_DEFAULT_PARAMETERS[20], 0.1542)
        self.assertEqual(mc.FSRS6_DEFAULT_PARAMETERS[0], 0.212)


class MetricsTest(unittest.TestCase):
    def test_brier_hand_computed(self):
        self.assertAlmostEqual(
            brier_score([(0.8, True), (0.4, False)]), 0.10, places=12
        )
        self.assertAlmostEqual(brier_score([(1.0, True)]), 0.0, places=12)
        self.assertAlmostEqual(brier_score([(1.0, False)]), 1.0, places=12)

    def test_log_loss_hand_computed(self):
        expected = -(math.log(0.8) + math.log(0.6)) / 2.0
        self.assertAlmostEqual(
            log_loss([(0.8, True), (0.4, False)]), expected, places=12
        )

    def test_log_loss_clamps_at_epsilon(self):
        # p=0 with a recall would be infinite; the disclosed clamp bounds it
        value = log_loss([(0.0, True)])
        self.assertAlmostEqual(value, -math.log(mc.LOG_LOSS_EPSILON), places=9)
        self.assertTrue(math.isfinite(log_loss([(1.0, False)])))

    def test_empty_samples_raise(self):
        for fn in (brier_score, log_loss):
            with self.assertRaises(ValueError):
                fn([])
        with self.assertRaises(ValueError):
            bootstrap_brier_ci([])
        with self.assertRaises(ValueError):
            expected_calibration_error(calibration_bins([]))

    def test_bin_index_edges(self):
        self.assertEqual(bin_index(0.0), 0)
        self.assertEqual(bin_index(0.0999), 0)
        self.assertEqual(bin_index(0.1), 1)
        self.assertEqual(bin_index(0.999), 9)
        self.assertEqual(bin_index(1.0), 9)  # closed last bin

    def test_calibration_bins_known_input(self):
        bins = calibration_bins([(0.05, False), (0.95, True), (0.92, False)])
        self.assertEqual(len(bins), 10)
        self.assertEqual(bins[0]["n"], 1)
        self.assertEqual(bins[0]["observed"], 0.0)
        self.assertEqual(bins[9]["n"], 2)
        self.assertAlmostEqual(bins[9]["mean_predicted"], 0.935, places=6)
        self.assertAlmostEqual(bins[9]["observed"], 0.5, places=12)
        self.assertEqual(bins[5]["n"], 0)
        self.assertIsNone(bins[5]["observed"])
        self.assertTrue(bins[9]["bin"].endswith("]"))  # closed last bin
        self.assertTrue(bins[0]["bin"].endswith(")"))

    def test_ece_known_input(self):
        bins = calibration_bins([(0.05, False), (0.95, True), (0.92, False)])
        expected = 1 / 3 * 0.05 + 2 / 3 * abs(0.5 - 0.935)
        self.assertAlmostEqual(expected_calibration_error(bins), expected, places=9)

    def test_perfectly_calibrated_ece_is_zero(self):
        bins = calibration_bins([(0.5, True), (0.5, False)])
        self.assertAlmostEqual(expected_calibration_error(bins), 0.0, places=12)

    def test_bootstrap_deterministic_under_fixed_seed(self):
        sample = [(0.7, True), (0.6, False), (0.9, True), (0.2, False), (0.4, True)]
        first = bootstrap_brier_ci(sample, resamples=300, seed=42)
        second = bootstrap_brier_ci(sample, resamples=300, seed=42)
        self.assertEqual(first, second)
        other_seed = bootstrap_brier_ci(sample, resamples=300, seed=43)
        self.assertNotEqual(first, other_seed)

    def test_bootstrap_ci_brackets_point_estimate(self):
        sample = [(0.8, True)] * 12 + [(0.3, False)] * 8 + [(0.9, False)] * 3
        low, high = bootstrap_brier_ci(sample, resamples=500, seed=7)
        point = brier_score(sample)
        self.assertLessEqual(low, point)
        self.assertGreaterEqual(high, point)
        self.assertLess(low, high)

    def test_metrics_block_baselines(self):
        pairs = [(0.9, True), (0.8, True), (0.3, False), (0.6, True)]
        block = metrics_block(pairs, train_recall_rate=0.75, bootstrap_resamples=50)
        self.assertEqual(block["n"], 4)
        self.assertEqual(len(block["bins"]), 10)
        self.assertAlmostEqual(
            block["baselines"]["chance_0.5"]["brier"], 0.25, places=12
        )
        constant = block["baselines"]["constant_train_rate"]
        self.assertEqual(constant["p"], 0.75)
        expected = ((0.75 - 1) ** 2 * 3 + (0.75 - 0) ** 2) / 4
        self.assertAlmostEqual(constant["brier"], expected, places=6)
        self.assertEqual(block["log_loss_epsilon"], mc.LOG_LOSS_EPSILON)
        self.assertEqual(block["caveats"], [])

    def test_metrics_block_flags_degenerate_and_single_bin_holdouts(self):
        # all outcomes recalled and every prediction in one bin: both
        # honesty caveats must fire
        block = metrics_block(
            [(0.95, True), (0.97, True), (0.99, True)],
            train_recall_rate=0.9,
            bootstrap_resamples=50,
        )
        self.assertEqual(len(block["caveats"]), 2)
        self.assertIn("degenerate holdout", block["caveats"][0])
        self.assertIn("single bin", block["caveats"][1])
        # all-lapse holdout flags the other side
        block = metrics_block(
            [(0.2, False), (0.8, False)],
            train_recall_rate=0.5,
            bootstrap_resamples=50,
        )
        self.assertTrue(any("zero recalls" in c for c in block["caveats"]))


class SplitAndHoldoutTest(unittest.TestCase):
    def test_is_eligible_mirrors_rslib(self):
        # graded review/learning/relearning rows count
        for kind in (mc.REVLOG_LEARNING, mc.REVLOG_REVIEW, mc.REVLOG_RELEARNING):
            self.assertTrue(is_eligible(RevlogRow(1, 1, 3, kind, 2500)))
        # ungraded rows (manual/rescheduled/reset carry ease 0) never count
        for kind in (mc.REVLOG_MANUAL, mc.REVLOG_RESCHEDULED, mc.REVLOG_REVIEW):
            self.assertFalse(is_eligible(RevlogRow(1, 1, 0, kind, 2500)))
        # cramming = Filtered with factor 0 (rslib is_cramming)
        self.assertFalse(is_eligible(RevlogRow(1, 1, 3, mc.REVLOG_FILTERED, 0)))
        # graded filtered review with a factor DOES affect scheduling
        self.assertTrue(is_eligible(RevlogRow(1, 1, 3, mc.REVLOG_FILTERED, 2500)))

    def test_choose_cutoff_quantile(self):
        ids = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        self.assertEqual(choose_cutoff(ids, 0.75), 80)  # floor(0.75*10)=7 -> ids[7]
        self.assertEqual(choose_cutoff(ids, 0.5), 60)
        self.assertEqual(choose_cutoff(list(reversed(ids)), 0.5), 60)  # sorts
        with self.assertRaises(CalibrationError):
            choose_cutoff([], 0.75)
        with self.assertRaises(CalibrationError):
            choose_cutoff(ids, 1.5)

    def _micro_rows(self) -> list[RevlogRow]:
        day = MS_PER_DAY
        return [
            RevlogRow(1 * day, 1, 3, mc.REVLOG_LEARNING, 0),
            RevlogRow(2 * day, 1, 3, mc.REVLOG_REVIEW, 2500),
            RevlogRow(5 * day, 1, 1, mc.REVLOG_REVIEW, 2500),  # holdout, lapse
            RevlogRow(6 * day, 1, 3, mc.REVLOG_REVIEW, 2500),  # later: excluded
            RevlogRow(1 * day + 1, 2, 3, mc.REVLOG_LEARNING, 0),
            RevlogRow(5 * day + 1, 2, 0, mc.REVLOG_MANUAL, 0),  # ungraded: skip
            RevlogRow(6 * day + 1, 2, 4, mc.REVLOG_REVIEW, 2500),  # holdout
            RevlogRow(5 * day + 2, 3, 3, mc.REVLOG_LEARNING, 0),  # new post-cutoff
            RevlogRow(4 * day, 4, 2, mc.REVLOG_FILTERED, 0),  # cramming: skip
        ]

    def test_select_holdout_first_post_cutoff_per_card(self):
        day = MS_PER_DAY
        observations, stats = select_holdout(self._micro_rows(), 5 * day)
        self.assertEqual([obs.card_id for obs in observations], [1, 2])
        first, second = observations
        self.assertFalse(first.recalled)  # ease 1 = lapse
        self.assertTrue(second.recalled)  # ease 4
        self.assertAlmostEqual(first.elapsed_days, 3.0, places=12)
        # card 2: manual entry ignored; elapsed from the graded learn step
        self.assertAlmostEqual(second.elapsed_days, 5.0, places=9)
        self.assertEqual(stats["skipped_first_seen_post_cutoff"], 1)  # card 3
        self.assertEqual(stats["observations"], 2)
        # card 1's later post-cutoff review must not appear anywhere
        self.assertEqual(stats["cards_with_post_cutoff_reviews"], 3)

    def test_select_holdout_ease_boundary(self):
        # ease > 1 is recalled: Again=1 lapse, Hard=2 recalled
        day = MS_PER_DAY
        rows = [
            RevlogRow(1 * day, 7, 3, mc.REVLOG_LEARNING, 0),
            RevlogRow(3 * day, 7, 2, mc.REVLOG_REVIEW, 2500),
        ]
        observations, _ = select_holdout(rows, 2 * day)
        self.assertTrue(observations[0].recalled)

    def test_adaptive_cutoff_respects_explicit_quantile(self):
        rows = self._micro_rows()
        ids = sorted(row.id for row in rows if is_eligible(row))
        cutoff, quantile, tried = adaptive_cutoff(ids, rows, 0.5)
        self.assertEqual(quantile, 0.5)
        self.assertEqual(cutoff, choose_cutoff(ids, 0.5))
        self.assertEqual(len(tried), 1)

    def test_adaptive_cutoff_falls_back_to_default_when_target_unreachable(self):
        # tiny data: the >= 50 observation target is unreachable, so the
        # search tries 0.75 down to 0.50 then settles on the default rule
        rows = self._micro_rows()
        ids = sorted(row.id for row in rows if is_eligible(row))
        cutoff, quantile, tried = adaptive_cutoff(ids, rows, None)
        self.assertEqual(quantile, mc.DEFAULT_CUTOFF_QUANTILE)
        self.assertEqual(cutoff, choose_cutoff(ids, mc.DEFAULT_CUTOFF_QUANTILE))
        self.assertEqual(
            [entry["quantile"] for entry in tried],
            [0.75, 0.7, 0.65, 0.6, 0.55, 0.5],
        )

    def test_evaluate_tier_skips_stateless_cards_and_predicts_in_range(self):
        observations = [
            HoldoutObs(1, 10 * MS_PER_DAY, 5 * MS_PER_DAY, 5.0, True, 3),
            HoldoutObs(2, 10 * MS_PER_DAY, 9 * MS_PER_DAY, 1.0, False, 1),
            HoldoutObs(3, 10 * MS_PER_DAY, 9 * MS_PER_DAY, 1.0, True, 3),
        ]
        states = {
            1: {
                "stability": 5.0,
                "difficulty": 6.0,
                "decay": 0.2,
                "desired_retention": 0.9,
            },
            2: None,
            3: {
                "stability": 1.0,
                "difficulty": 4.0,
                "decay": 0.2,
                "desired_retention": 0.9,
            },
        }
        pairs, per_obs, skipped = evaluate_tier(observations, states)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(pairs), 2)
        # card 1: elapsed == stability -> exactly the 0.9 anchor
        self.assertAlmostEqual(pairs[0][0], 0.9, places=12)
        self.assertAlmostEqual(pairs[1][0], 0.9, places=12)
        for p, _ in pairs:
            self.assertTrue(0.0 <= p <= 1.0)
        self.assertEqual(per_obs[0]["card_id"], 1)
        self.assertTrue(per_obs[0]["recalled"])


class TruncationAndLeakageGuardTest(unittest.TestCase):
    """sqlite fixtures: a minimal collection shape (revlog + cards.data)."""

    def _make_source(self, path: Path, cutoff: int) -> None:
        con = sqlite3.connect(path)
        con.execute(
            "create table revlog (id integer primary key, cid integer, "
            "usn integer, ease integer, ivl integer, lastIvl integer, "
            "factor integer, time integer, type integer)"
        )
        con.execute(
            "create table cards (id integer primary key, data text not null default '')"
        )
        rows = [
            (cutoff - 3 * MS_PER_DAY, 1, 3, mc.REVLOG_LEARNING),
            (cutoff - 2 * MS_PER_DAY, 1, 3, mc.REVLOG_REVIEW),
            (cutoff, 1, 1, mc.REVLOG_REVIEW),  # boundary: id >= cutoff goes
            (cutoff + MS_PER_DAY, 2, 3, mc.REVLOG_REVIEW),
        ]
        con.executemany(
            "insert into revlog values (?, ?, -1, ?, 1, 1, 2500, 3000, ?)", rows
        )
        con.executemany(
            "insert into cards (id, data) values (?, ?)",
            [
                (
                    1,
                    json.dumps(
                        {
                            "s": 4.2,
                            "d": 5.1,
                            "dr": 0.9,
                            "decay": 0.1542,
                            "lrt": 1700000000,
                            "pos": 7,
                            "cd": '{"x":1}',
                        }
                    ),
                ),
                (2, json.dumps({"pos": 3})),
                (3, ""),
            ],
        )
        con.commit()
        con.close()

    def test_truncation_removes_post_cutoff_rows_and_fsrs_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.anki2"
            dest = Path(tmp) / "truncated.anki2"
            cutoff = 1_700_000_000_000
            self._make_source(source, cutoff)
            stats = build_truncated_copy(source, dest, cutoff)
            self.assertEqual(stats["revlog_rows_deleted"], 2)
            self.assertEqual(stats["cards_fsrs_state_cleared"], 1)

            guard = leakage_check(dest, cutoff)
            self.assertTrue(guard["passed"], guard)
            self.assertEqual(guard["post_cutoff_revlog_rows"], 0)
            self.assertEqual(guard["cards_with_fsrs_state"], 0)

            con = sqlite3.connect(dest)
            remaining = con.execute("select id from revlog order by id").fetchall()
            self.assertEqual(len(remaining), 2)
            self.assertTrue(all(rid < cutoff for (rid,) in remaining))
            data = json.loads(
                con.execute("select data from cards where id = 1").fetchone()[0]
            )
            con.close()
            # FSRS keys stripped; non-FSRS payload preserved
            self.assertEqual(set(data), {"pos", "cd"})
            self.assertEqual(data["pos"], 7)

            # the source is untouched
            con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            self.assertEqual(
                con.execute("select count(*) from revlog").fetchone()[0], 4
            )
            con.close()

    def test_leakage_guard_rejects_post_cutoff_contamination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.anki2"
            dest = Path(tmp) / "truncated.anki2"
            cutoff = 1_700_000_000_000
            self._make_source(source, cutoff)
            build_truncated_copy(source, dest, cutoff)

            con = sqlite3.connect(dest)
            con.execute(
                "insert into revlog values (?, 9, -1, 3, 1, 1, 2500, 3000, 1)",
                (cutoff + 5,),
            )
            con.commit()
            con.close()
            guard = leakage_check(dest, cutoff)
            self.assertFalse(guard["passed"])
            self.assertEqual(guard["post_cutoff_revlog_rows"], 1)

    def test_leakage_guard_rejects_surviving_card_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.anki2"
            dest = Path(tmp) / "truncated.anki2"
            cutoff = 1_700_000_000_000
            self._make_source(source, cutoff)
            build_truncated_copy(source, dest, cutoff)

            con = sqlite3.connect(dest)
            con.execute(
                "update cards set data = ? where id = 2",
                (json.dumps({"pos": 3, "s": 9.9}),),
            )
            con.commit()
            con.close()
            guard = leakage_check(dest, cutoff)
            self.assertFalse(guard["passed"])
            self.assertEqual(guard["cards_with_fsrs_state"], 1)

    def test_strip_fsrs_card_state(self):
        obj = {"s": 1.0, "d": 2.0, "dr": 0.9, "decay": 0.1, "lrt": 5, "pos": 1}
        self.assertTrue(strip_fsrs_card_state(obj))
        self.assertEqual(obj, {"pos": 1})
        untouched = {"pos": 2, "cd": "{}"}
        self.assertFalse(strip_fsrs_card_state(untouched))
        self.assertEqual(untouched, {"pos": 2, "cd": "{}"})


class ChartTest(unittest.TestCase):
    def _tiers(self, pairs: list[tuple[float, bool]]) -> list[dict]:
        bins = calibration_bins(pairs)
        return [
            {
                "tier": mc.TIER_DEFAULTS,
                "color": mc.TIER_COLORS[mc.TIER_DEFAULTS],
                "bins": bins,
                "n": len(pairs),
            }
        ]

    def test_svg_structure(self):
        pairs = [(0.15, False), (0.55, True), (0.85, True), (0.95, True), (0.92, False)]
        svg = reliability_svg(self._tiers(pairs), "title text", "subtitle text")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('class="diagonal"', svg)
        self.assertIn("stroke-dasharray", svg)
        non_empty_bins = sum(1 for b in calibration_bins(pairs) if b["n"])
        self.assertEqual(svg.count('class="bin-point"'), non_empty_bins)
        self.assertEqual(svg.count('class="bin-count"'), non_empty_bins)
        self.assertEqual(svg.count('class="hist-bar"'), mc.N_BINS)
        self.assertIn("n=2", svg)  # the two-prediction bin is labelled
        self.assertIn("title text", svg)
        self.assertIn("observed recall", svg)
        self.assertNotIn('class="low-n-warning"', svg)

    def test_svg_low_n_warning_and_two_tiers(self):
        pairs_a = [(0.9, True), (0.3, False)]
        pairs_b = [(0.8, True), (0.2, False), (0.5, True)]
        tiers = [
            {
                "tier": mc.TIER_TRAINED,
                "color": mc.TIER_COLORS[mc.TIER_TRAINED],
                "bins": calibration_bins(pairs_a),
                "n": len(pairs_a),
            },
            {
                "tier": mc.TIER_DEFAULTS,
                "color": mc.TIER_COLORS[mc.TIER_DEFAULTS],
                "bins": calibration_bins(pairs_b),
                "n": len(pairs_b),
            },
        ]
        svg = reliability_svg(tiers, "t", "s", warning="LOW N: weak evidence")
        self.assertIn('class="low-n-warning"', svg)
        self.assertIn("LOW N: weak evidence", svg)
        self.assertIn(mc.TIER_TRAINED, svg)
        self.assertIn(mc.TIER_DEFAULTS, svg)
        # two tiers -> both histograms drawn
        self.assertEqual(svg.count('class="hist-bar"'), 2 * mc.N_BINS)
        # count labels come from the primary (first) tier only
        self.assertEqual(
            svg.count('class="bin-count"'),
            sum(1 for b in calibration_bins(pairs_a) if b["n"]),
        )


class ReportWritingTest(unittest.TestCase):
    def _fake_report(self, low_n: bool) -> dict:
        pairs = [(0.9, True), (0.7, True), (0.4, False), (0.85, True)]
        metrics = metrics_block(pairs, 0.8, bootstrap_resamples=50)
        return {
            "meta": {
                "tool": "memory_calibration",
                "generated_at": "2026-07-04T00:00:00+00:00",
                "modes": ["collection"],
            },
            "collection": {
                "path": "/tmp/fake.anki2",
                "revlog_rows": 10,
                "eligible_graded_reviews": 8,
                "split": {
                    "rule": "test rule",
                    "requested_quantile": None,
                    "used_quantile": 0.75,
                    "quantiles_tried": [{"quantile": 0.75, "observations": 4}],
                    "cutoff_ms": 1700000000000,
                    "cutoff_utc": "2023-11-14T22:13:20+00:00",
                    "train_reviews": 6,
                    "train_recall_rate": 0.8,
                    "post_cutoff_eligible_rows": 4,
                    "cards_with_post_cutoff_reviews": 4,
                    "skipped_first_seen_post_cutoff": 0,
                    "observations": 4,
                },
                "truncation": {"revlog_rows_deleted": 4, "cards_fsrs_state_cleared": 1},
                "leakage_guard": {
                    "passed": True,
                    "post_cutoff_revlog_rows": 0,
                    "cards_with_fsrs_state": 0,
                    "cutoff_ms": 1700000000000,
                },
                "training": {"status": "insufficient", "fsrs_items": 0},
                "tiers": [
                    {
                        "tier": mc.TIER_DEFAULTS,
                        "params_source": "defaults",
                        "params": None,
                        "skipped_no_memory_state": 0,
                        "metrics": metrics,
                        "per_observation": [],
                    }
                ],
                "low_n_warning": low_n,
                "truncated_copy": "/tmp/truncated.anki2",
            },
            "failures": [],
            "exit_code": 0,
        }

    def test_write_reports_creates_json_md_and_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._fake_report(low_n=True)
            json_path, md_path, chart_path = write_reports(report, Path(tmp))
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIsNotNone(chart_path)
            self.assertTrue(chart_path.exists())

            parsed = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["exit_code"], 0)
            self.assertEqual(parsed["chart"], str(chart_path))

            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("# Memory calibration report", markdown)
            self.assertIn("LOW-N WARNING", markdown)
            self.assertIn("inference.rs:60-63", markdown)  # provenance
            self.assertIn("| bin | n | mean predicted | observed |", markdown)

            svg = chart_path.read_text(encoding="utf-8")
            self.assertIn('class="diagonal"', svg)
            self.assertIn('class="low-n-warning"', svg)

    def test_no_low_n_banner_when_n_is_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._fake_report(low_n=False)
            _, md_path, chart_path = write_reports(report, Path(tmp))
            self.assertNotIn("LOW-N WARNING", md_path.read_text(encoding="utf-8"))
            self.assertNotIn(
                'class="low-n-warning"', chart_path.read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
