# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the machine validation gates (aig/gates.py) + the mock LLM path."""

from __future__ import annotations

import copy
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig import gates, models  # noqa: E402
from aig import generators as G  # noqa: E402


def make_param_mcq() -> dict:
    gen = G.ModDurationFromMac()
    rng = random.Random("gate-fixture")
    return G.build_item(gen, "mcq", 0, rng)


def grounded(item: dict) -> dict:
    item = copy.deepcopy(item)
    item["source"] = {
        "doc": "duration.md",
        "loc": "#modified-duration",
        "passage": "Modified duration rescales Macaulay duration.",
    }
    return item


class SolveCheckTests(unittest.TestCase):
    def test_valid_param_mcq_passes(self) -> None:
        result = gates.gate_solve_check(make_param_mcq())
        self.assertTrue(result.passed, result.reason)

    def test_two_correct_answers_rejected(self) -> None:
        """The #1 AIG defect: two defensible choices must be rejected."""
        item = make_param_mcq()
        correct = item["correct"]
        wrong = next(l for l in "ABC" if l != correct)
        # Duplicate the correct numeric value onto a distractor letter.
        item["_aig"]["choice_values"][wrong] = item["_aig"]["choice_values"][correct]
        item["choices"][wrong] = item["choices"][correct]
        result = gates.gate_solve_check(item)
        self.assertFalse(result.passed)
        self.assertIn("exactly one defensible choice", result.reason)

    def test_mislabelled_answer_rejected(self) -> None:
        item = make_param_mcq()
        # Point `correct` at a distractor letter: the independent solver
        # must disagree with the label.
        wrong = next(l for l in "ABC" if l != item["correct"])
        item["correct"] = wrong
        result = gates.gate_solve_check(item)
        self.assertFalse(result.passed)

    def test_llm_item_without_solver_rejected(self) -> None:
        item = make_param_mcq()
        item["provenance"]["generator"] = "llm:mock"
        result = gates.gate_solve_check(item, llm_path=None)
        self.assertFalse(result.passed)


class LeakageWallTests(unittest.TestCase):
    REFERENCE = (
        "An analyst gathers the following information about a bond paying "
        "annual coupons of five percent with three years remaining"
    )

    def make_wall(self) -> gates.LeakageWall:
        wall = gates.LeakageWall(reference_pdf=None, corpus_texts={})
        wall.add_reference_text(self.REFERENCE)
        return wall

    def test_verbatim_stem_caught(self) -> None:
        wall = self.make_wall()
        item = make_param_mcq()
        # Splice >= 8 consecutive reference tokens into the stem.
        item["stem"] = (
            "Consider this: an analyst gathers the following information "
            "about a bond paying annual coupons. What is its duration?"
        )
        result = gates.gate_leakage(item, wall)
        self.assertFalse(result.passed)
        self.assertIn("overlap with reference", result.reason)

    def test_original_stem_passes(self) -> None:
        wall = self.make_wall()
        result = gates.gate_leakage(make_param_mcq(), wall)
        self.assertTrue(result.passed, result.reason)

    def test_corpus_overlap_caught(self) -> None:
        wall = gates.LeakageWall(
            reference_pdf=None,
            corpus_texts={
                "duration.md#x": (
                    "Macaulay duration is the weighted-average time to "
                    "receipt of a bond's cash flows where each weight is"
                )
            },
        )
        item = make_param_mcq()
        item["stem"] = (
            "Recall that Macaulay duration is the weighted-average time to "
            "receipt of a bond's cash flows. Compute it for this bond."
        )
        result = gates.gate_leakage(item, wall)
        self.assertFalse(result.passed)
        self.assertIn("overlap with corpus", result.reason)

    def test_seven_gram_overlap_allowed(self) -> None:
        wall = self.make_wall()
        item = make_param_mcq()
        # Exactly 7 shared tokens - below the 8-gram wall.
        item["stem"] = (
            "An analyst gathers the following information about equity "
            "index futures margins."
        )
        result = gates.gate_leakage(item, wall)
        self.assertTrue(result.passed, result.reason)


class SchemaGateTests(unittest.TestCase):
    def test_good_item_passes(self) -> None:
        item = grounded(make_param_mcq())
        result = gates.gate_schema(item)
        self.assertTrue(result.passed, result.reason)

    def test_missing_rationale_rejected(self) -> None:
        item = grounded(make_param_mcq())
        item["rationale"] = "  "
        self.assertFalse(gates.gate_schema(item).passed)
        self.assertFalse(gates.gate_rationale(item).passed)

    def test_empty_source_rejected(self) -> None:
        item = make_param_mcq()  # pre-grounding: source == {}
        result = gates.gate_schema(item)
        self.assertFalse(result.passed)
        self.assertIn("source", result.reason)

    def test_wrong_choice_keys_rejected(self) -> None:
        item = grounded(make_param_mcq())
        item["choices"] = {"A": "1", "B": "2", "D": "3"}
        self.assertFalse(gates.gate_schema(item).passed)

    def test_internal_validator_used_when_external_absent(self) -> None:
        """gate_schema must work when ladder_schema.py does not exist."""
        import aig.gates as gates_mod

        original = gates_mod._external_validator
        gates_mod._external_validator = lambda: None
        try:
            good = gates.gate_schema(grounded(make_param_mcq()))
            self.assertTrue(good.passed, good.reason)
            bad_item = grounded(make_param_mcq())
            del bad_item["rationale"]
            self.assertFalse(gates.gate_schema(bad_item).passed)
        finally:
            gates_mod._external_validator = original

    def test_external_rejection_respected_when_present(self) -> None:
        """A clean rejection from a sibling ladder_schema blocks the item."""
        import aig.gates as gates_mod

        original = gates_mod._external_validator
        gates_mod._external_validator = lambda: (lambda item: ["external says no"])
        try:
            result = gates.gate_schema(grounded(make_param_mcq()))
            self.assertFalse(result.passed)
            self.assertIn("external says no", result.reason)
        finally:
            gates_mod._external_validator = original

    def test_broken_external_does_not_block(self) -> None:
        """An uncallable/broken ladder_schema falls back to internal checks."""
        import aig.gates as gates_mod

        def boom(item):  # noqa: ANN001
            raise TypeError("half-written API")

        original = gates_mod._external_validator
        gates_mod._external_validator = lambda: boom
        try:
            result = gates.gate_schema(grounded(make_param_mcq()))
            self.assertTrue(result.passed, result.reason)
            self.assertIn("uncallable", result.reason)
        finally:
            gates_mod._external_validator = original


class RationaleGateTests(unittest.TestCase):
    def test_missing_distractor_rationale_rejected(self) -> None:
        item = make_param_mcq()
        wrong = sorted(set("ABC") - {item["correct"]})[0]
        del item["distractor_rationales"][wrong]
        result = gates.gate_rationale(item)
        self.assertFalse(result.passed)
        self.assertIn(wrong, result.reason)

    def test_unknown_misconception_id_rejected(self) -> None:
        item = make_param_mcq()
        wrong = sorted(set("ABC") - {item["correct"]})[0]
        item["misconceptions"][wrong] = "duration.not_a_real_id"
        self.assertFalse(gates.gate_rationale(item).passed)


class NumericGateTests(unittest.TestCase):
    def test_agreeing_item_passes(self) -> None:
        self.assertTrue(gates.gate_numeric(make_param_mcq()).passed)

    def test_disagreeing_recomputation_rejected(self) -> None:
        item = make_param_mcq()
        item["_aig"]["answer_check"] = item["_aig"]["answer"] * 1.01
        result = gates.gate_numeric(item)
        self.assertFalse(result.passed)
        self.assertIn("disagrees", result.reason)

    def test_margin_violation_rejected(self) -> None:
        item = make_param_mcq()
        cv = item["_aig"]["choice_values"]
        wrong = sorted(set("ABC") - {item["correct"]})
        cv[wrong[0]] = cv[wrong[1]] * 1.0001  # 0.01% apart
        result = gates.gate_numeric(item)
        self.assertFalse(result.passed)
        self.assertIn("margin", result.reason)


class MockLlmPathTests(unittest.TestCase):
    def test_mock_path_accepts_consistent_draft(self) -> None:
        path = models.make_llm_path("mock")
        draft, audit = path.generate_validated(
            "fixed_income",
            "fi::duration",
            "modified duration",
            ["duration.modified_vs_macaulay"],
        )
        self.assertIsNotNone(draft)
        self.assertTrue(audit["consensus"])
        self.assertTrue(audit["critic_accept"])
        self.assertEqual(len(audit["solver_picks"]), 3)

    def test_split_solver_consensus_rejects(self) -> None:
        path = models.make_llm_path("mock")
        path.solver = models.MockBackend(role="solver", failure_mode="split_solver")
        draft, audit = path.generate_validated("fixed_income", "fi::duration", "x", [])
        self.assertIsNone(draft)
        self.assertFalse(audit["consensus"])

    def test_adversarial_critic_rejects(self) -> None:
        path = models.make_llm_path("mock")
        path.critic = models.MockBackend(role="critic", failure_mode="reject_critic")
        draft, audit = path.generate_validated("fixed_income", "fi::duration", "x", [])
        self.assertIsNone(draft)
        self.assertFalse(audit["critic_accept"])

    def test_same_family_warning_recorded(self) -> None:
        path = models.make_llm_path("mock")
        _, audit = path.generate_validated("fixed_income", "fi::duration", "x", [])
        self.assertIn("[R23]", audit["same_family_warning"])

    def test_parse_json_reply_tolerates_fences(self) -> None:
        self.assertEqual(
            models.parse_json_reply('```json\n{"answer": "A"}\n```'),
            {"answer": "A"},
        )
        self.assertEqual(
            models.parse_json_reply('noise {"verdict": "accept", "reasons": []} tail'),
            {"verdict": "accept", "reasons": []},
        )
        self.assertIsNone(models.parse_json_reply("no json here"))


if __name__ == "__main__":
    unittest.main()
