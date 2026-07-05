# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Invariants of the REAL probe bank (probes/probe_bank.jsonl) against the
speedrun-probe-v1 contract (probes/PROBE_SCHEMA.md). stdlib only; run with:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_harness  # noqa: E402

BANK_PATH = Path(__file__).resolve().parents[1] / "probes" / "probe_bank.jsonl"


def load_bank() -> list[dict]:
    records, failures = probe_harness.load_bank(BANK_PATH)
    assert not failures, failures
    return records


BANK = load_bank()


class BankShapeTests(unittest.TestCase):
    def test_bank_validates_completely(self) -> None:
        self.assertEqual(probe_harness.validate_bank(BANK), [])

    def test_seventy_records_thirty_five_concepts_two_variants(self) -> None:
        self.assertEqual(len(BANK), 70)
        by_concept: dict[str, list[str]] = {}
        for record in BANK:
            by_concept.setdefault(record["concept_id"], []).append(record["variant"])
        self.assertEqual(len(by_concept), 35)
        for concept_id, variants in by_concept.items():
            self.assertEqual(sorted(variants), ["a", "b"], f"{concept_id}: {variants}")

    def test_concept_ids_contiguous(self) -> None:
        expected = {f"c{number:02d}" for number in range(1, 36)}
        self.assertEqual({record["concept_id"] for record in BANK}, expected)

    def test_titles_unique(self) -> None:
        titles = [record["title"] for record in BANK]
        self.assertEqual(len(titles), len(set(titles)))


class PoolPartitionTests(unittest.TestCase):
    def test_deterministic_partition_rule(self) -> None:
        """c01..c25 -> performance, c26..c35 -> calibration, no exceptions."""
        for record in BANK:
            number = int(record["concept_id"][1:])
            expected = "performance" if number <= 25 else "calibration"
            self.assertEqual(record["pool"], expected, f"{record['concept_id']} pool")

    def test_performance_pool_meets_the_rust_gate(self) -> None:
        """>= 50 items so the MIN_DELAYED_PROBES=50 give-up gate is
        satisfiable (and exactly 50: tight, never padded)."""
        performance = [r for r in BANK if r["pool"] == "performance"]
        self.assertEqual(len(performance), 50)
        self.assertGreaterEqual(len(performance), probe_harness.MIN_PERFORMANCE_ITEMS)

    def test_pools_concept_disjoint(self) -> None:
        performance = {r["concept_id"] for r in BANK if r["pool"] == "performance"}
        calibration = {r["concept_id"] for r in BANK if r["pool"] == "calibration"}
        self.assertEqual(performance & calibration, set())
        self.assertEqual(len(calibration) * 2, 20)

    def test_variants_share_pool_topic_cluster(self) -> None:
        by_concept: dict[str, list[dict]] = {}
        for record in BANK:
            by_concept.setdefault(record["concept_id"], []).append(record)
        for concept_id, pair in by_concept.items():
            for fld in ("pool", "topic", "cluster"):
                self.assertEqual(pair[0][fld], pair[1][fld], f"{concept_id}.{fld}")


class CoverageTests(unittest.TestCase):
    def test_all_ten_topics_covered_in_each_pool(self) -> None:
        for pool in ("performance", "calibration"):
            topics = {r["topic"] for r in BANK if r["pool"] == pool}
            self.assertEqual(topics, set(probe_harness.TOPICS), f"{pool} pool coverage")

    def test_cluster_prefixes_match_topics(self) -> None:
        for record in BANK:
            prefix = record["cluster"].split("::", 1)[0]
            self.assertEqual(
                probe_harness.CLUSTER_PREFIX_TOPIC[prefix],
                record["topic"],
                record["cluster"],
            )

    def test_studied_sample_deck_clusters_are_probed(self) -> None:
        """The bridge proof needs probes over the material actually studied:
        every cluster family of the sample deck + the ladder deck appears."""
        studied = {
            # cfa_sample_cards.py cluster tags (suffixes)
            "ethics::standards",
            "quant::return_measures",
            "quant::hypothesis_errors",
            "econ::goods_types",
            "econ::market_structures",
            "fsa::inventory_cost_flow",
            "fsa::liquidity_ratios",
            "corp::capital_budgeting",
            "equity::market_efficiency",
            "fi::duration",
            "fi::spreads",
            "deriv::forward_commitments",
            "deriv::option_payoffs",
            "alt::futures_curves",
            "pm::cml_sml",
            "pm::risk_adjusted_measures",
            # ladder deck clusters (items/generated.jsonl)
            "qm::tvm",
            "fsa::inventory",
        }
        probed = {record["cluster"] for record in BANK}
        self.assertEqual(studied - probed, set(), "unprobed studied clusters")


class ContentQualityTests(unittest.TestCase):
    def test_stems_are_application_scenarios(self) -> None:
        for record in BANK:
            self.assertGreaterEqual(
                len(record["stem"].split()),
                probe_harness.MIN_STEM_WORDS,
                record["title"],
            )

    def test_choices_distinct_and_correct_letter_valid(self) -> None:
        for record in BANK:
            choices = record["choices"]
            self.assertEqual(sorted(choices), ["A", "B", "C"], record["title"])
            self.assertEqual(
                len({choices[k].strip() for k in "ABC"}), 3, record["title"]
            )
            self.assertIn(record["correct"], "ABC", record["title"])

    def test_rationales_explain_both_distractors(self) -> None:
        for record in BANK:
            wrong = set("ABC") - {record["correct"]}
            for letter in wrong:
                self.assertRegex(
                    record["rationale"],
                    rf"\b{letter}\b",
                    f"{record['title']}: rationale must dismiss {letter}",
                )

    def test_hand_authored_provenance(self) -> None:
        for record in BANK:
            self.assertEqual(
                record["provenance"], {"author": "hand", "date": "2026-07-04"}
            )

    def test_variant_stems_diverge_materially(self) -> None:
        """Variant b is a genuine rewording: token Jaccard < 0.7 after
        lowercasing + stopword strip (documented threshold)."""
        divergence = probe_harness.variant_divergence(BANK)
        self.assertEqual(len(divergence), 35)
        for concept_id, similarity in divergence.items():
            self.assertLess(
                similarity,
                probe_harness.VARIANT_JACCARD_MAX,
                f"{concept_id}: variants too similar ({similarity})",
            )


class TagDerivationTests(unittest.TestCase):
    def test_tags_exactly_as_schema_specifies(self) -> None:
        record = next(
            r for r in BANK if r["concept_id"] == "c01" and r["variant"] == "a"
        )
        self.assertEqual(
            probe_harness.tags_for_probe(record),
            [
                "probe::held_out",
                "probe::pool::performance",
                "cfa::topic::ethics",
                "cluster::ethics::standards",
                "probe::concept::c01",
                "probe::variant::a",
            ],
        )

    def test_no_probe_ever_carries_aig_or_rung_tags(self) -> None:
        for record in BANK:
            tags = probe_harness.tags_for_probe(record)
            self.assertFalse(any(t.startswith("aig::") for t in tags))
            self.assertFalse(any(t.startswith("rung::") for t in tags))
            pools = [t for t in tags if t.startswith("probe::pool::")]
            self.assertEqual(len(pools), 1, "exactly one pool tag")


class LeakageTests(unittest.TestCase):
    def test_real_bank_clean_against_real_generator_inputs(self) -> None:
        """No 8-gram overlap with corpus/*.md, items/*.jsonl, or the aig
        prompt templates - in either direction."""
        result = probe_harness.leakage_scan(BANK)
        self.assertEqual(result["forward_hits"], [])
        self.assertEqual(result["reverse_hits"], [])
        self.assertTrue(result["passed"])
        names = result["sources_scanned"]
        self.assertTrue(any(name.startswith("corpus/") for name in names))
        self.assertTrue(any(name.startswith("items/") for name in names))
        self.assertIn("aig/prompts.py", names)

    def test_scanner_catches_a_planted_quote(self) -> None:
        """A probe quoting >= 8 corpus tokens must be flagged in BOTH
        directions (the wall is what keeps the bank held-out)."""
        corpus_text = (
            "the weighted average time to receipt of the bond cash flows "
            "measured in periods and divided by the price"
        )
        leaky = copy.deepcopy(BANK[0])
        leaky["stem"] = (
            "A bond analyst defines duration as the weighted average time to "
            "receipt of the bond cash flows and then asks which figure to use."
        )
        result = probe_harness.leakage_scan(
            [leaky], sources={"corpus/fixture.md": corpus_text}, reference_pdf=""
        )
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["forward_hits"]), 1)
        self.assertEqual(len(result["reverse_hits"]), 1)
        self.assertEqual(result["reverse_hits"][0]["source"], "corpus/fixture.md")

    def test_clean_fixture_passes(self) -> None:
        result = probe_harness.leakage_scan(
            [BANK[0]],
            sources={"corpus/fixture.md": "entirely unrelated prose about sailing"},
            reference_pdf="",
        )
        self.assertTrue(result["passed"])


class NumericSpotCheckTests(unittest.TestCase):
    """Recompute a sample of the bank's numeric answers from the stems'
    own figures (guards against a typo silently corrupting an answer)."""

    def _record(self, concept_id: str, variant: str) -> dict:
        return next(
            r for r in BANK if r["concept_id"] == concept_id and r["variant"] == variant
        )

    def _correct_value(self, record: dict) -> float:
        text = record["choices"][record["correct"]]
        return float(re.sub(r"[^\d.+-]", "", text))

    def test_tvm_future_value(self) -> None:  # c06a
        record = self._record("c06", "a")
        self.assertAlmostEqual(self._correct_value(record), 25000 * 1.015**12, delta=5)

    def test_tvm_annuity_present_value(self) -> None:  # c06b
        record = self._record("c06", "b")
        self.assertAlmostEqual(
            self._correct_value(record),
            2000 * (1 - 1.04**-5) / 0.04,
            delta=1,
        )

    def test_lifo_cogs(self) -> None:  # c09a
        record = self._record("c09", "a")
        self.assertEqual(self._correct_value(record), 100 * 12 + 50 * 10)

    def test_weighted_average_ending_inventory(self) -> None:  # c11a
        record = self._record("c11", "a")
        self.assertEqual(self._correct_value(record), 150 * (200 * 3 + 300 * 4) / 500)

    def test_wacc(self) -> None:  # c13a
        record = self._record("c13", "a")
        self.assertAlmostEqual(
            self._correct_value(record),
            0.6 * 12 + 0.4 * 7 * 0.75,
            places=1,
        )

    def test_gordon_growth(self) -> None:  # c15a
        record = self._record("c15", "a")
        self.assertAlmostEqual(
            self._correct_value(record), 2.40 / (0.11 - 0.05), places=2
        )

    def test_modified_duration(self) -> None:  # c17a
        record = self._record("c17", "a")
        self.assertAlmostEqual(self._correct_value(record), 8.40 / 1.03, places=2)

    def test_put_call_parity(self) -> None:  # c33a
        record = self._record("c33", "a")
        self.assertAlmostEqual(
            self._correct_value(record), 6.20 + 100 / 1.04 - 95, places=2
        )

    def test_hedge_fund_fees(self) -> None:  # c23a
        record = self._record("c23", "a")
        management = 0.02 * 120
        incentive = 0.20 * (120 - management - 100)
        self.assertAlmostEqual(
            self._correct_value(record), management + incentive, places=2
        )

    def test_treynor(self) -> None:  # c25b
        record = self._record("c25", "b")
        self.assertAlmostEqual(self._correct_value(record), (10 - 2) / 1.25, places=2)


if __name__ == "__main__":
    unittest.main()
