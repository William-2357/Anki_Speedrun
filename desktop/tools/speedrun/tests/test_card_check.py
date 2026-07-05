# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for card_check.py: checker units, frozen-cutoff pinning, and
gold-set file integrity (incl. re-deriving every quantitative answer)."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import card_check as cc  # noqa: E402
from aig import generators as G  # noqa: E402

GOLD_PATH = Path(__file__).resolve().parents[1] / "gold" / "gold_set_v1.jsonl"


def empty_checker() -> cc.Checker:
    return cc.Checker([], corpus_texts={})


def gold_checker() -> cc.Checker:
    return cc.Checker(cc.load_gold(GOLD_PATH), corpus_texts={})


def human_card(cid: str, q: str, a: str) -> dict:
    return {"id": cid, "question": q, "correct_answer": a, "origin": "human"}


class RecomputationTests(unittest.TestCase):
    def make_worked(self) -> dict:
        rng = random.Random("cardcheck-fixture")
        return G.build_item(G.TvmFvLump(), "worked", 0, rng)

    def test_clean_param_item_verified_and_ships(self) -> None:
        result = empty_checker().check_card(self.make_worked())
        self.assertEqual(result.label, "correct_useful")
        self.assertEqual(result.correctness, "verified")
        self.assertTrue(result.shipped, result.reasons)

    def test_recomputation_catches_seeded_wrong_answer(self) -> None:
        item = self.make_worked()
        item["_aig"]["answer"] *= 1.5  # seed a wrong stated answer
        result = empty_checker().check_card(item)
        self.assertEqual(result.label, "wrong")
        self.assertFalse(result.shipped)
        self.assertTrue(
            any("recomputation" in r for r in result.reasons), result.reasons
        )

    def test_solve_check_catches_mislabelled_mcq(self) -> None:
        rng = random.Random("cardcheck-fixture-mcq")
        item = G.build_item(G.ModDurationFromMac(), "mcq", 0, rng)
        item["correct"] = next(letter for letter in "ABC" if letter != item["correct"])
        result = empty_checker().check_card(item)
        self.assertEqual(result.label, "wrong")
        self.assertFalse(result.shipped)


class DuplicateTests(unittest.TestCase):
    Q = (
        "A portfolio returned 10% with a standard deviation of 16% while "
        "the risk-free rate was 2%. What is its Sharpe ratio?"
    )

    def test_near_copy_flagged_at_frozen_threshold(self) -> None:
        near = (
            "A portfolio returns 10% with a 16% standard deviation while "
            "the risk-free rate is 2%. What is the portfolio's Sharpe ratio?"
        )
        batch = empty_checker().check_batch(
            [human_card("a", self.Q, "0.50"), human_card("b", near, "0.50")]
        )
        self.assertEqual(batch.results[0].label, "correct_useful")
        self.assertEqual(batch.results[1].label, "bad_teaching")
        self.assertEqual(batch.results[1].dup_of, "a")
        self.assertTrue(any("duplicate" in r for r in batch.results[1].reasons))

    def test_distinct_pair_passes(self) -> None:
        other = (
            "The risk-free rate is 2% and the expected market return is "
            "8%. Using the CAPM, what is the required return on a stock "
            "with a beta of 1.2?"
        )
        batch = empty_checker().check_batch(
            [human_card("a", self.Q, "0.50"), human_card("b", other, "9.2%")]
        )
        self.assertTrue(all(r.label == "correct_useful" for r in batch.results))

    def test_near_copy_of_gold_record_flagged(self) -> None:
        checker = gold_checker()
        result = checker.check_card(
            human_card(
                "x",
                "A portfolio returns 10% with a 16% standard deviation "
                "while the risk-free rate is 2%. What is the portfolio's "
                "Sharpe ratio?",
                "0.50",
            )
        )
        self.assertEqual(result.label, "bad_teaching")
        self.assertEqual(result.dup_of, "gold::pm::02")


class VaguenessTests(unittest.TestCase):
    def check(self, q: str, a: str) -> cc.CardResult:
        return empty_checker().check_card(human_card("v", q, a))

    def test_short_question_is_vague(self) -> None:
        result = self.check("What is finance?", "The study of money.")
        self.assertEqual(result.label, "bad_teaching")
        self.assertTrue(any("vague" in r for r in result.reasons))

    def test_no_question_mark_no_task_cue_is_vague(self) -> None:
        result = self.check(
            "Bonds, equities, and derivatives in modern markets.",
            "Securities exist.",
        )
        self.assertTrue(
            any("no question mark and no task cue" in r for r in result.reasons),
            result.reasons,
        )

    def test_non_answer_is_vague(self) -> None:
        result = self.check(
            "What are the main things an investor should generally think "
            "about before making any kind of investment decision?",
            "It depends on many factors.",
        )
        self.assertEqual(result.label, "bad_teaching")
        self.assertTrue(any("non-answer" in r for r in result.reasons))

    def test_answer_leaked_in_question_is_trivial(self) -> None:
        result = self.check(
            "The quick ratio excludes inventory from current assets. "
            "What does the quick ratio exclude from current assets?",
            "Inventory.",
        )
        self.assertEqual(result.label, "bad_teaching")
        self.assertTrue(any("trivial" in r for r in result.reasons))

    def test_specific_card_is_clean(self) -> None:
        result = self.check(
            "A stock is bought for $40.00, pays a $1.00 dividend during "
            "the year, and is sold at year-end for $43.00. What is the "
            "holding-period return?",
            "10%",
        )
        self.assertEqual(result.label, "correct_useful")
        self.assertTrue(result.shipped)


class PriorityAndPolicyTests(unittest.TestCase):
    def test_wrong_beats_bad_teaching_when_both_fire(self) -> None:
        # Verbatim gold question (dup fires) with a different numeric
        # answer (gold contradiction fires): wrong must win.
        checker = gold_checker()
        result = checker.check_card(
            human_card(
                "both",
                "A portfolio returned 10% with a standard deviation of 16% "
                "while the risk-free rate was 2%. What is its Sharpe ratio?",
                "0.75",
            )
        )
        self.assertEqual(result.label, "wrong")
        self.assertTrue(any("contradicts gold" in r for r in result.reasons))
        self.assertTrue(any("duplicate" in r for r in result.reasons))
        self.assertFalse(result.shipped)

    def test_corpus_antonym_flip_detected(self) -> None:
        corpus = {
            "duration.md#modified-duration": (
                "Modified duration\nBecause of the division by (1 + y/k), "
                "modified duration is always smaller than Macaulay duration "
                "whenever the yield is positive."
            )
        }
        checker = cc.Checker([], corpus_texts=corpus)
        result = checker.check_card(
            human_card(
                "w3",
                "At a positive yield, how does a bond's modified duration "
                "compare with its Macaulay duration?",
                "Modified duration is always larger than Macaulay duration "
                "whenever the yield is positive.",
            )
        )
        self.assertEqual(result.label, "wrong")
        self.assertTrue(
            any("contradicts corpus" in r for r in result.reasons),
            result.reasons,
        )

    def test_generated_without_metadata_is_blocked(self) -> None:
        card = {
            "_cc_id": "gen/no-meta",
            "kind": "compare",
            "provenance": {"generator": "param:compare_x_v1"},
            "left_title": "A",
            "left_body": "Weighted-average time to the bond's cash flows.",
            "right_title": "B",
            "right_body": "Price sensitivity to a one-unit yield change.",
            "discriminator": (
                "Which of these two duration measures is a time in years, "
                "and which is a rate sensitivity?"
            ),
            "rationale": "One is a time, the other a sensitivity.",
        }
        result = empty_checker().check_card(card)
        self.assertEqual(result.label, "correct_useful")
        self.assertEqual(result.correctness, "unverifiable")
        self.assertFalse(result.shipped)  # generated + unverifiable => block
        self.assertTrue(any("unverifiable-by-machine" in r for r in result.reasons))

    def test_human_card_ships_on_attestation(self) -> None:
        result = empty_checker().check_card(
            human_card(
                "h",
                "Under the mosaic theory, may an analyst act on conclusions "
                "drawn from public plus nonmaterial nonpublic information?",
                "Yes - assembling such a mosaic does not violate Standard II(A).",
            )
        )
        self.assertEqual(result.correctness, "unverifiable")
        self.assertTrue(result.shipped)


class FrozenCutoffTests(unittest.TestCase):
    """Freeze-test: silent retuning of the cutoff must break the build."""

    def test_constants_exact(self) -> None:
        self.assertEqual(cc.DUP_JACCARD, 0.55)
        self.assertEqual(cc.MIN_QUESTION_CHARS, 20)
        self.assertEqual(cc.MIN_QUESTION_CONTENT_TOKENS, 3)
        self.assertEqual(cc.GOLD_MATCH_JACCARD, 0.75)
        self.assertEqual(cc.ANSWER_AGREE_JACCARD, 0.20)
        self.assertEqual(cc.CONTRADICTION_OVERLAP, 0.35)
        self.assertEqual(cc.SENTENCE_MIN_CONTENT, 5)
        self.assertEqual(cc.SOURCE_DOC, "duration.md")
        self.assertEqual(cc.BATCH_N, 50)
        self.assertEqual(cc.CUTOFF_FROZEN_ON, "2026-07-05")

    def test_ship_policy_wording(self) -> None:
        for phrase in (
            "SHIPS only if classification == correct_useful",
            "wrong => BLOCKED",
            "bad_teaching => BLOCKED",
            "unverifiable-by-machine => BLOCKED",
        ):
            self.assertIn(phrase, cc.SHIP_POLICY)


class GoldSetIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = cc.load_gold(GOLD_PATH)

    def test_integrity(self) -> None:
        self.assertEqual(cc.gold_integrity_errors(self.records), [])

    def test_counts_and_topics(self) -> None:
        gold = [r for r in self.records if not r.get("defect_type")]
        defects = [r for r in self.records if r.get("defect_type")]
        self.assertEqual(len(gold), 50)
        self.assertEqual(len(defects), 15)
        self.assertEqual(len({r["id"] for r in self.records}), 65)
        for topic in cc.TOPICS:
            self.assertEqual(sum(1 for r in gold if r["topic"] == topic), 5, topic)
        for dt in ("wrong", "vague", "duplicate"):
            self.assertEqual(sum(1 for r in defects if r["defect_type"] == dt), 5, dt)

    def test_duplicate_defects_name_their_gold_source(self) -> None:
        gold_ids = {r["id"] for r in self.records if not r.get("defect_type")}
        for r in self.records:
            if r.get("defect_type") == "duplicate":
                self.assertIn(r.get("based_on"), gold_ids, r["id"])

    def test_quantitative_answers_rederivable(self) -> None:
        """Recompute every quantitative gold answer with independent
        hard-coded arithmetic; the stated answer must contain the figure."""
        expected: dict[str, tuple[float, str]] = {
            "gold::qm::01": (10_000 * (1 + 0.08 / 4) ** 8, "11,716.59"),
            "gold::qm::02": ((43 - 40 + 1) / 40 * 100, "10%"),
            "gold::qm::03": (((1 + 0.12 / 12) ** 12 - 1) * 100, "12.68"),
            "gold::qm::04": (1000 * (1 - 1.10**-3) / 0.10, "2,486.85"),
            "gold::econ::01": (-10 / 5, "-2.0"),
            "gold::econ::02": (7 - 3, "4%"),
            "gold::fsa::01": (100 * 10 + 50 * 12, "1,600"),
            "gold::fsa::02": (300 / 150, "2.0"),
            "gold::fsa::03": ((1_000_000 - 100_000) / 450_000, "2.00"),
            "gold::corp::01": (6 * (1 - 0.25), "4.5%"),
            "gold::corp::02": (0.6 * 12 + 0.4 * 6 * (1 - 0.30), "8.88%"),
            "gold::equity::01": (5.00 / 0.08, "62.50"),
            "gold::equity::02": (2.00 / (0.10 - 0.04), "33.33"),
            "gold::fi::01": (8.00 / 1.03, "7.77"),
            "gold::fi::02": (-5.0 * 0.0050 * 100, "-2.5%"),
            "gold::fi::03": (
                (1 * 5 / 1.05 + 2 * 105 / 1.05**2) / (5 / 1.05 + 105 / 1.05**2),
                "1.95",
            ),
            "gold::deriv::01": (100 * 1.05, "105.00"),
            "gold::deriv::02": (5.00 + 50 / 1.04 - 50, "3.08"),
            "gold::alt::01": ((15 - 2 - 0.20 * (15 - 2)) / 100 * 100, "10.4%"),
            "gold::pm::01": (0.40 * 10 + 0.60 * 5, "7.0%"),
            "gold::pm::02": ((10 - 2) / 16, "0.50"),
            "gold::pm::03": (2 + 1.2 * (8 - 2), "9.2%"),
        }
        by_id = {r["id"]: r for r in self.records}
        quant_gold = [
            r["id"]
            for r in self.records
            if not r.get("defect_type") and r.get("answer_type") == "quantitative"
        ]
        self.assertEqual(sorted(quant_gold), sorted(expected))
        for rec_id, (value, fragment) in expected.items():
            answer = by_id[rec_id]["correct_answer"]
            self.assertIn(fragment, answer, rec_id)
            # The stated figure must equal the recomputed value at the
            # precision the answer states it to.
            numeric = fragment.replace(",", "").replace("%", "")
            decimals = len(numeric.split(".")[1]) if "." in numeric else 0
            self.assertAlmostEqual(
                round(value, decimals),
                float(numeric),
                places=decimals,
                msg=rec_id,
            )

    def test_gold_batch_id_namespaces(self) -> None:
        for r in self.records:
            prefix = "defect::" if r.get("defect_type") else "gold::"
            self.assertTrue(r["id"].startswith(prefix), r["id"])


class BatchGenerationTests(unittest.TestCase):
    def test_source_filter_selects_duration_generators(self) -> None:
        gens = [g for g in G.GENERATORS if g.passage.split("#")[0] == cc.SOURCE_DOC]
        self.assertEqual(
            sorted(g.name for g in gens),
            [
                "duration_price_change",
                "macaulay_from_cashflows",
                "mod_duration_from_mac",
            ],
        )


if __name__ == "__main__":
    unittest.main()
