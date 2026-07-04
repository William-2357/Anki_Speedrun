# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""C1 tests: the tag->topic suggester pre-fills only what survives validation.

The invariant under test: ``suggest_mappings`` returns plain data for the
Map-tags editor to PRE-FILL dropdowns with - it never persists anything,
never forces a low-confidence pick, and preserves abstention ("unsure")
end to end by dropping it rather than mapping it.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import core, tag_suggest  # noqa: E402

TOPICS = ["fixed_income", "equity", "ethics"]


def _tag(tag: str, cards: int = 3, fronts: list[str] | None = None) -> dict[str, Any]:
    return {
        "tag": tag,
        "cards": cards,
        "sample_fronts": ["What is duration?"] if fronts is None else fronts,
    }


class CannedBackend:
    """Answers every completion with one fixed JSON reply; records prompts."""

    name = "canned"

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.reply)


class MustNotCallBackend:
    """Proves the no-input fast path never reaches the backend."""

    name = "must-not-call"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        self.calls += 1
        raise AssertionError("suggest_mappings must not call the backend")


def _canned_run(
    suggestions: Any,
    tags: list[dict[str, Any]],
    topics: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Run suggest_mappings against a stub reply {"suggestions": ...}."""
    diagnostics: dict[str, Any] = {}
    result = tag_suggest.suggest_mappings(
        tags,
        TOPICS if topics is None else topics,
        CannedBackend({"suggestions": suggestions}),
        diagnostics=diagnostics,
    )
    return result, diagnostics


class EmptyInputTests(unittest.TestCase):
    def test_empty_tags_skip_backend(self) -> None:
        backend = MustNotCallBackend()
        diagnostics: dict[str, Any] = {}
        result = tag_suggest.suggest_mappings(
            [], TOPICS, backend, diagnostics=diagnostics
        )
        self.assertEqual(result, {})
        self.assertEqual(backend.calls, 0)
        self.assertEqual(diagnostics["reason"], "nothing to classify")

    def test_empty_topics_skip_backend(self) -> None:
        backend = MustNotCallBackend()
        diagnostics: dict[str, Any] = {}
        result = tag_suggest.suggest_mappings(
            [_tag("fixed_income_readings")], [], backend, diagnostics=diagnostics
        )
        self.assertEqual(result, {})
        self.assertEqual(backend.calls, 0)
        self.assertEqual(diagnostics["reason"], "nothing to classify")

    def test_empty_inputs_work_without_diagnostics(self) -> None:
        result = tag_suggest.suggest_mappings([], TOPICS, MustNotCallBackend())
        self.assertEqual(result, {})


class MockBackendTests(unittest.TestCase):
    """End-to-end through core.grounded_complete with the offline mock."""

    def _run(self) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        diagnostics: dict[str, Any] = {}
        result = tag_suggest.suggest_mappings(
            [
                _tag("fixed_income_readings", cards=12),
                _tag("zz9", cards=1, fronts=[]),
            ],
            TOPICS,
            core.MockAssistantBackend(),
            diagnostics=diagnostics,
        )
        return result, diagnostics

    def test_keyword_tag_kept_opaque_tag_dropped(self) -> None:
        # The mock keyword-matches "fixed_income_readings" onto the
        # "fixed_income" topic id (0.9) and answers "unsure" (0.2) for
        # "zz9"; only the former may pre-fill a dropdown.
        result, diagnostics = self._run()
        self.assertEqual(
            result,
            {"fixed_income_readings": {"topic": "fixed_income", "confidence": 0.9}},
        )
        self.assertEqual(diagnostics["outcome"], "ok")
        self.assertEqual(diagnostics["kept"], 1)
        self.assertEqual(diagnostics["dropped"], 1)

    def test_deterministic_across_two_runs(self) -> None:
        first, _ = self._run()
        second, _ = self._run()
        self.assertEqual(first, second)


class PromptContractTests(unittest.TestCase):
    def test_single_call_with_task_and_fact_passthrough(self) -> None:
        tags = [_tag("duration_notes"), _tag("leech", cards=1, fronts=[])]
        backend = CannedBackend(
            {
                "suggestions": {
                    "duration_notes": {"topic": "fixed_income", "confidence": 0.8},
                    "leech": {"topic": "ignore", "confidence": 0.9},
                }
            }
        )
        result = tag_suggest.suggest_mappings(tags, TOPICS, backend)
        self.assertEqual(len(backend.prompts), 1)
        prompt = backend.prompts[0]
        self.assertEqual(core._prompt_task(prompt), "tag_suggest")
        facts = core._prompt_facts(prompt)
        # Tag names, card counts and sample fronts are app-computed facts,
        # handed through unchanged; topics arrive as the canonical id list.
        self.assertEqual(facts["tags"], tags)
        self.assertEqual(facts["topics"], TOPICS)
        self.assertEqual(len(result), 2)


class ConfidenceFloorTests(unittest.TestCase):
    def _one(self, confidence: Any) -> dict[str, dict[str, Any]]:
        result, _ = _canned_run(
            {"duration_notes": {"topic": "fixed_income", "confidence": confidence}},
            [_tag("duration_notes")],
        )
        return result

    def test_floor_constant_unchanged(self) -> None:
        self.assertEqual(tag_suggest.CONFIDENCE_FLOOR, 0.6)

    def test_just_below_floor_dropped(self) -> None:
        self.assertEqual(self._one(0.59), {})

    def test_at_floor_kept(self) -> None:
        self.assertEqual(
            self._one(0.6),
            {"duration_notes": {"topic": "fixed_income", "confidence": 0.6}},
        )

    def test_integer_confidence_normalised_to_float(self) -> None:
        result = self._one(1)
        self.assertEqual(
            result, {"duration_notes": {"topic": "fixed_income", "confidence": 1.0}}
        )
        self.assertIsInstance(result["duration_notes"]["confidence"], float)


class TopicValidationTests(unittest.TestCase):
    def _one(self, topic: Any, confidence: float = 0.95) -> dict[str, dict[str, Any]]:
        result, _ = _canned_run(
            {"duration_notes": {"topic": topic, "confidence": confidence}},
            [_tag("duration_notes")],
        )
        return result

    def test_invented_topic_dropped(self) -> None:
        self.assertEqual(self._one("astrology"), {})

    def test_ignore_kept(self) -> None:
        self.assertEqual(
            self._one("ignore"),
            {"duration_notes": {"topic": "ignore", "confidence": 0.95}},
        )

    def test_unsure_dropped_even_at_high_confidence(self) -> None:
        # Abstention is preserved end to end: "unsure" never pre-fills a
        # dropdown, no matter how confidently the model says it.
        self.assertEqual(self._one("unsure", confidence=0.99), {})


class ReplyHygieneTests(unittest.TestCase):
    def test_unknown_tag_key_dropped(self) -> None:
        result, diagnostics = _canned_run(
            {"not_an_input_tag": {"topic": "equity", "confidence": 0.9}},
            [_tag("duration_notes")],
        )
        self.assertEqual(result, {})
        self.assertEqual(diagnostics["kept"], 0)
        self.assertEqual(diagnostics["dropped"], 1)

    def test_malformed_entries_dropped_valid_remainder_kept(self) -> None:
        suggestions = {
            "keepme": {"topic": "equity", "confidence": 0.8},
            "topic_not_string": {"topic": 3, "confidence": 0.9},
            "topic_is_none": {"topic": None, "confidence": 0.9},
            "conf_missing": {"topic": "equity"},
            "conf_none": {"topic": "equity", "confidence": None},
            "conf_string": {"topic": "equity", "confidence": "high"},
            "conf_bool": {"topic": "equity", "confidence": True},
            "conf_too_high": {"topic": "equity", "confidence": 1.5},
            "conf_negative": {"topic": "equity", "confidence": -0.2},
            "value_not_dict": "equity",
            "value_is_list": [{"topic": "equity", "confidence": 0.9}],
        }
        tags = [_tag(name) for name in suggestions]
        result, diagnostics = _canned_run(suggestions, tags)
        self.assertEqual(result, {"keepme": {"topic": "equity", "confidence": 0.8}})
        self.assertEqual(diagnostics["kept"], 1)
        self.assertEqual(diagnostics["dropped"], len(suggestions) - 1)

    def test_suggestions_not_a_mapping_abstains_to_empty(self) -> None:
        result, diagnostics = _canned_run(
            [{"topic": "equity", "confidence": 0.9}], [_tag("duration_notes")]
        )
        self.assertEqual(result, {})
        self.assertIn("schema", diagnostics["reason"])

    def test_missing_suggestions_field_abstains_to_empty(self) -> None:
        diagnostics: dict[str, Any] = {}
        result = tag_suggest.suggest_mappings(
            [_tag("duration_notes")],
            TOPICS,
            CannedBackend({"mappings": {}}),
            diagnostics=diagnostics,
        )
        self.assertEqual(result, {})
        self.assertIn("schema", diagnostics["reason"])


class FailureModeTests(unittest.TestCase):
    """Backend failures abstain to {} - the editor simply stays blank."""

    def _run(self, failure_mode: str) -> tuple[dict, dict[str, Any]]:
        diagnostics: dict[str, Any] = {}
        result = tag_suggest.suggest_mappings(
            [_tag("fixed_income_readings")],
            TOPICS,
            core.MockAssistantBackend(failure_mode=failure_mode),
            diagnostics=diagnostics,
        )
        return result, diagnostics

    def test_garbage_reply(self) -> None:
        result, diagnostics = self._run("garbage")
        self.assertEqual(result, {})
        self.assertIn("not a JSON object", diagnostics["reason"])
        self.assertNotIn("kept", diagnostics)

    def test_backend_outage(self) -> None:
        result, diagnostics = self._run("raise")
        self.assertEqual(result, {})
        self.assertEqual(diagnostics["reason"], "backend error or timeout")
        self.assertNotIn("kept", diagnostics)

    def test_explicit_model_abstention(self) -> None:
        result, diagnostics = self._run("abstain")
        self.assertEqual(result, {})
        self.assertIn("model abstained", diagnostics["reason"])
        self.assertNotIn("kept", diagnostics)


class PurityTests(unittest.TestCase):
    """Pre-fill only: plain data out, no persistence machinery imported."""

    def test_no_pylib_or_gui_imports(self) -> None:
        import inspect

        for name, value in vars(tag_suggest).items():
            module = inspect.getmodule(value)
            if module is None or module is tag_suggest:
                continue
            root = module.__name__.partition(".")[0]
            self.assertNotIn(
                root,
                {"anki", "aqt"},
                f"tag_suggest.{name} pulls in {module.__name__}, "
                "which could open a write path",
            )

    def test_returns_plain_data(self) -> None:
        result = tag_suggest.suggest_mappings(
            [_tag("fixed_income_readings")], TOPICS, core.MockAssistantBackend()
        )
        self.assertIsInstance(result, dict)
        for value in result.values():
            self.assertIsInstance(value, dict)
            self.assertIsInstance(value["topic"], str)
            self.assertIsInstance(value["confidence"], float)


if __name__ == "__main__":
    unittest.main()
