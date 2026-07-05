# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for bench: the nearest-rank percentile math, the §10
target-comparison / PASS-FAIL logic, report rendering, and the
deterministic deck-spec helpers. bench.py must be importable WITHOUT
pylib (it imports anki lazily), so these run under plain python3:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bench  # noqa: E402
from bench import (  # noqa: E402
    TARGETS,
    TOPICS,
    cluster_for_index,
    deck_spec,
    evaluate_targets,
    fields_for_index,
    format_table,
    percentile,
    rating_for_index,
    render_markdown,
    summarize,
    tags_for_index,
    topic_for_index,
)

ALL_ACTIONS = (
    "button_press_ack",
    "next_card_after_grade",
    "dashboard_first_load",
    "dashboard_refresh",
    "session_sync",
    "cold_start",
    "peak_memory",
)

#: A memory reading comfortably under the stated limit, whatever it is.
MEMORY_OK_MB = bench.MEMORY_LIMIT_MB * 0.4


class ImportContractTests(unittest.TestCase):
    def test_importable_without_pylib(self) -> None:
        """bench must import under plain python3: every pylib import has
        to live inside a function body, never at module level. (Checked
        via the AST rather than sys.modules, because other test modules
        in this suite legitimately stub `anki` into sys.modules.)"""
        tree = ast.parse(Path(bench.__file__).read_text(encoding="utf-8"))
        offenders = [
            node.lineno
            for node in tree.body  # module level only, not function bodies
            if (
                isinstance(node, ast.Import)
                and any(alias.name.split(".")[0] == "anki" for alias in node.names)
            )
            or (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "anki"
            )
        ]
        self.assertEqual(offenders, [], "module-level pylib import(s) found")

    def test_percentile_method_is_documented(self) -> None:
        self.assertEqual(bench.PERCENTILE_METHOD, "nearest-rank")


class PercentileTests(unittest.TestCase):
    """Nearest-rank: rank = ceil(p/100 * n) on the sorted samples."""

    def test_known_vector(self) -> None:
        xs = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        self.assertEqual(percentile(xs, 50), 50.0)  # rank ceil(5.0) = 5
        self.assertEqual(percentile(xs, 95), 100.0)  # rank ceil(9.5) = 10
        self.assertEqual(percentile(xs, 90), 90.0)  # rank ceil(9.0) = 9
        self.assertEqual(percentile(xs, 100), 100.0)
        self.assertEqual(percentile(xs, 1), 10.0)  # rank ceil(0.1) -> 1

    def test_unsorted_input_is_sorted_internally(self) -> None:
        self.assertEqual(percentile([30.0, 10.0, 20.0], 50), 20.0)

    def test_single_sample(self) -> None:
        self.assertEqual(percentile([42.0], 50), 42.0)
        self.assertEqual(percentile([42.0], 95), 42.0)

    def test_five_samples_p95_is_max(self) -> None:
        # ceil(0.95 * 5) = 5 -> the largest sample; small-n p95 is honest
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0, 9.0], 95), 9.0)

    def test_rejects_empty_and_bad_pct(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 50)
        with self.assertRaises(ValueError):
            percentile([1.0], 0)
        with self.assertRaises(ValueError):
            percentile([1.0], 101)

    def test_summarize_known_stats(self) -> None:
        stats = summarize([4.0, 1.0, 3.0, 2.0])
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["p50"], 2.0)  # rank ceil(2.0) = 2
        self.assertEqual(stats["p95"], 4.0)  # rank ceil(3.8) = 4
        self.assertEqual(stats["worst"], 4.0)
        self.assertEqual(stats["mean"], 2.5)


def stats_with_p95(p95_ms: float, n: int = 30) -> dict:
    """A stats dict whose p95 and worst equal p95_ms exactly."""
    return {
        "n": n,
        "min": p95_ms / 10,
        "p50": p95_ms / 2,
        "p95": p95_ms,
        "worst": p95_ms,
        "mean": p95_ms / 2,
    }


def make_action_stats(overrides: dict[str, float] | None = None) -> dict:
    """Passing-by-default stats for the six timing actions."""
    p95s = {
        "button_press_ack": 1.0,
        "next_card_after_grade": 2.0,
        "dashboard_first_load": 100.0,
        "dashboard_refresh": 50.0,
        "session_sync": 800.0,
        "cold_start": 900.0,
    }
    p95s.update(overrides or {})
    return {action: stats_with_p95(value) for action, value in p95s.items()}


class TargetLogicTests(unittest.TestCase):
    def test_all_seven_actions_have_targets(self) -> None:
        self.assertEqual(tuple(TARGETS), ALL_ACTIONS)

    def test_all_pass(self) -> None:
        rows = evaluate_targets(make_action_stats(), memory_mb=MEMORY_OK_MB)
        self.assertEqual([row["action"] for row in rows], list(ALL_ACTIONS))
        self.assertTrue(all(row["passed"] for row in rows))

    def test_comparison_is_strictly_less_than(self) -> None:
        # exactly-on-target must FAIL (target is "< X", not "<= X")
        rows = evaluate_targets(
            make_action_stats({"button_press_ack": 50.0}), memory_mb=MEMORY_OK_MB
        )
        by_action = {row["action"]: row for row in rows}
        self.assertFalse(by_action["button_press_ack"]["passed"])
        rows = evaluate_targets(
            make_action_stats({"button_press_ack": 49.99}), memory_mb=MEMORY_OK_MB
        )
        by_action = {row["action"]: row for row in rows}
        self.assertTrue(by_action["button_press_ack"]["passed"])

    def test_each_timing_action_fails_over_its_own_target(self) -> None:
        limits = {
            "button_press_ack": 50.0,
            "next_card_after_grade": 100.0,
            "dashboard_first_load": 1000.0,
            "dashboard_refresh": 500.0,
            "session_sync": 5000.0,
            "cold_start": 5000.0,
        }
        for action, limit in limits.items():
            rows = evaluate_targets(
                make_action_stats({action: limit + 0.01}), memory_mb=MEMORY_OK_MB
            )
            by_action = {row["action"]: row for row in rows}
            self.assertFalse(by_action[action]["passed"], action)
            others = [r for r in rows if r["action"] not in (action, "peak_memory")]
            self.assertTrue(all(r["passed"] for r in others), action)

    def test_memory_row_compares_measured_to_stated_limit(self) -> None:
        rows = evaluate_targets(
            make_action_stats(), memory_mb=bench.MEMORY_LIMIT_MB - 0.1
        )
        self.assertTrue(rows[-1]["passed"])
        rows = evaluate_targets(
            make_action_stats(), memory_mb=float(bench.MEMORY_LIMIT_MB)
        )
        self.assertFalse(rows[-1]["passed"])  # strict <
        self.assertEqual(rows[-1]["action"], "peak_memory")

    def test_rows_carry_percentiles_for_the_report(self) -> None:
        rows = evaluate_targets(make_action_stats(), memory_mb=MEMORY_OK_MB)
        for row in rows:
            if row["action"] == "peak_memory":
                self.assertNotIn("p95", row)
            else:
                for key in ("n", "p50", "p95", "worst"):
                    self.assertIn(key, row)


def make_report(memory_mb: float = MEMORY_OK_MB) -> dict:
    """A minimal but shape-complete report for rendering tests."""
    action_stats = make_action_stats()
    actions: dict = {}
    for action, stats in action_stats.items():
        actions[action] = {"samples_ms": [stats["p50"]], "stats": stats}
    actions["session_sync"].update(
        session_cards_per_sample=20,
        initial_full_upload_ms_uncounted=1234.5,
        endpoint="http://127.0.0.1:28701/",
    )
    actions["cold_start"].update(
        in_process_stats=stats_with_p95(700.0),
        child_phases=[],
    )
    actions["peak_memory"] = {
        "measured_mb": memory_mb,
        "stated_limit_mb": bench.MEMORY_LIMIT_MB,
        "ru_maxrss_raw": int(memory_mb * 2**20),
        "ru_maxrss_unit": "bytes",
        "workload": "open + dashboard queries + queue build + 5 answers",
    }
    return {
        "meta": {
            "tool": "bench",
            "generated_at": "2026-07-04T00:00:00+00:00",
            "machine": {
                "chip": "Test Chip",
                "ram_gb": 32.0,
                "cpu_count": 10,
                "os": "macOS 26.5.1",
                "python": "3.13.5",
            },
            "percentile_method": bench.PERCENTILE_METHOD,
            "rerun": bench.RERUN_COMMAND,
            "bench_wall_s": 100.0,
        },
        "deck": {
            "spec": deck_spec(50_000),
            "cached": True,
            "card_count": 50_000,
            "revlog_count": 1_000,
            "graded": 1_000,
            "build_wall_s": 60.0,
            "built_at": "2026-07-04T00:00:00+00:00",
        },
        "actions": actions,
        "targets_table": evaluate_targets(action_stats, memory_mb),
        "target_failures": [],
        "exit_code": 0,
    }


class RenderTests(unittest.TestCase):
    def test_markdown_contains_every_action_and_verdict(self) -> None:
        text = render_markdown(make_report())
        for action in ALL_ACTIONS:
            self.assertIn(action, text)
        self.assertIn("PASS", text)
        self.assertIn("§10 target", text)
        self.assertIn("Test Chip", text)
        self.assertIn("nearest-rank", text)

    def test_markdown_failure_is_loud(self) -> None:
        text = render_markdown(make_report(memory_mb=bench.MEMORY_LIMIT_MB + 1))
        self.assertIn("**FAIL**", text)

    def test_markdown_discloses_the_hard_truths(self) -> None:
        text = render_markdown(make_report())
        # phone timings not fabricated
        self.assertIn("instrumented device", text)
        self.assertIn("NOT measured", text)
        # screen freeze only proxied
        self.assertIn("PROXIED", text)
        # UI paint not claimed
        self.assertIn("paint", text)
        # ru_maxrss platform quirk
        self.assertIn("ru_maxrss", text)
        self.assertIn("bytes", text)
        # concrete off-UI-thread evidence
        self.assertIn("DashboardPage.svelte", text)
        # how to re-run
        self.assertIn("just bench", text)
        self.assertIn(bench.RERUN_COMMAND, text)

    def test_markdown_marks_non_default_deck_as_dev_run(self) -> None:
        report = make_report()
        report["deck"]["spec"] = deck_spec(2_000)
        report["deck"]["card_count"] = 2_000
        text = render_markdown(report)
        self.assertIn("Dev run only", text)
        self.assertNotIn("Dev run only", render_markdown(make_report()))

    def test_stdout_table_lists_all_actions(self) -> None:
        table = format_table(make_report()["targets_table"])
        for action in ALL_ACTIONS:
            self.assertIn(action, table)
        self.assertIn("verdict", table)


class DeckSpecTests(unittest.TestCase):
    """The deck layout is a pure function of the card index."""

    def test_topic_assignment_is_deterministic_and_valid(self) -> None:
        for index in (0, 1, 9, 10, 4_999, 49_999):
            self.assertEqual(topic_for_index(index), topic_for_index(index))
            self.assertIn(topic_for_index(index), TOPICS)

    def test_all_ten_topics_covered(self) -> None:
        self.assertEqual({topic_for_index(index) for index in range(100)}, set(TOPICS))

    def test_topics_match_probe_harness_slugs(self) -> None:
        self.assertEqual(
            TOPICS,
            (
                "ethics",
                "quantitative_methods",
                "economics",
                "financial_statement_analysis",
                "corporate_issuers",
                "equity_investments",
                "fixed_income",
                "derivatives",
                "alternative_investments",
                "portfolio_management",
            ),
        )

    def test_cluster_belongs_to_the_cards_topic(self) -> None:
        for index in (0, 7, 123, 45_678):
            cluster = cluster_for_index(index)
            topic, family = cluster.rsplit("::", 1)
            self.assertEqual(topic, topic_for_index(index))
            self.assertRegex(family, r"^f\d{2}$")

    def test_tags_carry_the_forks_taxonomy(self) -> None:
        tags = tags_for_index(12)
        self.assertEqual(len(tags), 2)
        self.assertTrue(tags[0].startswith("cfa::topic::"))
        self.assertTrue(tags[1].startswith("cluster::"))
        self.assertEqual(tags[0], f"cfa::topic::{topic_for_index(12)}")
        self.assertEqual(tags[1], f"cluster::{cluster_for_index(12)}")

    def test_fields_are_unique_per_index(self) -> None:
        fronts = {fields_for_index(index)[0] for index in range(500)}
        self.assertEqual(len(fronts), 500)
        front, back = fields_for_index(3)
        self.assertTrue(front and back)

    def test_ratings_are_valid_and_mostly_good(self) -> None:
        ratings = [rating_for_index(index) for index in range(1_000)]
        self.assertTrue(set(ratings) <= {0, 1, 2, 3})
        self.assertEqual(ratings[:20], ratings[20:40])  # cyclic pattern
        good_share = ratings.count(2) / len(ratings)
        self.assertGreater(good_share, 0.5)
        self.assertGreater(ratings.count(0), 0)  # some lapses

    def test_deck_spec_scales_down_for_dev_runs(self) -> None:
        full = deck_spec(50_000)
        self.assertEqual(full["cards"], 50_000)
        self.assertEqual(full["graded"], 1_000)
        self.assertEqual(full["due_spread"], 14_000)
        small = deck_spec(2_000)
        self.assertEqual(small["graded"], 400)
        self.assertEqual(small["due_spread"], 666)
        # spec equality is what validates the cache; must be deterministic
        self.assertEqual(deck_spec(50_000), full)


if __name__ == "__main__":
    unittest.main()
