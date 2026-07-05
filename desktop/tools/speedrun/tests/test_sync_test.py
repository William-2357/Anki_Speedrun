# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for sync_test's pure helpers: revlog union/dedupe logic
(seeded loss/duplication faults must be caught), the conflict-winner
assertion, report rendering and the CLI surface. stdlib only - importing
the module must NOT pull in pylib; run with:

    python3 -m unittest discover -s tools/speedrun/tests -t tools/speedrun
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync_test  # noqa: E402
from sync_test import (  # noqa: E402
    check_revlog_union,
    conflict_verdict,
    render_markdown,
)

#: 10 reviews on A (cards 1..10) + 10 different on B (cards 11..20),
#: revlog ids = taken-at epoch-ms.
EXPECTED_A = [(1000 + i, 100 + i) for i in range(10)]
EXPECTED_B = [(2000 + i, 200 + i) for i in range(10)]
CONVERGED = sorted(EXPECTED_A + EXPECTED_B)


class ImportHygieneTests(unittest.TestCase):
    def test_module_import_does_not_load_pylib(self) -> None:
        # hermetic subprocess: sibling test files install fake `anki`
        # modules in this process, so sys.modules here proves nothing
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import sync_test; "
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


class RevlogUnionTests(unittest.TestCase):
    def test_all_twenty_land_on_both_sides(self) -> None:
        result = check_revlog_union(
            list(CONVERGED), list(CONVERGED), EXPECTED_A, EXPECTED_B
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_total"], 20)
        self.assertEqual(result["per_side"]["a"]["found"], 20)
        self.assertEqual(result["per_side"]["b"]["found"], 20)
        self.assertEqual(result["lost"], [])
        self.assertEqual(result["duplicated"], [])

    def test_lost_entry_detected(self) -> None:
        # seeded fault: B's table is missing one of A's reviews
        b_rows = [row for row in CONVERGED if row != EXPECTED_A[3]]
        result = check_revlog_union(list(CONVERGED), b_rows, EXPECTED_A, EXPECTED_B)
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["lost"],
            [{"side": "b", "id": EXPECTED_A[3][0], "cid": EXPECTED_A[3][1]}],
        )
        self.assertEqual(result["per_side"]["b"]["found"], 19)

    def test_duplicated_entry_detected(self) -> None:
        # seeded fault: one review double-counted on A
        a_rows = list(CONVERGED) + [EXPECTED_B[0]]
        result = check_revlog_union(a_rows, list(CONVERGED), EXPECTED_A, EXPECTED_B)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["duplicated"]), 1)
        self.assertEqual(result["duplicated"][0]["side"], "a")
        self.assertEqual(result["duplicated"][0]["count"], 2)

    def test_id_collision_detected(self) -> None:
        # same revlog id on two different cards would slip past a
        # PK-only check; the (cid, id) view must flag it
        a_rows = list(CONVERGED) + [(EXPECTED_A[0][0], 999)]
        result = check_revlog_union(a_rows, list(CONVERGED), EXPECTED_A, EXPECTED_B)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["id_collisions"]), 1)
        self.assertEqual(result["id_collisions"][0]["id"], EXPECTED_A[0][0])

    def test_both_faults_reported_per_side(self) -> None:
        a_rows = [row for row in CONVERGED if row != EXPECTED_B[5]]
        b_rows = list(CONVERGED) + [EXPECTED_A[1]]
        result = check_revlog_union(a_rows, b_rows, EXPECTED_A, EXPECTED_B)
        self.assertFalse(result["passed"])
        self.assertEqual({row["side"] for row in result["lost"]}, {"a"})
        self.assertEqual({row["side"] for row in result["duplicated"]}, {"b"})

    def test_cross_side_epoch_ms_collision_tripwire(self) -> None:
        # the hazard an early run of sync_test hit: A and B mint the SAME
        # revlog id (same millisecond) for DIFFERENT cards; the merge
        # would silently drop the later-arriving entry
        expected_b = list(EXPECTED_B)
        expected_b[0] = (EXPECTED_A[0][0], expected_b[0][1])  # id collides
        result = check_revlog_union(
            list(CONVERGED), list(CONVERGED), EXPECTED_A, expected_b
        )
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["cross_side_id_collisions"]), 1)
        self.assertEqual(result["cross_side_id_collisions"][0]["id"], EXPECTED_A[0][0])


def _state(mod: int, due: int = 5, ivl: int = 1) -> dict:
    return {
        "mod": mod,
        "type": 1,
        "queue": 1,
        "due": due,
        "ivl": ivl,
        "factor": 2500,
        "reps": 1,
        "lapses": 0,
        "left": 1001,
    }


class ConflictVerdictTests(unittest.TestCase):
    def test_newer_mod_wins(self) -> None:
        older = _state(mod=1000, due=10)
        newer = _state(mod=1002, due=20)
        verdict = conflict_verdict(older, newer, dict(newer), dict(newer))
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["winner"], "B")
        self.assertIn("newer", verdict["reason"])

    def test_newer_mod_wins_when_a_is_newer(self) -> None:
        newer = _state(mod=1005, due=10)
        older = _state(mod=1001, due=20)
        verdict = conflict_verdict(newer, older, dict(newer), dict(newer))
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["winner"], "A")

    def test_non_convergence_fails(self) -> None:
        a = _state(mod=1000, due=10)
        b = _state(mod=1002, due=20)
        verdict = conflict_verdict(a, b, dict(a), dict(b))
        self.assertFalse(verdict["passed"])
        self.assertIsNone(verdict["winner"])
        self.assertIn("did NOT converge", verdict["reason"])

    def test_wrong_winner_fails(self) -> None:
        older = _state(mod=1000, due=10)
        newer = _state(mod=1002, due=20)
        # both clients ended on the OLDER copy: rule violated
        verdict = conflict_verdict(older, newer, dict(older), dict(older))
        self.assertFalse(verdict["passed"])
        self.assertIn("does not match the newer-mod copy", verdict["reason"])

    def test_equal_mods_are_indeterminate(self) -> None:
        a = _state(mod=1000, due=10)
        b = _state(mod=1000, due=20)
        verdict = conflict_verdict(a, b, dict(a), dict(a))
        self.assertFalse(verdict["passed"])
        self.assertIn("cannot pick a winner", verdict["reason"])


def _fake_report() -> dict:
    union = check_revlog_union(list(CONVERGED), list(CONVERGED), EXPECTED_A, EXPECTED_B)
    older = _state(mod=1000)
    newer = _state(mod=1002, due=99)
    return {
        "meta": {
            "generated_at": "2026-07-04T00:00:00+00:00",
            "port": 28711,
            "seed": 1,
            "invocation": "python sync_test.py",
        },
        "setup": {
            "cards": 40,
            "a_first_sync_required": 4,
            "b_first_sync_required": 3,
            "card_ids_match": True,
        },
        "offline": {
            "server_stopped": True,
            "a_reviews": [{"cid": c, "revlog_id": r, "ease": 3} for r, c in EXPECTED_A],
            "b_reviews": [{"cid": c, "revlog_id": r, "ease": 1} for r, c in EXPECTED_B],
        },
        "union_check": union,
        "full_revlog_identical": True,
        "conflict": {
            "cid": 300,
            "a_revlog_id": 5000,
            "a_ease": 3,
            "b_revlog_id": 5002,
            "b_ease": 1,
            "gap_seconds": 2,
            "both_entries_on_a": True,
            "both_entries_on_b": True,
            "verdict": conflict_verdict(older, newer, dict(newer), dict(newer)),
        },
        "honesty": ["offline means no sync calls"],
        "failures": [],
    }


class RenderMarkdownTests(unittest.TestCase):
    def test_counts_and_winner_render(self) -> None:
        text = render_markdown(_fake_report())
        self.assertIn("| A | 20 | 20 | 0 | 0 |", text)
        self.assertIn("| B | 20 | 20 | 0 | 0 |", text)
        self.assertIn("**PASS**", text)
        self.assertIn("winner: **B**", text)
        self.assertIn("append-only history", text)
        self.assertIn("Honesty notes", text)

    def test_failures_render(self) -> None:
        report = _fake_report()
        report["failures"] = ["union check failed: 1 lost"]
        text = render_markdown(report)
        self.assertIn("## FAILURES", text)
        self.assertIn("union check failed", text)


class CliSurfaceTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = sync_test.build_arg_parser().parse_args([])
        self.assertEqual(args.port, 28711)  # 28701 belongs to the bench agent
        self.assertEqual(args.cards, 40)

    def test_overrides(self) -> None:
        args = sync_test.build_arg_parser().parse_args(
            ["--port", "29000", "--cards", "25", "--seed", "9"]
        )
        self.assertEqual((args.port, args.cards, args.seed), (29000, 25, 9))


if __name__ == "__main__":
    unittest.main()
