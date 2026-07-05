# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for crash_test's pure helpers: committed-vs-in-flight
accounting, integrity-output parsing, engine-check classification,
report rendering and the CLI surface. stdlib only - importing the module
must NOT pull in pylib; run with:

    python3 -m unittest discover -s tools/speedrun/tests -t tools/speedrun
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crash_test  # noqa: E402
from crash_test import (  # noqa: E402
    classify_db_check,
    classify_iteration,
    headline,
    parse_child_log,
    parse_integrity_output,
    pick_dead_endpoint,
    render_markdown,
)


class ImportHygieneTests(unittest.TestCase):
    def test_module_import_does_not_load_pylib(self) -> None:
        # hermetic subprocess: sibling test files install fake `anki`
        # modules in this process, so sys.modules here proves nothing
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import crash_test; "
                "assert not any(m == 'anki' or m.startswith('anki.') "
                "for m in sys.modules), 'pylib loaded at import time'",
                str(Path(__file__).resolve().parents[1]),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ParseChildLogTests(unittest.TestCase):
    def test_empty(self) -> None:
        counts = parse_child_log("")
        self.assertEqual(counts["attempted"], 0)
        self.assertEqual(counts["committed"], 0)

    def test_matched_pairs(self) -> None:
        text = "ATTEMPT 1 100\nCOMMIT 1 100\nATTEMPT 2 101\nCOMMIT 2 101\n"
        counts = parse_child_log(text)
        self.assertEqual(counts["attempted"], 2)
        self.assertEqual(counts["committed"], 2)

    def test_in_flight_attempt_without_commit(self) -> None:
        text = "ATTEMPT 1 100\nCOMMIT 1 100\nATTEMPT 2 101\n"
        counts = parse_child_log(text)
        self.assertEqual(counts["attempted"], 2)
        self.assertEqual(counts["committed"], 1)

    def test_torn_trailing_line_dropped(self) -> None:
        # SIGKILL mid-write of the final line: the fragment must not count
        text = "ATTEMPT 1 100\nCOMMIT 1 100\nATTEM"
        counts = parse_child_log(text)
        self.assertEqual(counts["attempted"], 1)
        self.assertEqual(counts["committed"], 1)

    def test_torn_commit_line_dropped(self) -> None:
        text = "ATTEMPT 1 100\nCOMM"
        counts = parse_child_log(text)
        self.assertEqual(counts["attempted"], 1)
        self.assertEqual(counts["committed"], 0)

    def test_queue_empty_exit_flag(self) -> None:
        text = "ATTEMPT 1 100\nCOMMIT 1 100\nEXIT queue_empty\n"
        self.assertEqual(parse_child_log(text)["queue_empty_exit"], 1)


class ClassifyIterationTests(unittest.TestCase):
    def test_clean_kill_between_answers(self) -> None:
        row = classify_iteration(100, 150, attempted=50, committed=50)
        self.assertEqual(row["rolled_back_in_flight"], 0)
        self.assertEqual(row["committed_unlogged"], 0)
        self.assertEqual(row["corruption"], [])
        self.assertEqual(row["anomalies"], [])

    def test_in_flight_rolled_back_is_not_corruption(self) -> None:
        # child attempted 51, logged 50 commits, revlog gained 50: the
        # in-flight answer rolled back - correct SQLite behaviour.
        row = classify_iteration(100, 150, attempted=51, committed=50)
        self.assertEqual(row["rolled_back_in_flight"], 1)
        self.assertEqual(row["committed_unlogged"], 0)
        self.assertEqual(row["corruption"], [])
        self.assertEqual(row["anomalies"], [])

    def test_committed_but_unlogged(self) -> None:
        # kill landed between the SQLite commit and the COMMIT log line
        row = classify_iteration(100, 151, attempted=51, committed=50)
        self.assertEqual(row["rolled_back_in_flight"], 0)
        self.assertEqual(row["committed_unlogged"], 1)
        self.assertEqual(row["corruption"], [])
        self.assertEqual(row["anomalies"], [])

    def test_lost_committed_answer_is_corruption(self) -> None:
        row = classify_iteration(100, 149, attempted=50, committed=50)
        self.assertTrue(any("durability" in c for c in row["corruption"]))

    def test_revlog_decrease_is_corruption(self) -> None:
        row = classify_iteration(100, 90, attempted=0, committed=0)
        self.assertTrue(any("DECREASED" in c for c in row["corruption"]))

    def test_phantom_rows_flagged(self) -> None:
        row = classify_iteration(100, 153, attempted=51, committed=50)
        self.assertTrue(any("phantom" in c for c in row["corruption"]))

    def test_impossible_in_flight_count_is_anomaly(self) -> None:
        row = classify_iteration(100, 150, attempted=52, committed=50)
        self.assertTrue(any("anomaly" in a for a in row["anomalies"]))

    def test_extra_row_without_in_flight_is_anomaly(self) -> None:
        row = classify_iteration(100, 151, attempted=50, committed=50)
        self.assertTrue(row["anomalies"])
        self.assertEqual(row["corruption"], [])


class ParseIntegrityOutputTests(unittest.TestCase):
    def test_ok(self) -> None:
        self.assertEqual(parse_integrity_output("ok\n"), (True, "ok"))

    def test_corruption_text(self) -> None:
        ok, detail = parse_integrity_output(
            "*** in database main ***\nPage 3: btreeInitPage() returns error"
        )
        self.assertFalse(ok)
        self.assertIn("Page 3", detail)

    def test_empty_output_is_not_ok(self) -> None:
        self.assertFalse(parse_integrity_output("")[0])


class ClassifyDbCheckTests(unittest.TestCase):
    def test_ok_with_housekeeping_trailer(self) -> None:
        verdict = classify_db_check("Database rebuilt and optimized.", ok=True)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["corruption_class"], [])
        self.assertEqual(len(verdict["housekeeping"]), 1)

    def test_real_problem_is_corruption_class(self) -> None:
        verdict = classify_db_check(
            "Deleted 2 cards with missing note.\nDatabase rebuilt and optimized.",
            ok=False,
        )
        self.assertFalse(verdict["ok"])
        self.assertEqual(len(verdict["corruption_class"]), 1)
        self.assertIn("missing note", verdict["corruption_class"][0])
        self.assertEqual(len(verdict["housekeeping"]), 1)

    def test_hard_dberror_text(self) -> None:
        verdict = classify_db_check("collection is corrupt", ok=False)
        self.assertEqual(verdict["corruption_class"], ["collection is corrupt"])


class HeadlineTests(unittest.TestCase):
    def test_zero_of_twenty(self) -> None:
        self.assertEqual(headline(0, 20), "corrupted collections: 0 of 20")

    def test_honest_nonzero(self) -> None:
        self.assertEqual(headline(3, 20), "corrupted collections: 3 of 20")


class DeadEndpointTests(unittest.TestCase):
    def test_loopback_only(self) -> None:
        endpoint = pick_dead_endpoint()
        self.assertTrue(endpoint.startswith("http://127.0.0.1:"))


def _fake_report() -> dict:
    iteration = {
        "iteration": 1,
        "kill_delay_s": 0.5,
        "killed": True,
        "saw_commit_before_kill": True,
        "child_exited_before_kill": False,
        "queue_empty_exit": False,
        "wal_present_after_kill": True,
        "integrity_check_ok": True,
        "quick_check_ok": True,
        "db_check": {"ok": True, "corruption_class": [], "housekeeping": []},
        "revlog_before": 0,
        "revlog_after": 40,
        "revlog_delta": 40,
        "attempted": 41,
        "committed_logged": 40,
        "rolled_back_in_flight": 1,
        "committed_unlogged": 0,
        "corruption": [],
        "anomalies": [],
        "corrupted": False,
    }
    return {
        "meta": {
            "generated_at": "2026-07-04T00:00:00+00:00",
            "seed": 1,
            "collection": "x.anki2",
            "invocation": "python crash_test.py",
        },
        "crash": {
            "headline": headline(0, 20),
            "iterations_run": 20,
            "iterations": [iteration],
            "corrupted_iterations": [],
            "corrupted_collections": 0,
            "totals": {
                "committed": 40,
                "rolled_back_in_flight": 1,
                "committed_unlogged": 0,
                "anomalies": 0,
            },
        },
        "network_off": {
            "dead_endpoint": "http://127.0.0.1:1",
            "checks": [
                {
                    "name": "coach call abstains gracefully",
                    "passed": True,
                    "detail": "reason='backend error or timeout'",
                }
            ],
            "passed": True,
        },
        "honesty": ["note one"],
        "failures": [],
    }


class RenderMarkdownTests(unittest.TestCase):
    def test_contains_headline_and_network_section(self) -> None:
        text = render_markdown(_fake_report())
        self.assertIn("CORRUPTED COLLECTIONS: 0 OF 20", text)
        self.assertIn("Network-off section", text)
        self.assertIn("abstains gracefully", text)
        self.assertIn("Honesty notes", text)

    def test_failures_and_corruption_render_loudly(self) -> None:
        report = _fake_report()
        report["crash"]["corrupted_iterations"] = [1]
        report["crash"]["headline"] = headline(1, 20)
        report["crash"]["iterations"][0]["corrupted"] = True
        report["crash"]["iterations"][0]["corruption"] = ["durability violation: x"]
        report["failures"] = ["CORRUPTION: corrupted collections: 1 of 20"]
        text = render_markdown(report)
        self.assertIn("CORRUPTED COLLECTIONS: 1 OF 20", text)
        self.assertIn("CORRUPTION FOUND", text)
        self.assertIn("durability violation", text)
        self.assertIn("## FAILURES", text)


class CliSurfaceTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = crash_test.build_arg_parser().parse_args([])
        self.assertEqual(args.iterations, 20)
        self.assertEqual(args.cards, 400)
        self.assertFalse(args.child)
        self.assertFalse(args.skip_network_off)
        self.assertFalse(args.skip_crash)

    def test_child_mode_args(self) -> None:
        args = crash_test.build_arg_parser().parse_args(
            ["--child", "--collection", "c.anki2", "--child-log", "log.txt"]
        )
        self.assertTrue(args.child)
        self.assertEqual(args.collection, "c.anki2")
        self.assertEqual(args.child_log, "log.txt")

    def test_child_mode_requires_paths(self) -> None:
        self.assertEqual(crash_test.main(["--child"]), 2)

    def test_overrides(self) -> None:
        args = crash_test.build_arg_parser().parse_args(
            ["--iterations", "5", "--cards", "50", "--seed", "7"]
        )
        self.assertEqual((args.iterations, args.cards, args.seed), (5, 50, 7))


if __name__ == "__main__":
    unittest.main()
