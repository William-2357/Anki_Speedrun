# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Retrieval-for-grounding [R21]: BM25 + optional dense + RRF + rerank.

Indexes the grounding corpus (``desktop/tools/speedrun/corpus/*.md``, split
into passages on ``##`` headings with stable ``doc.md#slug`` ids) and, for
each emitted item, finds the supporting passage stored as the item's named
``source`` (doc + loc + passage).

Arms:

- ``bm25`` - hand-rolled Okapi BM25 (stdlib only); k1/b tuned by grid search
  on the train split of the synthetic qrels.
- ``vector_hash`` - deterministic feature-hashed TF-IDF vector space model
  with cosine scoring (stdlib only): character n-grams inside word-boundary
  markers, hashed with ``zlib.crc32`` into a fixed-dim bucket space; n-gram
  range and dim tuned on the train split like BM25. This is a classical
  vector-space retrieval baseline, NOT a neural/semantic embedding.
- ``rrf_stdlib`` - RRF(k=60) over tuned BM25 top-100 + tuned vector_hash
  top-100: the fusion arm that is always runnable in the default (stdlib)
  environment, so the keyword-vs-vector-vs-fusion side-by-side never has to
  be declined for missing ML deps.
- ``dense`` - optional bi-encoder (sentence-transformers all-MiniLM-L6-v2)
  IF importable; "tuning" = selecting scoring (cosine/dot) and passage
  representation (with/without title) on the train split. If the library or
  models are unavailable the arm is recorded as unavailable in the eval
  report - numbers are never faked.
- ``rrf`` - reciprocal-rank fusion RRF(k=60) over BM25 top-100 + dense
  top-100.
- ``rrf_rerank`` - cross-encoder (ms-marco-MiniLM-L-6-v2) rerank of the RRF
  top-10, when available.

EVAL (synthetic qrels - fully automated, no human relevance labels): each
item's generator-declared grounding passage is its relevance label. This
self-referential eval is WEAKER than human-judged relevance (disclosed in
the report). Queries are split by CLUSTER (fixed seed): the held-out
cluster's queries are never used for tuning, so a passage grounding a
training-split item cannot be the eval qrel for a paraphrase of the same
item (provenance wall). Reported per arm: precision@1, precision@5 and
median per-query latency.

Running ``python3 aig/retrieval.py`` regenerates ONLY
``eval/retrieval_eval.{json,md}`` (same items/seed/eval path as
run_pipeline.py, without rewriting items or the other eval artifacts).
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_SPEEDRUN_DIR = Path(__file__).resolve().parents[1]
if str(_SPEEDRUN_DIR) not in sys.path:
    sys.path.insert(0, str(_SPEEDRUN_DIR))

from aig.pdf_text import tokenize  # noqa: E402

RRF_K = 60
TOP_K = 100
RERANK_N = 10
SPLIT_SEED = 20260703

BM25_GRID_K1 = [0.6, 0.9, 1.2, 1.5, 2.0]
BM25_GRID_B = [0.3, 0.5, 0.75, 0.9]

VECTOR_HASH_GRID_NGRAM: list[tuple[int, int]] = [(3, 4), (3, 5), (4, 5)]
VECTOR_HASH_GRID_DIM = [1 << 14, 1 << 16, 1 << 18]


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    pid: str  # doc.md#slug
    doc: str
    title: str
    text: str


def _slugify(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def load_corpus(corpus_dir: str | Path) -> list[Passage]:
    """Split each markdown doc into '## ' sections with stable ids."""
    passages: list[Passage] = []
    for path in sorted(Path(corpus_dir).glob("*.md")):
        current: str | None = None
        buf: list[str] = []
        for line in path.read_text().splitlines():
            if line.startswith("## "):
                if current is not None:
                    passages.append(_mk_passage(path.name, current, buf))
                current = line[3:].strip()
                buf = []
            elif current is not None:
                buf.append(line)
        if current is not None:
            passages.append(_mk_passage(path.name, current, buf))
    return passages


def _mk_passage(doc: str, heading: str, lines: list[str]) -> Passage:
    return Passage(
        pid=f"{doc}#{_slugify(heading)}",
        doc=doc,
        title=heading,
        text="\n".join(lines).strip(),
    )


# ---------------------------------------------------------------------------
# Okapi BM25 (stdlib only)
# ---------------------------------------------------------------------------


class Bm25Index:
    def __init__(self, passages: list[Passage], k1: float = 1.2, b: float = 0.75):
        self.passages = passages
        self.k1 = k1
        self.b = b
        self._doc_tokens = [tokenize(p.title + " " + p.text) for p in passages]
        self._doc_len = [len(t) for t in self._doc_tokens]
        self._avgdl = (sum(self._doc_len) / len(self._doc_len)) if passages else 0.0
        self._tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for toks in self._doc_tokens:
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self._tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        n = len(passages)
        # BM25+-style floor at 0 keeps very common terms from going negative.
        self._idf = {
            t: max(0.0, math.log((n - d + 0.5) / (d + 0.5) + 1.0))
            for t, d in df.items()
        }

    def score(
        self, query: str, k1: float | None = None, b: float | None = None
    ) -> list[tuple[str, float]]:
        """Ranked (pid, score), best first."""
        k1 = self.k1 if k1 is None else k1
        b = self.b if b is None else b
        q_tokens = tokenize(query)
        scores: list[tuple[str, float]] = []
        for i, p in enumerate(self.passages):
            tf = self._tf[i]
            dl = self._doc_len[i]
            s = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = self._idf.get(t, 0.0)
                f = tf[t]
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self._avgdl))
            scores.append((p.pid, s))
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores

    def top(self, query: str, k: int = TOP_K) -> list[str]:
        return [pid for pid, _ in self.score(query)[:k]]


def tune_bm25(
    index: Bm25Index, train: list[tuple[str, str]]
) -> tuple[float, float, dict[str, Any]]:
    """Grid-search k1/b on train (query, qrel_pid) pairs; returns best + log."""
    best = (index.k1, index.b)
    best_key = (-1.0, -1.0)
    log = []
    for k1 in BM25_GRID_K1:
        for b in BM25_GRID_B:
            p1 = p5 = 0
            for query, qrel in train:
                ranked = [pid for pid, _ in index.score(query, k1=k1, b=b)[:5]]
                p1 += ranked[:1] == [qrel]
                p5 += qrel in ranked
            n = max(len(train), 1)
            key = (p1 / n, p5 / n)
            log.append({"k1": k1, "b": b, "train_p1": key[0], "train_p5_hit": key[1]})
            if key > best_key:
                best_key, best = key, (k1, b)
    return best[0], best[1], {"grid": log, "chosen": {"k1": best[0], "b": best[1]}}


# ---------------------------------------------------------------------------
# Feature-hashed TF-IDF vector space model (stdlib only)
# ---------------------------------------------------------------------------


def _char_ngrams(token: str, lo: int, hi: int) -> list[str]:
    """Character n-grams of a word token wrapped in boundary markers.

    ``<`` / ``>`` markers (the fastText convention) let prefixes and
    suffixes hash differently from word-internal grams, so e.g.
    ``<dur`` (start of "duration") is a distinct feature from ``dur``
    inside "endurance".
    """
    marked = f"<{token}>"
    grams: list[str] = []
    for n in range(lo, hi + 1):
        grams.extend(marked[i : i + n] for i in range(len(marked) - n + 1))
    return grams


class VectorHashIndex:
    """Deterministic feature-hashed TF-IDF vectors with cosine scoring.

    A classical vector-space retrieval baseline (feature hashing a la
    Weinberger et al. 2009), NOT a neural/semantic embedding: passages and
    queries are embedded in a fixed-dim sparse bucket space and compared by
    cosine over tf-idf weights. Features are character n-grams INSIDE word
    tokens (see ``_char_ngrams``): unlike whole-word features this gives
    partial credit on morphological variants ("compounding" vs "compounds"),
    which is exactly where the exact-token BM25 arm is brittle - so the two
    arms make usefully different errors and fusion has something to gain.
    Buckets come from ``zlib.crc32`` (a fixed, documented checksum), never
    the built-in ``hash`` which is salted per process; identical corpus +
    query therefore rank identically across runs and machines.
    """

    def __init__(
        self,
        passages: list[Passage],
        ngram: tuple[int, int] = (3, 5),
        dim: int = 1 << 18,
    ):
        self.passages = passages
        self.ngram = ngram
        self.dim = dim
        n = len(passages)
        raw: list[dict[int, int]] = []
        df: dict[int, int] = {}
        for p in passages:
            counts = self._bucket_counts(p.title + " " + p.text)
            raw.append(counts)
            for bucket in counts:
                df[bucket] = df.get(bucket, 0) + 1
        # Smoothed idf (sklearn-style): ln((N + 1) / (df + 1)) + 1, always
        # positive, and defined (df=0) for query-only buckets.
        self._idf = {b: math.log((n + 1) / (d + 1)) + 1.0 for b, d in df.items()}
        self._vecs: list[dict[int, float]] = []
        for counts in raw:
            vec = {b: c * self._idf[b] for b, c in counts.items()}
            norm = math.sqrt(sum(w * w for w in vec.values()))
            if norm > 0.0:
                vec = {b: w / norm for b, w in vec.items()}
            self._vecs.append(vec)

    def _bucket_counts(self, text: str) -> dict[int, int]:
        lo, hi = self.ngram
        counts: dict[int, int] = {}
        for token in tokenize(text):
            for gram in _char_ngrams(token, lo, hi):
                bucket = zlib.crc32(gram.encode("utf-8")) % self.dim
                counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def _idf_of(self, bucket: int) -> float:
        n = len(self.passages)
        return self._idf.get(bucket, math.log(n + 1.0) + 1.0)

    def score(self, query: str) -> list[tuple[str, float]]:
        """Ranked (pid, cosine), best first; ties broken by pid."""
        q_vec = {b: c * self._idf_of(b) for b, c in self._bucket_counts(query).items()}
        q_norm = math.sqrt(sum(w * w for w in q_vec.values()))
        scores: list[tuple[str, float]] = []
        for i, p in enumerate(self.passages):
            d_vec = self._vecs[i]
            s = 0.0
            if q_norm > 0.0:
                small, big = q_vec, d_vec
                if len(small) > len(big):
                    small, big = big, small
                s = sum(w * big.get(b, 0.0) for b, w in small.items()) / q_norm
            scores.append((p.pid, s))
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores

    def top(self, query: str, k: int = TOP_K) -> list[str]:
        return [pid for pid, _ in self.score(query)[:k]]


def tune_vector_hash(
    passages: list[Passage], train: list[tuple[str, str]]
) -> tuple[VectorHashIndex, dict[str, Any]]:
    """Grid-search n-gram range / dim on train, exactly like tune_bm25.

    Returns the index built with the winning config plus the grid log.
    """
    best: VectorHashIndex | None = None
    best_key = (-1.0, -1.0)
    log = []
    for ngram in VECTOR_HASH_GRID_NGRAM:
        for dim in VECTOR_HASH_GRID_DIM:
            index = VectorHashIndex(passages, ngram=ngram, dim=dim)
            p1 = p5 = 0
            for query, qrel in train:
                ranked = index.top(query, k=5)
                p1 += ranked[:1] == [qrel]
                p5 += qrel in ranked
            n = max(len(train), 1)
            key = (p1 / n, p5 / n)
            log.append(
                {
                    "ngram": list(ngram),
                    "dim": dim,
                    "train_p1": key[0],
                    "train_p5_hit": key[1],
                }
            )
            if key > best_key:
                best_key, best = key, index
    assert best is not None
    return best, {
        "grid": log,
        "chosen": {"ngram": list(best.ngram), "dim": best.dim},
    }


# ---------------------------------------------------------------------------
# Optional dense retriever + cross-encoder (sentence-transformers)
# ---------------------------------------------------------------------------


class DenseRetriever:
    """Bi-encoder retriever; construction raises if the stack is missing."""

    MODEL = "all-MiniLM-L6-v2"

    def __init__(self, passages: list[Passage]):
        from sentence_transformers import SentenceTransformer  # lazy import

        self._np = __import__("numpy")
        self.passages = passages
        self.model = SentenceTransformer(self.MODEL)
        self.variant = {"repr": "title_text", "score": "cosine"}
        self._emb: dict[str, Any] = {}
        for rep in ("title_text", "text"):
            texts = [
                (p.title + ". " + p.text) if rep == "title_text" else p.text
                for p in passages
            ]
            self._emb[rep] = self.model.encode(texts, show_progress_bar=False)

    def _score_matrix(self, q_vec: Any, rep: str, score: str) -> Any:
        np = self._np
        emb = self._emb[rep]
        if score == "dot":
            return emb @ q_vec
        qn = q_vec / (np.linalg.norm(q_vec) + 1e-12)
        en = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12)
        return en @ qn

    def top(
        self, query: str, k: int = TOP_K, variant: dict[str, str] | None = None
    ) -> list[str]:
        v = variant or self.variant
        q_vec = self.model.encode([query], show_progress_bar=False)[0]
        scores = self._score_matrix(q_vec, v["repr"], v["score"])
        order = sorted(
            range(len(self.passages)),
            key=lambda i: (-float(scores[i]), self.passages[i].pid),
        )
        return [self.passages[i].pid for i in order[:k]]

    def tune(self, train: list[tuple[str, str]]) -> dict[str, Any]:
        """Pick repr/scoring variant on the train split."""
        best_key = (-1.0, -1.0)
        log = []
        for rep in ("title_text", "text"):
            for score in ("cosine", "dot"):
                v = {"repr": rep, "score": score}
                p1 = p5 = 0
                for query, qrel in train:
                    ranked = self.top(query, k=5, variant=v)
                    p1 += ranked[:1] == [qrel]
                    p5 += qrel in ranked
                n = max(len(train), 1)
                key = (p1 / n, p5 / n)
                log.append({**v, "train_p1": key[0], "train_p5_hit": key[1]})
                if key > best_key:
                    best_key, self.variant = key, v
        return {"grid": log, "chosen": dict(self.variant)}


class Reranker:
    MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self, passages: list[Passage]):
        from sentence_transformers import CrossEncoder  # lazy import

        self.model = CrossEncoder(self.MODEL)
        self._by_pid = {p.pid: p for p in passages}

    def rerank(self, query: str, pids: list[str]) -> list[str]:
        pairs = [
            (query, self._by_pid[pid].title + ". " + self._by_pid[pid].text)
            for pid in pids
        ]
        scores = self.model.predict(pairs)
        order = sorted(range(len(pids)), key=lambda i: (-float(scores[i]), pids[i]))
        return [pids[i] for i in order]


#: The dense/rerank arms are OPT-IN (``SPEEDRUN_DENSE=1``), never automatic.
#: Rationale: the torch / sentence-transformers / numpy stack is fragile to
#: ABI drift (e.g. numpy 2.x against a torch built for numpy 1.x aborts the
#: interpreter outright rather than raising), and this pipeline is
#: authoring-time-only tooling whose guaranteed path is stdlib BM25 - the
#: plan's C7 already descopes the IR project to a minimal defensible slice.
#: The full-stack eval that DID run on this machine (pinned
#: sentence-transformers==3.4.1 / transformers==4.49.0 against torch 2.3)
#: is archived in eval/archive/retrieval_eval_fullstack_20260703.*; rerun
#: with SPEEDRUN_DENSE=1 in an environment with a compatible stack.
DENSE_OPT_IN_ENV = "SPEEDRUN_DENSE"


def _probe_dense_stack() -> str:
    """Verify torch + sentence-transformers work, in a SUBPROCESS.

    A broken torch install can abort the interpreter (SIGABRT) rather than
    raise, which an in-process try/except cannot contain - so the probe runs
    out of process. Returns "" when usable, else the failure reason.
    """
    import subprocess
    import sys

    code = (
        "import torch, sentence_transformers; "
        "torch.zeros(2) @ torch.zeros(2); print('ok')"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as e:  # timeout etc.
        return f"probe failed: {type(e).__name__}: {e}"
    if r.returncode != 0 or "ok" not in r.stdout:
        tail = (r.stderr or r.stdout).strip().splitlines()
        return f"probe exited {r.returncode}: {tail[-1] if tail else 'no output'}"
    return ""


def try_load_dense(
    passages: list[Passage],
) -> tuple[DenseRetriever | None, Reranker | None, str]:
    """Load the optional dense stack; on failure record why (never fake it).

    Opt-in only: without ``SPEEDRUN_DENSE=1`` the ML stack is never imported
    (see [DENSE_OPT_IN_ENV]); the arm is recorded as unavailable and the
    eval falls back to the stdlib BM25 path.
    """
    import os

    if os.environ.get(DENSE_OPT_IN_ENV) != "1":
        return (
            None,
            None,
            f"dense arm is opt-in (set {DENSE_OPT_IN_ENV}=1); the ML stack "
            "is ABI-fragile on this host, and the guaranteed path is "
            "stdlib BM25 - see eval/archive/ for the full-stack run",
        )
    probe_error = _probe_dense_stack()
    if probe_error:
        return None, None, probe_error
    try:
        dense = DenseRetriever(passages)
        reranker = Reranker(passages)
        return dense, reranker, ""
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal-rank fusion: score(pid) = sum over lists of 1/(k + rank)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return [pid for pid, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))]


# ---------------------------------------------------------------------------
# Qrels, split, eval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Qrel:
    query: str
    qrel_pid: str
    cluster: str
    item_title: str


def qrels_from_items(items: list[dict[str, Any]]) -> list[Qrel]:
    """Synthetic qrels: the item's declared grounding passage is its label."""
    out = []
    for it in items:
        aig = it.get("_aig") or {}
        if aig.get("declared_passage") and aig.get("query"):
            out.append(
                Qrel(
                    query=aig["query"],
                    qrel_pid=aig["declared_passage"],
                    cluster=it["cluster"],
                    item_title=it.get("title", ""),
                )
            )
    return out


def split_by_cluster(
    qrels: list[Qrel], seed: int = SPLIT_SEED
) -> tuple[list[Qrel], list[Qrel], str]:
    """Hold out one whole cluster for eval (provenance wall), fixed seed."""
    clusters = sorted({q.cluster for q in qrels})
    if not clusters:
        return [], [], ""
    held_out = random.Random(seed).choice(clusters)
    train = [q for q in qrels if q.cluster != held_out]
    eval_ = [q for q in qrels if q.cluster == held_out]
    return train, eval_, held_out


@dataclass
class ArmResult:
    name: str
    available: bool
    p_at_1: float | None = None
    p_at_5: float | None = None
    median_latency_ms: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.name,
            "available": self.available,
            "precision_at_1": self.p_at_1,
            "precision_at_5": self.p_at_5,
            "median_latency_ms": self.median_latency_ms,
            "note": self.note,
        }


def _evaluate_arm(
    name: str, retrieve: Callable[[str], list[str]], eval_qrels: list[Qrel]
) -> ArmResult:
    if not eval_qrels:
        return ArmResult(name, False, note="no eval queries")
    hits1 = hits5 = 0
    latencies: list[float] = []
    for q in eval_qrels:
        t0 = time.perf_counter()
        ranked = retrieve(q.query)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        top5 = ranked[:5]
        hits1 += top5[:1] == [q.qrel_pid]
        hits5 += q.qrel_pid in top5
    n = len(eval_qrels)
    return ArmResult(
        name,
        True,
        p_at_1=hits1 / n,
        p_at_5=hits5 / n,
        median_latency_ms=round(statistics.median(latencies), 3),
    )


class GroundingRetriever:
    """The production retriever: best available arm, used to attach sources."""

    def __init__(
        self,
        corpus_dir: str | Path,
        items: list[dict[str, Any]],
        seed: int = SPLIT_SEED,
    ):
        self.passages = load_corpus(corpus_dir)
        self.by_pid = {p.pid: p for p in self.passages}
        self.bm25 = Bm25Index(self.passages)
        self.qrels = qrels_from_items(items)
        self.train, self.eval_, self.held_out_cluster = split_by_cluster(
            self.qrels, seed
        )
        train_pairs = [(q.query, q.qrel_pid) for q in self.train]
        k1, b, self.bm25_tuning = tune_bm25(self.bm25, train_pairs)
        self.bm25.k1, self.bm25.b = k1, b
        self.vhash, self.vhash_tuning = tune_vector_hash(self.passages, train_pairs)
        self.dense, self.reranker, self.dense_error = try_load_dense(self.passages)
        self.dense_tuning: dict[str, Any] = {}
        if self.dense is not None:
            self.dense_tuning = self.dense.tune(train_pairs)
        self.stdlib_arm_selection = self._select_stdlib_arm()

    def _select_stdlib_arm(self) -> dict[str, Any]:
        """Gate the default-env production arm on TRAIN-split metrics only.

        rrf_stdlib replaces bare bm25 for grounding ONLY if it WINS on train:
        >= on BOTH train metrics (P@1, P@5-hit) and strictly better on at
        least one - a pure tie keeps bm25, the status-quo arm (minimal, safe
        change). Computed at construction from the train split - the held-out
        cluster is never consulted (no test leakage), and every input is
        deterministic (crc32 hashing, fixed grids, fixed split seed), so the
        pipeline output stays deterministic.
        """
        rule = (
            "prefer rrf_stdlib over bm25 for grounding iff rrf_stdlib wins on "
            "the train split: >= bm25 on BOTH metrics (P@1, P@5-hit) and "
            "strictly better on at least one; a tie keeps bm25. Decided at "
            "construction time from the train split only - the held-out "
            "cluster is never used (no test leakage)"
        )
        if not self.train:
            return {
                "chosen": "bm25",
                "rule": rule,
                "note": "no train queries - kept bm25",
            }
        bm = _evaluate_arm("bm25_train", self.arm_bm25, self.train)
        fu = _evaluate_arm("rrf_stdlib_train", self.arm_rrf_stdlib, self.train)
        ge_both = fu.p_at_1 >= bm.p_at_1 and fu.p_at_5 >= bm.p_at_5
        wins_one = fu.p_at_1 > bm.p_at_1 or fu.p_at_5 > bm.p_at_5
        prefer = ge_both and wins_one
        if prefer:
            note = "rrf_stdlib won on train - used for grounding"
        elif ge_both:
            note = "rrf_stdlib only TIED bm25 on train - kept bm25 (status quo)"
        else:
            note = "rrf_stdlib did not win on train - kept bm25 (status quo)"
        return {
            "chosen": "rrf_stdlib" if prefer else "bm25",
            "rule": rule,
            "note": note,
            "train_bm25": {"p1": bm.p_at_1, "p5_hit": bm.p_at_5},
            "train_rrf_stdlib": {"p1": fu.p_at_1, "p5_hit": fu.p_at_5},
        }

    # -- arms ---------------------------------------------------------------
    def arm_bm25(self, query: str) -> list[str]:
        return self.bm25.top(query)

    def arm_vector_hash(self, query: str) -> list[str]:
        return self.vhash.top(query)

    def arm_rrf_stdlib(self, query: str) -> list[str]:
        return rrf_fuse([self.bm25.top(query), self.vhash.top(query)])

    def arm_dense(self, query: str) -> list[str]:
        assert self.dense is not None
        return self.dense.top(query)

    def arm_rrf(self, query: str) -> list[str]:
        assert self.dense is not None
        return rrf_fuse([self.bm25.top(query), self.dense.top(query)])

    def arm_rrf_rerank(self, query: str) -> list[str]:
        assert self.dense is not None and self.reranker is not None
        fused = self.arm_rrf(query)
        return self.reranker.rerank(query, fused[:RERANK_N]) + fused[RERANK_N:]

    def best_arm(self) -> tuple[str, Callable[[str], list[str]]]:
        if self.dense is not None and self.reranker is not None:
            return "rrf_rerank", self.arm_rrf_rerank
        if self.stdlib_arm_selection["chosen"] == "rrf_stdlib":
            return "rrf_stdlib", self.arm_rrf_stdlib
        return "bm25", self.arm_bm25

    # -- grounding ------------------------------------------------------------
    def ground_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Attach the named source (doc + loc + passage) to one item."""
        aig = item.get("_aig") or {}
        query = aig.get("query") or item.get("title", "")
        arm_name, arm = self.best_arm()
        top = arm(query)
        pid = top[0] if top else ""
        passage = self.by_pid.get(pid)
        if passage is None:
            return item
        item["source"] = {
            "doc": passage.doc,
            "loc": f"#{pid.split('#', 1)[1]}",
            "passage": passage.text[:500],
        }
        aig["retrieved_passage"] = pid
        aig["grounding_arm"] = arm_name
        aig["grounding_agrees_with_declared"] = pid == aig.get("declared_passage")
        return item

    # -- eval -----------------------------------------------------------------
    def evaluate(self) -> dict[str, Any]:
        arms: list[ArmResult] = [
            _evaluate_arm("bm25_tuned", self.arm_bm25, self.eval_),
            _evaluate_arm("vector_hash_tuned", self.arm_vector_hash, self.eval_),
            _evaluate_arm("rrf_stdlib", self.arm_rrf_stdlib, self.eval_),
        ]
        if self.dense is not None:
            arms.append(_evaluate_arm("dense_tuned", self.arm_dense, self.eval_))
            arms.append(_evaluate_arm("rrf", self.arm_rrf, self.eval_))
            if self.reranker is not None:
                arms.append(
                    _evaluate_arm("rrf_rerank", self.arm_rrf_rerank, self.eval_)
                )
        else:
            note = (
                "dense retriever unavailable in this environment "
                f"({self.dense_error}); arm documented as unavailable - "
                "numbers are NOT faked"
            )
            arms.append(ArmResult("dense_tuned", False, note=note))
            arms.append(ArmResult("rrf", False, note="requires the dense arm"))
            arms.append(ArmResult("rrf_rerank", False, note="requires the dense arm"))

        by_name = {a.name: a for a in arms}
        claim = _honest_claim(by_name, len(self.eval_))
        return {
            "meta": {
                "qrels": (
                    "SYNTHETIC qrels: each item's generator-declared grounding "
                    "passage is its relevance label. Self-referential qrels are "
                    "weaker than human relevance judgments; treat the numbers "
                    "as a consistency check, not an IR benchmark."
                ),
                "split": (
                    "split by cluster (fixed seed): tuning uses the train "
                    f"clusters; eval uses the held-out cluster "
                    f"'{self.held_out_cluster}' (provenance wall: no eval qrel "
                    "passage is tuned against a paraphrase of the same item)"
                ),
                "precision_at_5_note": (
                    "with a single relevant passage per query, precision@5 is "
                    "reported as hit-rate@5 (success@5), the standard practice"
                ),
                "n_train_queries": len(self.train),
                "n_eval_queries": len(self.eval_),
                "n_passages": len(self.passages),
                "bm25_tuning": self.bm25_tuning,
                "vector_hash_tuning": self.vhash_tuning,
                "dense_tuning": self.dense_tuning or {"note": self.dense_error},
                "rrf_k": RRF_K,
                "rerank_top_n": RERANK_N,
                "stdlib_best_arm": self.stdlib_arm_selection,
                "fullstack_archive": (
                    "the full-stack (torch) side-by-side from 2026-07-03 is "
                    "archived in eval/archive/retrieval_eval_fullstack_20260703.md"
                ),
            },
            "arms": [a.as_dict() for a in arms],
            "honest_claim": claim,
        }


#: Verbatim numbers from the committed full-stack run (different session,
#: pinned torch / sentence-transformers stack) - quoted, never re-derived,
#: when the default-env stdlib fusion does not win outright.
ARCHIVE_FALLBACK_NOTE = (
    "Fallback committed result: the archived full-stack run "
    "(eval/archive/retrieval_eval_fullstack_20260703.md, session of "
    "2026-07-03 on the pinned torch / sentence-transformers stack) reported "
    "rrf_rerank P@1 0.727 / P@5 1.000 beating tuned BM25 (0.500 / 0.955) "
    "and tuned dense (0.455 / 1.000) on the same split - quoted verbatim "
    "from that DIFFERENT session and stack, not reproduced in this "
    "environment."
)


def _honest_claim(by_name: dict[str, ArmResult], n_eval: int) -> str:
    caveat = (
        f"on precision@1/@5 over the held-out cluster (N={n_eval} queries - "
        "small-N caveat: differences of one or two queries flip these "
        "comparisons; the synthetic self-referential qrels further weaken "
        "the claim)"
    )
    rr = by_name.get("rrf_rerank")
    bm = by_name.get("bm25_tuned")
    de = by_name.get("dense_tuned")
    if rr and rr.available and de and de.available and bm and bm.available:
        beats_bm = (rr.p_at_1, rr.p_at_5) > (bm.p_at_1, bm.p_at_5)
        beats_de = (rr.p_at_1, rr.p_at_5) > (de.p_at_1, de.p_at_5)
        ties = (rr.p_at_1, rr.p_at_5) == (bm.p_at_1, bm.p_at_5) or (
            rr.p_at_1,
            rr.p_at_5,
        ) == (de.p_at_1, de.p_at_5)
        if beats_bm and beats_de:
            verdict = (
                "RRF+rerank beat BOTH tuned BM25 and tuned dense at the same cutoff"
            )
        elif ties and not (beats_bm and beats_de):
            verdict = (
                "RRF+rerank TIED one or both tuned baselines (did not strictly "
                "beat both) at the same cutoff"
            )
        else:
            verdict = "RRF+rerank did NOT beat both tuned baselines at the same cutoff"
        return f"{verdict} {caveat}."

    # Default (stdlib-only) environment: adjudicate the fusion arm that is
    # always runnable against the keyword and vector baselines.
    fu = by_name.get("rrf_stdlib")
    vh = by_name.get("vector_hash_tuned")
    if not (fu and fu.available and vh and vh.available and bm and bm.available):
        return (
            "the retrieval side-by-side could NOT be adjudicated: no arm "
            "produced eval numbers in this environment. The [R21] claim is "
            "therefore not made."
        )
    beats_bm = (fu.p_at_1, fu.p_at_5) > (bm.p_at_1, bm.p_at_5)
    beats_vh = (fu.p_at_1, fu.p_at_5) > (vh.p_at_1, vh.p_at_5)
    if beats_bm and beats_vh:
        return (
            "rrf_stdlib (RRF fusion of tuned BM25 + tuned hashed TF-IDF, "
            "stdlib only) beat BOTH the keyword baseline (tuned BM25) and "
            "the vector baseline (tuned feature-hashed TF-IDF cosine) at "
            f"the same cutoff {caveat}."
        )
    ties = (fu.p_at_1, fu.p_at_5) == (bm.p_at_1, bm.p_at_5) or (
        fu.p_at_1,
        fu.p_at_5,
    ) == (vh.p_at_1, vh.p_at_5)
    if ties:
        verdict = (
            "rrf_stdlib TIED one or both stdlib baselines (did not strictly "
            "beat both) at the same cutoff"
        )
    else:
        verdict = "rrf_stdlib did NOT beat both stdlib baselines at the same cutoff"
    return f"{verdict} {caveat}. {ARCHIVE_FALLBACK_NOTE}"


def write_eval_reports(
    evaluation: dict[str, Any], json_path: str | Path, md_path: str | Path
) -> None:
    jp = Path(json_path)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(evaluation, indent=1) + "\n")

    lines = [
        "# Retrieval-for-grounding eval [R21]",
        "",
        evaluation["meta"]["qrels"],
        "",
        evaluation["meta"]["split"] + ".",
        "",
        f"- passages: {evaluation['meta']['n_passages']}",
        f"- train queries: {evaluation['meta']['n_train_queries']}",
        f"- eval queries (held-out cluster): {evaluation['meta']['n_eval_queries']}",
        f"- BM25 tuned: {evaluation['meta']['bm25_tuning']['chosen']}",
        f"- vector_hash tuned: {evaluation['meta']['vector_hash_tuning']['chosen']}"
        " (feature-hashed char-n-gram TF-IDF + cosine, stdlib; not a neural embedding)",
        f"- dense tuned: {evaluation['meta']['dense_tuning'].get('chosen', evaluation['meta']['dense_tuning'])}",
        f"- fusion: RRF(k={evaluation['meta']['rrf_k']}), rerank top-{evaluation['meta']['rerank_top_n']}",
        f"- production grounding arm (default env): {evaluation['meta']['stdlib_best_arm']['chosen']}"
        f" ({evaluation['meta']['stdlib_best_arm'].get('note', '')})"
        f" - rule: {evaluation['meta']['stdlib_best_arm']['rule']}",
        "",
        "| arm | available | P@1 | P@5 (hit) | median latency (ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in evaluation["arms"]:
        p1 = "-" if a["precision_at_1"] is None else f"{a['precision_at_1']:.3f}"
        p5 = "-" if a["precision_at_5"] is None else f"{a['precision_at_5']:.3f}"
        lat = "-" if a["median_latency_ms"] is None else f"{a['median_latency_ms']:.2f}"
        note = f" {a['note']}" if a["note"] else ""
        lines.append(
            f"| {a['arm']} | {'yes' if a['available'] else 'NO'} | {p1} | {p5} | {lat} |{note}"
        )
    lines += [
        "",
        f"**Honest claim:** {evaluation['honest_claim']}",
        "",
        evaluation["meta"]["precision_at_5_note"] + ".",
        "",
        evaluation["meta"]["fullstack_archive"] + ".",
        "",
    ]
    mp = Path(md_path)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Scoped eval-only entry point
# ---------------------------------------------------------------------------


def _pipeline_qrel_items() -> list[dict[str, Any]]:
    """The exact item set run_pipeline.py builds qrels from.

    Parameterized generator items (default seed) plus the mock-backend LLM
    drafts, via the same helpers run_pipeline.py uses - so the qrels, split
    and tuning here match a full pipeline run query-for-query.
    """
    from aig import generators, models
    from aig import run_pipeline as rp

    items = generators.generate_all(seed=generators.DEFAULT_SEED)
    llm_path = models.make_llm_path("mock", k_samples=3)
    for cluster, topic, concept, mids, passage in rp.LLM_CONCEPTS:
        draft, _audit = llm_path.generate_validated(topic, cluster, concept, mids)
        if draft is not None:
            items.append(
                rp._llm_item_from_draft(draft, cluster, topic, passage, "llm:mock")
            )
    return items


def main(argv: list[str] | None = None) -> int:
    """Regenerate ONLY eval/retrieval_eval.{json,md} (scoped refresh).

    Same corpus, items, seed and eval path as run_pipeline.py, but nothing
    else is rewritten - items/generated.jsonl, validation_report.json and
    the confusability artifacts are left untouched (minimal churn when only
    the retrieval side-by-side needs refreshing).
    """
    import argparse

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--eval-dir", default=str(_SPEEDRUN_DIR / "eval"))
    ap.add_argument("--corpus-dir", default=str(_SPEEDRUN_DIR / "corpus"))
    args = ap.parse_args(argv)

    grounder = GroundingRetriever(args.corpus_dir, _pipeline_qrel_items())
    evaluation = grounder.evaluate()
    eval_dir = Path(args.eval_dir)
    write_eval_reports(
        evaluation, eval_dir / "retrieval_eval.json", eval_dir / "retrieval_eval.md"
    )
    print(
        json.dumps(
            {
                "wrote": [
                    str(eval_dir / "retrieval_eval.json"),
                    str(eval_dir / "retrieval_eval.md"),
                ],
                "stdlib_best_arm": evaluation["meta"]["stdlib_best_arm"]["chosen"],
                "retrieval_honest_claim": evaluation["honest_claim"],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
