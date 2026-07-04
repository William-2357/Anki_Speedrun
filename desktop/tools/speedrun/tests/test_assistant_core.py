# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""S1 tests: the assistant backend adapter's grounded-or-abstain semantics."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import core  # noqa: E402

FACTS = {
    "days_to_exam": 34,
    "coverage": 0.42,
    "best_next": "Derivatives",
    "subjects": [
        {"name": "Derivatives", "memory": 0.55, "weighted_gap": 0.031},
        {"name": "Ethics", "memory": 0.91, "weighted_gap": 0.005},
    ],
    "note": "Only 42 graded reviews; need at least 300.",
}

SCHEMA = {"text": "str"}


class MakeBackendTests(unittest.TestCase):
    def test_defaults_to_mock(self) -> None:
        self.assertEqual(core.make_backend().name, "mock")

    def test_env_var_fallback(self) -> None:
        import os

        os.environ["SPEEDRUN_AI_BACKEND"] = "claude-cli"
        try:
            self.assertEqual(core.make_backend().name, "claude-cli")
            # explicit name wins over the env var
            self.assertEqual(core.make_backend("mock").name, "mock")
        finally:
            del os.environ["SPEEDRUN_AI_BACKEND"]

    def test_known_backends(self) -> None:
        self.assertEqual(core.make_backend("claude-cli").name, "claude-cli")
        self.assertEqual(
            core.make_backend("openai-compatible").name, "openai-compatible"
        )
        with self.assertRaises(ValueError):
            core.make_backend("skynet")


class GroundingTests(unittest.TestCase):
    def test_fact_numbers_and_percent_forms_allowed(self) -> None:
        allowed = core.allowed_numbers(FACTS)
        self.assertIn(34.0, allowed)
        self.assertIn(0.42, allowed)
        self.assertIn(42.0, allowed)  # percent form of 0.42 AND the string "42"
        self.assertIn(300.0, allowed)  # from the note string
        self.assertIn(0.031, allowed)

    def test_ungrounded_numbers_flagged(self) -> None:
        reply = {"text": "You are 87.31% ready and will score 1330."}
        bad = core.ungrounded_numbers(reply, FACTS)
        self.assertIn(87.31, bad)
        self.assertIn(1330.0, bad)

    def test_small_ints_do_not_force_abstention(self) -> None:
        reply = {"text": "Do these 3 things across the next 2 days."}
        self.assertEqual(core.ungrounded_numbers(reply, FACTS), [])

    def test_grounded_reply_passes(self) -> None:
        reply = {"text": "34 days out with 42% coverage: start Derivatives."}
        self.assertEqual(core.ungrounded_numbers(reply, FACTS), [])


class SchemaTests(unittest.TestCase):
    def test_required_and_optional(self) -> None:
        schema = {"summary": "str", "priorities": "list[dict]", "note": "str?"}
        self.assertEqual(
            core.schema_errors({"summary": "x", "priorities": []}, schema), []
        )
        errors = core.schema_errors({"summary": ""}, schema)
        self.assertEqual(len(errors), 2)  # empty summary + missing priorities

    def test_type_mismatch(self) -> None:
        self.assertTrue(core.schema_errors({"text": 7}, {"text": "str"}))


class GroundedCompleteTests(unittest.TestCase):
    def _run(self, failure_mode: str = "", **kwargs):
        diagnostics: dict = {}
        reply = core.grounded_complete(
            "You are a test narrator.",
            FACTS,
            schema=SCHEMA,
            backend=core.MockAssistantBackend(failure_mode=failure_mode),
            task="generic",
            diagnostics=diagnostics,
            **kwargs,
        )
        return reply, diagnostics

    def test_mock_reply_is_deterministic_and_grounded(self) -> None:
        first, diagnostics = self._run()
        second, _ = self._run()
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(diagnostics["outcome"], "ok")

    def test_unparseable_reply_abstains(self) -> None:
        reply, diagnostics = self._run("garbage")
        self.assertIsNone(reply)
        self.assertIn("not a JSON object", diagnostics["reason"])

    def test_backend_error_abstains(self) -> None:
        reply, diagnostics = self._run("raise")
        self.assertIsNone(reply)
        self.assertEqual(diagnostics["reason"], "backend error or timeout")

    def test_explicit_model_abstention_respected(self) -> None:
        reply, diagnostics = self._run("abstain")
        self.assertIsNone(reply)
        self.assertIn("model abstained", diagnostics["reason"])

    def test_low_confidence_abstains(self) -> None:
        reply, diagnostics = self._run("low_confidence")
        self.assertIsNone(reply)
        self.assertIn("low confidence", diagnostics["reason"])

    def test_ungrounded_number_abstains(self) -> None:
        # invent_number returns fields for the coach/debrief schemas; use a
        # schema its reply satisfies so the GROUNDING check is what fires.
        diagnostics: dict = {}
        reply = core.grounded_complete(
            "You are a test narrator.",
            FACTS,
            schema={"summary": "str"},
            backend=core.MockAssistantBackend(failure_mode="invent_number"),
            task="generic",
            diagnostics=diagnostics,
        )
        self.assertIsNone(reply)
        self.assertIn("ungrounded numbers", diagnostics["reason"])

    def test_schema_failure_abstains(self) -> None:
        diagnostics: dict = {}
        reply = core.grounded_complete(
            "You are a test narrator.",
            FACTS,
            schema={"nonexistent_field": "str"},
            backend=core.MockAssistantBackend(),
            task="generic",
            diagnostics=diagnostics,
        )
        self.assertIsNone(reply)
        self.assertIn("schema", diagnostics["reason"])

    def test_reject_hook_abstains(self) -> None:
        reply, diagnostics = self._run(reject=lambda r: "house rule violated")
        self.assertIsNone(reply)
        self.assertEqual(diagnostics["reason"], "house rule violated")

    def test_hard_timeout(self) -> None:
        class Sleeper:
            name = "sleeper"

            def complete(self, prompt: str, *, sample_index: int = 0) -> str:
                import time

                time.sleep(5)
                return "{}"

        diagnostics: dict = {}
        reply = core.grounded_complete(
            "You are a test narrator.",
            FACTS,
            schema=SCHEMA,
            backend=Sleeper(),
            timeout=0.2,
            diagnostics=diagnostics,
        )
        self.assertIsNone(reply)
        self.assertEqual(diagnostics["reason"], "backend error or timeout")


class MockTaskRoutingTests(unittest.TestCase):
    """The mock answers each assistant task with its canned generator."""

    def test_debrief_reply_shape(self) -> None:
        facts = {
            "topics_missed": [{"topic": "fixed_income", "lapses": 4}],
            "confusable_pairs": [{"pair": ["fi::duration", "fi::convexity"]}],
            "best_next": "Review fixed_income",
        }
        reply = core.grounded_complete(
            "Narrate.",
            facts,
            schema={"narrative": "str", "next_step": "str"},
            backend=core.MockAssistantBackend(),
            task="debrief",
        )
        self.assertIsNotNone(reply)
        self.assertIn("fixed_income", reply["narrative"])

    def test_prompt_facts_roundtrip(self) -> None:
        prompt = core.build_prompt("sys", FACTS, SCHEMA, "generic")
        self.assertEqual(core._prompt_facts(prompt)["days_to_exam"], 34)
        self.assertEqual(core._prompt_task(prompt), "generic")


if __name__ == "__main__":
    unittest.main()
