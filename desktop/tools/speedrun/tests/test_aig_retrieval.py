# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for retrieval-for-grounding (aig/retrieval.py) - stdlib arms only.

The optional dense/rerank arms need sentence-transformers and model
downloads, so these tests force the stdlib-only path for determinism; the
degraded-arm reporting is asserted instead of faked numbers. The stdlib
vector arm (feature-hashed TF-IDF) and the rrf_stdlib fusion arm are fully
covered here: hashing determinism, hand-computed cosine math, tuning-grid
selection, fusion, the honest-claim adjudication paths, and the train-gated
best_arm preference.
"""

from __future__ import annotations

import math
import sys
import unittest
import zlib
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


class VectorHashTests(unittest.TestCase):
    def test_bucket_hashing_is_fixed_not_salted(self) -> None:
        """crc32 buckets are process- and platform-stable constants.

        The built-in ``hash`` is salted per process (PYTHONHASHSEED), so the
        index MUST NOT use it; these literals only hold because the arm
        hashes with zlib.crc32.
        """
        passage = R.Passage(pid="d.md#x", doc="d.md", title="", text="dur")
        idx = R.VectorHashIndex([passage], ngram=(4, 4), dim=1 << 16)
        counts = idx._bucket_counts("dur")  # grams: '<dur', 'dur>'
        self.assertEqual(counts, {26202: 1, 4908: 1})
        self.assertEqual(zlib.crc32(b"<dur") % (1 << 16), 26202)
        self.assertEqual(zlib.crc32(b"dur>") % (1 << 16), 4908)

    def test_hand_computed_cosine_and_ordering(self) -> None:
        """Exact tf-idf cosine on a two-passage corpus, derived by hand.

        With ngram=(3,3): "alpha" -> 5 grams (df=2 -> idf=ln(3/3)+1=1),
        "beta" -> 4 grams and "gamma" -> 5 grams (df=1 -> idf=ln(3/2)+1).
        Doc vectors are l2-normalized; score = dot / query_norm.
        """
        passages = [
            R.Passage(pid="a.md#ab", doc="a.md", title="", text="alpha beta"),
            R.Passage(pid="b.md#ag", doc="b.md", title="", text="alpha gamma"),
        ]
        dim = 1 << 16
        idx = R.VectorHashIndex(passages, ngram=(3, 3), dim=dim)
        # the hand math assumes no bucket collisions among the 14 grams
        grams = []
        for tok in ("alpha", "beta", "gamma"):
            marked = f"<{tok}>"
            grams += [marked[i : i + 3] for i in range(len(marked) - 2)]
        buckets = {zlib.crc32(g.encode()) % dim for g in grams}
        self.assertEqual(len(buckets), len(set(grams)))

        idf_uni = math.log(3 / 2) + 1  # df=1 grams; shared "alpha" grams idf=1
        norm_a = math.sqrt(5 + 4 * idf_uni**2)
        norm_b = math.sqrt(5 + 5 * idf_uni**2)
        q_norm = math.sqrt(5)
        scores = dict(idx.score("alpha"))
        self.assertAlmostEqual(scores["a.md#ab"], 5 / norm_a / q_norm, places=12)
        self.assertAlmostEqual(scores["b.md#ag"], 5 / norm_b / q_norm, places=12)
        # expected exact orderings: shared-term query prefers the shorter
        # doc; unique-term queries hit their own doc, other doc scores 0.
        self.assertEqual(idx.top("alpha"), ["a.md#ab", "b.md#ag"])
        self.assertEqual(idx.top("beta"), ["a.md#ab", "b.md#ag"])
        self.assertEqual(idx.top("gamma"), ["b.md#ag", "a.md#ab"])
        self.assertEqual(dict(idx.score("gamma"))["a.md#ab"], 0.0)

    def test_reinstantiation_is_deterministic(self) -> None:
        """Same corpus + params -> identical rankings on re-instantiation."""
        passages = R.load_corpus(CORPUS_DIR)
        queries = [
            "modified duration price sensitivity",
            "LIFO reserve restate inventory",
        ]
        a = R.VectorHashIndex(passages, ngram=(3, 5), dim=1 << 16)
        b = R.VectorHashIndex(passages, ngram=(3, 5), dim=1 << 16)
        for q in queries:
            self.assertEqual(a.top(q, 10), b.top(q, 10))
            self.assertEqual(a.score(q), b.score(q))

    def test_tuning_grid_selection(self) -> None:
        """Grid is exhaustive; chosen = first config hitting the best key."""
        passages = R.load_corpus(CORPUS_DIR)
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
        index, log = R.tune_vector_hash(passages, train)
        self.assertEqual(
            len(log["grid"]),
            len(R.VECTOR_HASH_GRID_NGRAM) * len(R.VECTOR_HASH_GRID_DIM),
        )
        self.assertIn(tuple(log["chosen"]["ngram"]), R.VECTOR_HASH_GRID_NGRAM)
        self.assertIn(log["chosen"]["dim"], R.VECTOR_HASH_GRID_DIM)
        # the returned index is built with the chosen config
        self.assertEqual(list(index.ngram), log["chosen"]["ngram"])
        self.assertEqual(index.dim, log["chosen"]["dim"])
        # strict > comparison keeps the FIRST grid row achieving the max key
        best_key = max((r["train_p1"], r["train_p5_hit"]) for r in log["grid"])
        first_best = next(
            r for r in log["grid"] if (r["train_p1"], r["train_p5_hit"]) == best_key
        )
        self.assertEqual(log["chosen"]["ngram"], first_best["ngram"])
        self.assertEqual(log["chosen"]["dim"], first_best["dim"])


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


class RrfStdlibTests(unittest.TestCase):
    """The stdlib fusion arm: RRF(k=60) over bm25_tuned + vector_hash_tuned."""

    @classmethod
    def setUpClass(cls) -> None:
        original = R.try_load_dense
        R.try_load_dense = lambda p: (None, None, "disabled for test")
        try:
            cls.grounder = R.GroundingRetriever(CORPUS_DIR, G.generate_all())
        finally:
            R.try_load_dense = original

    def test_fusion_on_synthetic_rankings(self) -> None:
        """arm_rrf_stdlib is exactly rrf_fuse over the two arms' rankings."""

        class _Stub:
            def __init__(self, ranking: list[str]):
                self._ranking = ranking

            def top(self, query: str, k: int = R.TOP_K) -> list[str]:
                return self._ranking

        g = self.grounder
        bm25, vhash = g.bm25, g.vhash
        try:
            g.bm25 = _Stub(["solo", "both", "x1"])  # type: ignore[assignment]
            g.vhash = _Stub(["y1", "both", "y2"])  # type: ignore[assignment]
            fused = g.arm_rrf_stdlib("ignored")
            # RRF(k=60): "both" 2/62 beats "solo"/"y1" 1/61 (agreement wins);
            # rank-1 singles tie and break lexically; then the rank-3 pair.
            self.assertEqual(fused, ["both", "solo", "y1", "x1", "y2"])
            self.assertEqual(
                fused, R.rrf_fuse([["solo", "both", "x1"], ["y1", "both", "y2"]])
            )
        finally:
            g.bm25, g.vhash = bm25, vhash

    def test_fusion_on_real_corpus_matches_rrf_fuse(self) -> None:
        g = self.grounder
        query = "modified duration price sensitivity of a bond"
        expected = R.rrf_fuse([g.bm25.top(query), g.vhash.top(query)])
        self.assertEqual(g.arm_rrf_stdlib(query), expected)


class DenseOptInTests(unittest.TestCase):
    def test_dense_stack_is_opt_in(self) -> None:
        """Without SPEEDRUN_DENSE=1 the ML stack must not even be probed:
        a numpy/torch ABI mismatch aborts the interpreter, so the default
        path has to stay stdlib-only (authoring-time tooling, C7 slice)."""
        import os

        previous = os.environ.pop(R.DENSE_OPT_IN_ENV, None)
        try:
            dense, reranker, reason = R.try_load_dense([])
            self.assertIsNone(dense)
            self.assertIsNone(reranker)
            self.assertIn("opt-in", reason)
            self.assertIn(R.DENSE_OPT_IN_ENV, reason)
        finally:
            if previous is not None:
                os.environ[R.DENSE_OPT_IN_ENV] = previous


def _arm(name: str, p1: float, p5: float) -> R.ArmResult:
    return R.ArmResult(name, True, p_at_1=p1, p_at_5=p5, median_latency_ms=1.0)


class HonestClaimTests(unittest.TestCase):
    """Adjudication paths of _honest_claim in the default (stdlib) env."""

    def test_win_makes_the_claim_without_fallback(self) -> None:
        by_name = {
            "bm25_tuned": _arm("bm25_tuned", 0.5, 0.9),
            "vector_hash_tuned": _arm("vector_hash_tuned", 0.4, 0.8),
            "rrf_stdlib": _arm("rrf_stdlib", 0.6, 1.0),
        }
        claim = R._honest_claim(by_name, 22)
        self.assertIn("beat BOTH the keyword baseline (tuned BM25)", claim)
        self.assertIn("vector baseline (tuned feature-hashed TF-IDF cosine)", claim)
        self.assertIn("small-N caveat", claim)
        self.assertNotIn("Fallback committed result", claim)

    def test_tie_says_so_and_cites_the_archive(self) -> None:
        by_name = {
            "bm25_tuned": _arm("bm25_tuned", 0.5, 0.9),
            "vector_hash_tuned": _arm("vector_hash_tuned", 0.4, 0.8),
            "rrf_stdlib": _arm("rrf_stdlib", 0.5, 0.9),
        }
        claim = R._honest_claim(by_name, 22)
        self.assertIn("TIED one or both stdlib baselines", claim)
        self.assertIn(R.ARCHIVE_FALLBACK_NOTE, claim)
        self.assertIn("retrieval_eval_fullstack_20260703.md", claim)
        self.assertIn("0.727", claim)
        self.assertIn("quoted verbatim", claim)

    def test_loss_says_so_and_cites_the_archive(self) -> None:
        by_name = {
            "bm25_tuned": _arm("bm25_tuned", 0.5, 0.9),
            "vector_hash_tuned": _arm("vector_hash_tuned", 0.4, 0.8),
            "rrf_stdlib": _arm("rrf_stdlib", 0.3, 0.7),
        }
        claim = R._honest_claim(by_name, 22)
        self.assertIn("did NOT beat both stdlib baselines", claim)
        self.assertIn(R.ARCHIVE_FALLBACK_NOTE, claim)

    def test_full_stack_branch_unchanged_when_dense_available(self) -> None:
        by_name = {
            "bm25_tuned": _arm("bm25_tuned", 0.5, 0.955),
            "dense_tuned": _arm("dense_tuned", 0.455, 1.0),
            "rrf_rerank": _arm("rrf_rerank", 0.727, 1.0),
        }
        claim = R._honest_claim(by_name, 22)
        self.assertIn("RRF+rerank beat BOTH tuned BM25 and tuned dense", claim)
        self.assertNotIn("Fallback committed result", claim)

    def test_nothing_available_declines(self) -> None:
        claim = R._honest_claim({}, 0)
        self.assertIn("could NOT be adjudicated", claim)
        self.assertIn("claim is therefore not made", claim)


class BestArmGateTests(unittest.TestCase):
    """best_arm(): torch path untouched; stdlib path gated on TRAIN metrics."""

    @classmethod
    def setUpClass(cls) -> None:
        original = R.try_load_dense
        R.try_load_dense = lambda p: (None, None, "disabled for test")
        try:
            cls.grounder = R.GroundingRetriever(CORPUS_DIR, G.generate_all())
        finally:
            R.try_load_dense = original

    def test_selection_is_consistent_with_recorded_train_metrics(self) -> None:
        """chosen == rrf_stdlib iff >= on BOTH train metrics AND > on one."""
        sel = self.grounder.stdlib_arm_selection
        bm, fu = sel["train_bm25"], sel["train_rrf_stdlib"]
        ge_both = fu["p1"] >= bm["p1"] and fu["p5_hit"] >= bm["p5_hit"]
        wins_one = fu["p1"] > bm["p1"] or fu["p5_hit"] > bm["p5_hit"]
        expected = "rrf_stdlib" if (ge_both and wins_one) else "bm25"
        self.assertEqual(sel["chosen"], expected)
        self.assertIn("no test leakage", sel["rule"])

    def test_best_arm_prefers_rrf_stdlib_when_gate_says_so(self) -> None:
        g = self.grounder
        saved = g.stdlib_arm_selection
        try:
            g.stdlib_arm_selection = {**saved, "chosen": "rrf_stdlib"}
            name, arm = g.best_arm()
            self.assertEqual(name, "rrf_stdlib")
            self.assertEqual(
                arm("duration of a bond"), g.arm_rrf_stdlib("duration of a bond")
            )
        finally:
            g.stdlib_arm_selection = saved

    def test_best_arm_keeps_bm25_when_gate_declines(self) -> None:
        g = self.grounder
        saved = g.stdlib_arm_selection
        try:
            g.stdlib_arm_selection = {**saved, "chosen": "bm25"}
            name, arm = g.best_arm()
            self.assertEqual(name, "bm25")
            self.assertEqual(
                arm("duration of a bond"), g.arm_bm25("duration of a bond")
            )
        finally:
            g.stdlib_arm_selection = saved

    def test_torch_path_still_wins_when_dense_loaded(self) -> None:
        """With dense + reranker present, best_arm stays rrf_rerank."""
        g = self.grounder
        saved_dense, saved_rr = g.dense, g.reranker
        try:
            g.dense, g.reranker = object(), object()
            name, _arm_fn = g.best_arm()
            self.assertEqual(name, "rrf_rerank")
        finally:
            g.dense, g.reranker = saved_dense, saved_rr

    def test_no_train_queries_keeps_bm25(self) -> None:
        original = R.try_load_dense
        R.try_load_dense = lambda p: (None, None, "disabled for test")
        try:
            g = R.GroundingRetriever(CORPUS_DIR, [])
        finally:
            R.try_load_dense = original
        self.assertEqual(g.stdlib_arm_selection["chosen"], "bm25")
        self.assertEqual(g.best_arm()[0], "bm25")


class ScopedCliTests(unittest.TestCase):
    def test_main_writes_only_the_two_report_files(self) -> None:
        """The scoped entry regenerates eval/retrieval_eval.{json,md} ONLY."""
        import contextlib
        import io
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = R.main(["--eval-dir", tmp])
            self.assertEqual(rc, 0)
            written = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(written, ["retrieval_eval.json", "retrieval_eval.md"])
            report = json.loads((Path(tmp) / "retrieval_eval.json").read_text())
            arms = {a["arm"]: a for a in report["arms"]}
            for name in ("bm25_tuned", "vector_hash_tuned", "rrf_stdlib"):
                self.assertTrue(arms[name]["available"], name)
            self.assertIn("stdlib_best_arm", report["meta"])
            md = (Path(tmp) / "retrieval_eval.md").read_text()
            self.assertIn("| rrf_stdlib |", md)
            self.assertIn("retrieval_eval_fullstack_20260703.md", md)


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

    def test_grounding_and_eval_stdlib_only(self) -> None:
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
            # the stdlib side-by-side always runs: keyword, vector, fusion
            for name in ("bm25_tuned", "vector_hash_tuned", "rrf_stdlib"):
                self.assertTrue(arms[name]["available"], name)
                self.assertGreater(arms[name]["precision_at_5"], 0.5)
                self.assertIsNotNone(arms[name]["median_latency_ms"])
            # degraded torch arms are reported as unavailable, never faked
            for name in ("dense_tuned", "rrf", "rrf_rerank"):
                self.assertFalse(arms[name]["available"])
                self.assertIsNone(arms[name]["precision_at_1"])
            # the claim is now ADJUDICATED in the default env (win, tie or
            # loss - never declined), with the archive fallback on non-wins
            claim = evaluation["honest_claim"]
            self.assertNotIn("could NOT be adjudicated", claim)
            self.assertIn("rrf_stdlib", claim)
            if "beat BOTH the keyword baseline" not in claim:
                self.assertIn(R.ARCHIVE_FALLBACK_NOTE, claim)
            self.assertIn("SYNTHETIC", evaluation["meta"]["qrels"])
            self.assertIn("chosen", evaluation["meta"]["vector_hash_tuning"])
            self.assertIn(
                evaluation["meta"]["stdlib_best_arm"]["chosen"],
                ("bm25", "rrf_stdlib"),
            )
        finally:
            R.try_load_dense = original


if __name__ == "__main__":
    unittest.main()
