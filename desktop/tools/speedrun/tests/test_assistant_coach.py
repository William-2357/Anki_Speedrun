# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Feature B tests: the coach is grounded, prioritisation-only, and defers
to the Readiness gauge (no pass probability while it abstains)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import coach, core  # noqa: E402

ABSTAINING_FACTS: dict[str, Any] = {
    "exam": "CFA Level I",
    "days_to_exam": 34,
    "graded_reviews": 42,
    "coverage": 0.42,
    "deck_coverage": 0.55,
    "best_next": "Derivatives",
    "memory": {"kind": "value", "value": 0.71},
    "performance": {"kind": "value", "value": 0.53},
    "readiness": {
        "kind": "abstain",
        "missing": ["Only 42 graded reviews; need at least 300."],
    },
    "subjects": [
        {
            "name": "Derivatives",
            "memory": 0.55,
            "performance": 0.33,
            "studied": 12,
            "total": 80,
            "weight_pct": 6,
            "weighted_gap": 0.031,
        },
        {
            "name": "Ethics",
            "memory": 0.91,
            "performance": 0.82,
            "studied": 40,
            "total": 60,
            "weight_pct": 15,
            "weighted_gap": 0.005,
        },
    ],
}

VALUE_FACTS: dict[str, Any] = {
    **ABSTAINING_FACTS,
    "readiness": {"kind": "value", "value": 0.62, "low": 0.4, "high": 0.8},
}


class CannedBackend:
    """Returns a fixed JSON reply; used to drive the reject hook directly."""

    name = "canned"

    def __init__(self, reply: dict[str, Any]):
        self.reply = reply

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        return json.dumps(self.reply)


def run_coach(facts: dict[str, Any], backend: Any) -> tuple[Any, dict[str, Any]]:
    diagnostics: dict[str, Any] = {}
    plan = coach.coach_plan(facts, backend, diagnostics=diagnostics)
    return plan, diagnostics


class MockBackendTests(unittest.TestCase):
    def test_abstaining_readiness_yields_plan_without_pass_claim(self) -> None:
        first, diagnostics = run_coach(ABSTAINING_FACTS, core.MockAssistantBackend())
        second, _ = run_coach(ABSTAINING_FACTS, core.MockAssistantBackend())
        self.assertIsNotNone(first)
        self.assertEqual(first, second)  # deterministic
        self.assertEqual(diagnostics["outcome"], "ok")
        self.assertIn("Derivatives", first["summary"])
        for text in core._walk_strings(first):
            self.assertIsNone(coach._PASS_CLAIM_RE.search(text), text)

    def test_readiness_value_accepted(self) -> None:
        plan, diagnostics = run_coach(VALUE_FACTS, core.MockAssistantBackend())
        self.assertIsNotNone(plan)
        self.assertEqual(diagnostics["outcome"], "ok")


class RejectHookTests(unittest.TestCase):
    NUMBERLESS_PASS_CLAIM = {
        "summary": "Relax, you will probably pass.",
        "priorities": [{"topic": "Derivatives", "why": "largest weighted gap"}],
    }

    def test_numberless_pass_claim_rejected_while_abstaining(self) -> None:
        plan, diagnostics = run_coach(
            ABSTAINING_FACTS, CannedBackend(self.NUMBERLESS_PASS_CLAIM)
        )
        self.assertIsNone(plan)
        self.assertIn("pass probability", diagnostics["reason"])

    def test_same_claim_allowed_when_gauge_shows_a_value(self) -> None:
        plan, _ = run_coach(VALUE_FACTS, CannedBackend(self.NUMBERLESS_PASS_CLAIM))
        self.assertIsNotNone(plan)

    def test_pass_claim_variants_all_rejected(self) -> None:
        for wording in (
            "Your P(pass) looks fine.",
            "Your pass probability is improving.",
            "The probability of passing is high.",
            "There is a good chance that you pass.",
            "You are likely to pass if you keep going.",
            "The odds you pass improve daily.",
        ):
            reply = {
                "summary": wording,
                "priorities": [{"topic": "Ethics", "why": "grounded"}],
            }
            plan, diagnostics = run_coach(ABSTAINING_FACTS, CannedBackend(reply))
            self.assertIsNone(plan, wording)
            self.assertIn("pass probability", diagnostics["reason"], wording)

    def test_echoing_abstention_reasons_is_allowed(self) -> None:
        reply = {
            "summary": (
                "Readiness is abstaining (Only 42 graded reviews; need at "
                "least 300.), so focus on Derivatives today."
            ),
            "priorities": [{"topic": "Derivatives", "why": "largest weighted gap"}],
        }
        plan, diagnostics = run_coach(ABSTAINING_FACTS, CannedBackend(reply))
        self.assertIsNotNone(plan)
        self.assertEqual(diagnostics["outcome"], "ok")

    def test_invented_topic_rejected(self) -> None:
        reply = {
            "summary": "Study the stars.",
            "priorities": [{"topic": "Astrology", "why": "vibes"}],
        }
        plan, diagnostics = run_coach(ABSTAINING_FACTS, CannedBackend(reply))
        self.assertIsNone(plan)
        self.assertIn("absent from the facts", diagnostics["reason"])

    def test_malformed_priority_rejected(self) -> None:
        reply = {"summary": "Do things.", "priorities": [{"why": "no topic"}]}
        plan, diagnostics = run_coach(ABSTAINING_FACTS, CannedBackend(reply))
        self.assertIsNone(plan)
        self.assertIn("non-empty topic", diagnostics["reason"])

    def test_best_next_counts_as_a_known_topic(self) -> None:
        facts = {**ABSTAINING_FACTS, "subjects": []}
        reply = {
            "summary": "Start with Derivatives.",
            "priorities": [{"topic": "derivatives", "why": "named best next"}],
        }
        plan, _ = run_coach(facts, CannedBackend(reply))
        self.assertIsNotNone(plan)


class GroundingAndFailureTests(unittest.TestCase):
    def test_invented_number_abstains(self) -> None:
        plan, diagnostics = run_coach(
            ABSTAINING_FACTS, core.MockAssistantBackend(failure_mode="invent_number")
        )
        self.assertIsNone(plan)
        self.assertIn("ungrounded numbers", diagnostics["reason"])

    def test_backend_failures_abstain(self) -> None:
        for mode, fragment in (
            ("raise", "backend error"),
            ("garbage", "not a JSON object"),
            ("low_confidence", "low confidence"),
            ("abstain", "model abstained"),
        ):
            plan, diagnostics = run_coach(
                ABSTAINING_FACTS, core.MockAssistantBackend(failure_mode=mode)
            )
            self.assertIsNone(plan, mode)
            self.assertIn(fragment, diagnostics["reason"], mode)


class TheWallTests(unittest.TestCase):
    def test_module_never_touches_pylib(self) -> None:
        # the coach operates on plain dicts; it must not import the
        # collection layer (pylib "anki"/"aqt"), let alone write through it.
        # (Source scan rather than sys.modules: sibling test files stub
        # "anki" into sys.modules, so presence there proves nothing.)
        import inspect

        source = inspect.getsource(coach)
        self.assertNotIn("import anki", source)
        self.assertNotIn("from anki", source)
        self.assertNotIn("import aqt", source)


if __name__ == "__main__":
    unittest.main()
