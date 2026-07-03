# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for retrieval-for-grounding (aig/retrieval.py) - stdlib arms only.

The optional dense/rerank arms need sentence-transformers and model
downloads, so these tests force the BM25-only path for determinism; the
degraded-arm reporting is asserted instead of faked numbers.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig import generators as G  # noqa: E402
from aig import retrieval as R  # noqa: E402

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"


class CorpusTests(unittest.TestCase):
    def test_passages_have_stable_ids(self) -> None:
        passages = R.load_corpus(CORPUS_DIR)
        self.assertGreater(len(passages), 10)
        pids = [p.pid for p in passages]
        self.assertEqual(len(pids), len(set(pids)), "duplicate passage ids")
        for pid in pids:
            doc, slug = pid.split("#", 1)
            self.assertTrue(doc.endswith(".md"))
            self.assertTrue(slug)
        self.assertIn("duration.md#modified-duration", pids)
        self.assertIn("tvm.md#future-value-of-a-single-sum", pids)
        self.assertIn("inventory.md#lifo-reserve", pids)

    def test_declared_grounding_passages_exist(self) -> None:
        """Every generator's declared qrel passage must be a real passage."""
        pids = {p.pid for p in R.load_corpus(CORPUS_DIR)}
        for gen in G.GENERATORS:
            self.assertIn(gen.passage, pids, gen.gen_id)
        for item in G.compare_items():
            self.assertIn(item["_aig"]["declared_passage"], pids)


class Bm25Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = R.Bm25Index(R.load_corpus(CORPUS_DIR))

    def test_known_queries_hit_right_passage(self) -> None:
        cases = {
            "modified duration rescales Macaulay into price sensitivity "
            "divide by one plus periodic yield": "duration.md#modified-duration",
            "LIFO reserve restate LIFO inventory to FIFO add reserve": "inventory.md#lifo-reserve",
        }
        for query, expected in cases.items():
            self.assertEqual(self.index.top(query, 1)[0], expected, query)

    def test_scores_sorted_descending(self) -> None:
        ranked = self.index.score("duration of a bond")
        scores = [s for _, s in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_tuning_grid_search(self) -> None:
        train = [
            (
                "weighted average time PV of cash flows Macaulay",
                "duration.md#macaulay-duration",
            ),
            (
                "first in first out oldest costs to cost of goods sold",
                "inventory.md#fifo-cost-flow",
            ),
        ]
        k1, b, log = R.tune_bm25(self.index, train)
        self.assertIn(k1, R.BM25_GRID_K1)
        self.assertIn(b, R.BM25_GRID_B)
        self.assertEqual(len(log["grid"]), len(R.BM25_GRID_K1) * len(R.BM25_GRID_B))
        self.assertEqual(log["chosen"], {"k1": k1, "b": b})


class RrfTests(unittest.TestCase):
    def test_rrf_math(self) -> None:
        """score(pid) = sum over lists of 1/(k + rank), rank 1-based."""
        fused = R.rrf_fuse([["p1", "p2", "p3"], ["p2", "p1", "p3"]], k=60)
        # p1: 1/61 + 1/62; p2: 1/62 + 1/61 (tie); p3: 1/63 + 1/63
        s1 = 1 / 61 + 1 / 62
        s3 = 2 / 63
        self.assertGreater(s1, s3)
        # tie between p1/p2 broken lexically; p3 strictly last
        self.assertEqual(fused, ["p1", "p2", "p3"])

    def test_rrf_favors_agreement(self) -> None:
        # doc "both" is mid-ranked in both lists; "solo" tops one list only.
        fused = R.rrf_fuse([["solo", "both", "x1"], ["y1", "both", "y2"]], k=60)
        self.assertEqual(fused[0], "both")

    def test_rrf_single_list_preserves_order(self) -> None:
        self.assertEqual(R.rrf_fuse([["a", "b", "c"]]), ["a", "b", "c"])


class SplitAndEvalTests(unittest.TestCase):
    def test_split_by_cluster_is_disjoint_and_seeded(self) -> None:
        items = G.generate_all()
        qrels = R.qrels_from_items(items)
        train1, eval1, held1 = R.split_by_cluster(qrels, seed=42)
        train2, eval2, held2 = R.split_by_cluster(qrels, seed=42)
        self.assertEqual(held1, held2)
        self.assertEqual(len(train1), len(train2))
        self.assertTrue(all(q.cluster != held1 for q in train1))
        self.assertTrue(all(q.cluster == held1 for q in eval1))
        # provenance wall: no eval qrel passage may appear as a TRAIN qrel
        train_passages = {q.qrel_pid for q in train1}
        eval_passages = {q.qrel_pid for q in eval1}
        self.assertFalse(train_passages & eval_passages)

    def test_grounding_and_eval_bm25_only(self) -> None:
        original = R.try_load_dense
        R.try_load_dense = lambda p: (None, None, "disabled for test")
        try:
            items = G.generate_all()
            grounder = R.GroundingRetriever(CORPUS_DIR, items)
            item = grounder.ground_item(items[0])
            src = item["source"]
            self.assertTrue(src["doc"].endswith(".md"))
            self.assertTrue(src["loc"].startswith("#"))
            self.assertTrue(src["passage"])
            evaluation = grounder.evaluate()
            arms = {a["arm"]: a for a in evaluation["arms"]}
            self.assertTrue(arms["bm25_tuned"]["available"])
            self.assertGreater(arms["bm25_tuned"]["precision_at_5"], 0.5)
            self.assertIsNotNone(arms["bm25_tuned"]["median_latency_ms"])
            # degraded arms are reported as unavailable, never faked
            for name in ("dense_tuned", "rrf", "rrf_rerank"):
                self.assertFalse(arms[name]["available"])
                self.assertIsNone(arms[name]["precision_at_1"])
            self.assertIn("could NOT be adjudicated", evaluation["honest_claim"])
            self.assertIn("SYNTHETIC", evaluation["meta"]["qrels"])
        finally:
            R.try_load_dense = original


if __name__ == "__main__":
    unittest.main()
