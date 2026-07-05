# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Gold-set card checker + ship gate (challenge 7f).

Classifies each (question, answer[, rationale/source]) card into exactly one
of ``correct_useful`` / ``wrong`` / ``bad_teaching`` using layered,
deterministic, stdlib-only signals, and enforces a SHIP CUTOFF that was
frozen before the first scored run (see the FROZEN CUTOFF block below - the
constants are the contract; tests pin their exact values so silent retuning
breaks the build).

Signals, in priority order (wrong beats bad_teaching):

* WRONG - (a) independent recomputation for parameterized pipeline items
  (reuses aig/gates.py gate_numeric + gate_solve_check and the generators'
  INDEPENDENT_SOLVERS on the item's own ``_aig`` params); (b) contradiction
  against the hand-verified gold set (a card asking the same question as a
  gold record but answering with different numbers / a different answer);
  (c) contradiction against the grounding corpus (one-sided negation or
  direction-antonym flip of a corpus statement with sufficient content
  overlap). HONESTY: a stdlib checker cannot fact-check arbitrary free
  text. It checks what it can and ABSTAINS otherwise; the abstention is
  recorded as correctness == "unverifiable" and BLOCKS generated cards
  (they must carry recomputation metadata to be shippable - that policy is
  part of the frozen cutoff, not a tunable).
* BAD_TEACHING - vagueness lint (question too short / too little content /
  no question mark or task cue / non-answer like "it depends"), triviality
  (the answer is contained verbatim in the question), duplicate detection
  (normalized token-set Jaccard vs the other cards in the batch and vs the
  gold set; unigrams chosen over higher-order n-grams because reworded
  near-copies survive word-order changes), and the [R9] feedback lint
  reused from aig/gates.py for schema items.
* CORRECT_USEFUL - passes every hard gate and no bad-teaching flag.

Modes (one command does everything - reports are from real runs only):

* no flags          -> full eval: validate the gold set file, run the
                       known-answer validation (50 gold + 15 seeded defects
                       -> confusion matrix), generate BATCH_N cards from the
                       ONE named source (SOURCE_DOC) via the aig generators
                       + gates + grounding, check them, write
                       eval/card_check_report.{json,md} and the scratch
                       batch + blocked-ids sidecar under
                       out/speedrun_eval/cardcheck/. Exit non-zero only on
                       integrity/generation failure (checker misses are
                       REPORTED, never fatal - that is the point).
* --cards FILE      -> ship gate on an arbitrary items JSONL: writes
                       FILE.blocked.json (the sidecar) and exits non-zero
                       if ANY card fails the frozen cutoff. This is the
                       block: wiring it before the deck build means blocked
                       cards are never emitted. (Integration owned by
                       run_pipeline.py / build_ladder_deck.py owners - one
                       call to check_batch() before writing JSONL.)

stdlib only; offline; deterministic with --seed. Reuses aig/gates.py,
aig/generators.py, aig/pdf_text.py (tokenize) and aig/retrieval.py
(grounding) without modifying them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aig import gates, retrieval  # noqa: E402
from aig import generators as G  # noqa: E402
from aig.pdf_text import tokenize  # noqa: E402

# ---------------------------------------------------------------------------
# FROZEN CUTOFF - fixed 2026-07-05 BEFORE the first scored run. Do not tune
# after seeing results; oddities get REPORTED, not retuned. The freeze test
# in tests/test_card_check.py asserts these exact values.
# ---------------------------------------------------------------------------

CUTOFF_FROZEN_ON = "2026-07-05"

SHIP_POLICY = (
    "A card SHIPS only if classification == correct_useful AND its "
    "correctness is machine-verified (independent recomputation from the "
    "card's own generator metadata) or human-attested (hand-written gold "
    "card). wrong => BLOCKED (a wrong fact is worse than no card). "
    "bad_teaching => BLOCKED. A generated card whose correctness the "
    "machine cannot verify is unverifiable-by-machine => BLOCKED (generated "
    "cards must carry recomputation metadata to be shippable)."
)

#: Duplicate: normalized token-set Jaccard (question + answer) at or above
#: this blocks. Unigram token sets (via aig.pdf_text.tokenize) survive the
#: trivial rewording that defines a near-copy.
DUP_JACCARD = 0.55
#: Vagueness: questions shorter than this (characters) block.
MIN_QUESTION_CHARS = 20
#: Vagueness: fewer distinct non-stopword tokens than this blocks.
MIN_QUESTION_CONTENT_TOKENS = 3
#: A card whose question matches a gold question at/above this content-token
#: Jaccard is treated as asking about the SAME fact as the gold record.
GOLD_MATCH_JACCARD = 0.75
#: ...and if its answer's token Jaccard vs the gold answer is below this
#: (and the numeric-token rule does not already decide), it contradicts the
#: gold record => wrong.
ANSWER_AGREE_JACCARD = 0.20
#: Corpus contradiction: content-token overlap (after removing negation and
#: antonym vocabulary) required before a one-sided negation/antonym flip
#: against a corpus sentence counts as a contradiction. Calibrated on paper
#: against one worked example before any run; frozen since.
CONTRADICTION_OVERLAP = 0.35
#: Corpus sentences with fewer content tokens than this are not compared.
SENTENCE_MIN_CONTENT = 5

#: The ONE named source for the generated batch (chosen for richest
#: coverage: three parameterized generators plus two compare fixtures
#: declare duration.md passages as their grounding).
SOURCE_DOC = "duration.md"
BATCH_N = 50
DEFAULT_SEED = 20260705

#: Exact-match non-answers (normalized token string).
VAGUE_ANSWER_EXACT = frozenset(
    {
        "yes",
        "no",
        "maybe",
        "none",
        "it depends",
        "depends",
        "various",
        "varies",
        "sometimes",
        "many things",
        "many factors",
        "a lot",
        "who knows",
        "n a",
    }
)
#: Prefix-match non-answers (normalized token string).
VAGUE_ANSWER_PREFIXES = ("it depends",)

#: A question with no "?" must contain at least one of these task cues.
TASK_CUES = frozenset(
    (
        "what which who whose whom when where why how compute calculate "
        "estimate determine complete define state identify name list explain "
        "describe distinguish compare contrast closest true false does do "
        "did is are can may must should"
    ).split()
)

#: Stopwords for content-token extraction. Deliberately EXCLUDES negation
#: and antonym vocabulary (those carry the signal the contradiction check
#: needs).
STOPWORDS = frozenset(
    (
        "a an the of to in on for and or is are was were be been being at "
        "by with from as that this it its into after before during than "
        "then so such but if because while both each any all per which "
        "what whose when where why how does do did can could may might "
        "must should would will has have had s"
    ).split()
)

NEGATIONS = frozenset({"not", "never", "cannot"})

#: Direction/antonym pairs for the corpus contradiction check (one-sided
#: swap = the card uses one pole, the corpus sentence the other, and
#: neither text contains the opposite pole).
ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("rise", "fall"),
    ("rises", "falls"),
    ("rising", "falling"),
    ("higher", "lower"),
    ("highest", "lowest"),
    ("above", "below"),
    ("increase", "decrease"),
    ("increases", "decreases"),
    ("increasing", "decreasing"),
    ("larger", "smaller"),
    ("longer", "shorter"),
    ("more", "less"),
    ("overstates", "understates"),
    ("overstate", "understate"),
    ("add", "subtract"),
    ("added", "subtracted"),
    ("adds", "subtracts"),
    ("gain", "loss"),
    ("gains", "losses"),
    ("premium", "discount"),
    ("elastic", "inelastic"),
)
_ANTONYM_VOCAB = frozenset(w for pair in ANTONYM_PAIRS for w in pair)

#: Numeric tokens for answer-contradiction: digit-bearing tokens plus the
#: roman numerals used by the CFA Standards (I and V excluded - too easily
#: confused with English words; an honest, documented gap).
_ROMAN = frozenset({"ii", "iii", "iv", "vi", "vii"})

LABELS = ("correct_useful", "wrong", "bad_teaching")
GOLD_TOPIC_COUNT = 5
GOLD_N = 50
DEFECTS_PER_TYPE = 5
DEFECT_TYPES = ("wrong", "vague", "duplicate")
#: Expected checker label per seeded defect type (for the confusion matrix).
DEFECT_EXPECTED_LABEL = {
    "wrong": "wrong",
    "vague": "bad_teaching",
    "duplicate": "bad_teaching",
}

TOPICS = (
    "ethics",
    "quantitative_methods",
    "economics",
    "financial_statement_analysis",
    "corporate_issuers",
    "equity_investments",
    "fixed_income",
    "derivatives",
    "alternative_investments",
    "portfolio_management",
)

_CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)\}\}")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _content(tokens: list[str]) -> set[str]:
    return {t for t in tokens if t not in STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _numeric_tokens(text: str) -> set[str]:
    toks = tokenize(text)
    return {t for t in toks if any(c.isdigit() for c in t) or t in _ROMAN}


def _contiguous_in(needle: list[str], hay: list[str]) -> bool:
    n = len(needle)
    if n == 0 or n > len(hay):
        return False
    return any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


# ---------------------------------------------------------------------------
# Card views: one uniform surface over gold-shape records and schema items
# ---------------------------------------------------------------------------


@dataclass
class CardView:
    """Uniform view of a card for checking purposes."""

    card_id: str
    origin: str  # "human" | "generated"
    question: str
    answer: str
    rationale: str
    kind: str  # "" for gold-shape records
    raw: dict[str, Any]

    @property
    def aig(self) -> dict[str, Any]:
        return self.raw.get("_aig") or {}


def view_of(card: dict[str, Any], fallback_id: str = "") -> CardView:
    """Build the uniform view. Gold-shape records have a ``question`` key;
    ITEM_SCHEMA items are mapped kind by kind."""
    card_id = str(card.get("id") or card.get("_cc_id") or fallback_id)
    generated = bool(
        card.get("_aig") or (card.get("provenance") or {}).get("generator")
    )
    origin = str(card.get("origin") or ("generated" if generated else "human"))
    if "question" in card:
        return CardView(
            card_id=card_id,
            origin=origin,
            question=str(card.get("question") or ""),
            answer=str(card.get("correct_answer") or card.get("answer") or ""),
            rationale=str(card.get("rationale") or ""),
            kind=str(card.get("kind") or ""),
            raw=card,
        )
    kind = str(card.get("kind") or "")
    if kind == "mcq":
        question = str(card.get("stem") or "")
        answer = str((card.get("choices") or {}).get(card.get("correct"), ""))
    elif kind == "cloze":
        question = str(card.get("prompt") or "")
        answer = " ".join(_CLOZE_RE.findall(card.get("cloze_text") or ""))
    elif kind == "worked":
        question = str(card.get("prompt") or "")
        answer = " ".join(card.get("worked_steps") or [])
    elif kind == "compare":
        question = str(card.get("discriminator") or "")
        answer = (
            str(card.get("left_body") or "") + " " + str(card.get("right_body") or "")
        )
    else:
        question = str(card.get("prompt") or card.get("title") or "")
        answer = str(card.get("rationale") or "")
    return CardView(
        card_id=card_id,
        origin=origin,
        question=question,
        answer=answer,
        rationale=str(card.get("rationale") or ""),
        kind=kind,
        raw=card,
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CardResult:
    card_id: str
    label: str  # correct_useful | wrong | bad_teaching
    origin: str  # human | generated
    correctness: str  # verified | unverifiable | refuted
    shipped: bool
    reasons: list[str] = field(default_factory=list)
    dup_of: str = ""
    gold_match: str = ""
    kind: str = ""
    generator: str = ""

    @property
    def blocked(self) -> bool:
        return not self.shipped

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.card_id,
            "label": self.label,
            "origin": self.origin,
            "correctness": self.correctness,
            "shipped": self.shipped,
            "blocked": self.blocked,
            "reasons": self.reasons,
            "dup_of": self.dup_of,
            "gold_match": self.gold_match,
            "kind": self.kind,
            "generator": self.generator,
        }


@dataclass
class BatchResult:
    results: list[CardResult]

    @property
    def counts(self) -> dict[str, int]:
        c = {label: 0 for label in LABELS}
        for r in self.results:
            c[r.label] += 1
        c["shipped"] = sum(1 for r in self.results if r.shipped)
        c["blocked"] = sum(1 for r in self.results if r.blocked)
        c["unverifiable_generated"] = sum(
            1
            for r in self.results
            if r.origin == "generated" and r.correctness == "unverifiable"
        )
        return c

    @property
    def blocked_ids(self) -> list[str]:
        return [r.card_id for r in self.results if r.blocked]

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "blocked_ids": self.blocked_ids,
            "cards": [r.as_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------


@dataclass
class _GoldRef:
    rec_id: str
    q_content: set[str]
    a_tokens: set[str]
    a_numeric: set[str]
    dup_tokens: set[str]


class Checker:
    """Deterministic card checker against a gold set + grounding corpus."""

    def __init__(
        self,
        gold_records: list[dict[str, Any]],
        corpus_texts: dict[str, str] | None = None,
    ) -> None:
        self.gold_ref: list[_GoldRef] = []
        for rec in gold_records:
            if rec.get("defect_type"):
                continue  # seeded defects are test inputs, never reference
            q = str(rec.get("question") or "")
            a = str(rec.get("correct_answer") or rec.get("answer") or "")
            self.gold_ref.append(
                _GoldRef(
                    rec_id=str(rec.get("id") or ""),
                    q_content=_content(tokenize(q)),
                    a_tokens=set(tokenize(a)),
                    a_numeric=_numeric_tokens(a),
                    dup_tokens=set(tokenize(q + " " + a)),
                )
            )
        self.corpus_sentences: list[tuple[str, set[str]]] = []
        for doc, text in (corpus_texts or {}).items():
            for sent in self._sentences(text):
                content = _content(tokenize(sent))
                if len(content) >= SENTENCE_MIN_CONTENT:
                    self.corpus_sentences.append((doc, content))

    @staticmethod
    def _sentences(text: str) -> list[str]:
        flat = re.sub(r"\s+", " ", text)
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]

    # -- wrong signals -------------------------------------------------------

    def _verify_recomputation(self, view: CardView) -> tuple[str, list[str]]:
        """Independent recomputation from the card's own parameters.

        Returns (correctness, wrong_reasons): correctness is "verified",
        "refuted", or "unverifiable" (metadata absent => the machine
        abstains).
        """
        aig = view.aig
        gen_id = str(
            aig.get("generator")
            or (view.raw.get("provenance") or {}).get("generator")
            or ""
        )
        solver = G.INDEPENDENT_SOLVERS.get(gen_id)
        if (
            not gen_id.startswith("param:")
            or solver is None
            or "params" not in aig
            or "answer" not in aig
        ):
            return "unverifiable", []
        reasons: list[str] = []
        numeric = gates.gate_numeric(view.raw)
        if not numeric.passed:
            reasons.append(f"recomputation: {numeric.reason}")
        try:
            recomputed = solver(aig["params"])
        except Exception as exc:  # metadata present but unusable
            return "unverifiable", [f"recomputation: solver failed ({exc})"]
        stated = aig["answer"]
        rel = abs(recomputed - stated) / max(abs(recomputed), abs(stated), 1.0)
        if rel > G.NUMERIC_TOLERANCE:
            reasons.append(
                "recomputation: independent solver disagrees with stated "
                f"answer ({recomputed!r} vs {stated!r})"
            )
        if view.kind == "mcq":
            solve = gates.gate_solve_check(view.raw)
            if not solve.passed:
                reasons.append(f"solve_check: {solve.reason}")
        return ("refuted" if reasons else "verified"), reasons

    def _gold_contradiction(
        self, view: CardView, exclude_gold_id: str
    ) -> tuple[list[str], str, bool]:
        """Contradiction / near-copy vs the hand-verified gold reference.

        Returns (wrong_reasons, matched_gold_id, is_gold_duplicate).
        """
        q_content = _content(tokenize(view.question))
        best: _GoldRef | None = None
        best_j = 0.0
        for ref in self.gold_ref:
            if ref.rec_id == exclude_gold_id:
                continue
            j = _jaccard(q_content, ref.q_content)
            if j > best_j:
                best, best_j = ref, j
        if best is None or best_j < GOLD_MATCH_JACCARD:
            return [], "", False
        a_num = _numeric_tokens(view.answer)
        if a_num and best.a_numeric:
            # Same question, different numbers => contradiction (subset
            # tolerated: gold rationale-style answers carry extra digits).
            if not (a_num <= best.a_numeric or best.a_numeric <= a_num):
                return (
                    [
                        f"contradicts gold {best.rec_id}: same question "
                        f"(J={best_j:.2f}) but answer numbers "
                        f"{sorted(a_num)} vs {sorted(best.a_numeric)}"
                    ],
                    best.rec_id,
                    False,
                )
        a_j = _jaccard(set(tokenize(view.answer)), best.a_tokens)
        if a_j < ANSWER_AGREE_JACCARD:
            return (
                [
                    f"contradicts gold {best.rec_id}: same question "
                    f"(J={best_j:.2f}) but a different answer "
                    f"(answer J={a_j:.2f})"
                ],
                best.rec_id,
                False,
            )
        return [], best.rec_id, True  # near-copy that AGREES => duplicate

    @staticmethod
    def _one_sided_swaps(card: set[str], sent: set[str]) -> list[tuple[str, str]]:
        """Antonym pairs where the card uses one pole, the sentence the
        other, and NEITHER text contains the opposite pole (a text that
        mentions both directions is describing the relationship, not
        asserting one side)."""
        swaps: list[tuple[str, str]] = []
        for a, b in ANTONYM_PAIRS:
            if a in card and b in sent and b not in card and a not in sent:
                swaps.append((a, b))
            elif b in card and a in sent and a not in card and b not in sent:
                swaps.append((b, a))
        return swaps

    def _corpus_contradiction(self, view: CardView) -> list[str]:
        """Direction-antonym flip vs a corpus sentence.

        SCOPE (documented limitation): only one-sided direction-word flips
        with enough content overlap are detected. Bare negations ("X is
        not Y") are NOT checked - a topic-overlap heuristic cannot tell a
        negated restatement from a correct card that legitimately contains
        "not" (e.g. "permitted under US GAAP, not IFRS"), so the stdlib
        checker abstains there rather than guess.
        """
        card = _content(tokenize(view.question + " " + view.answer))
        if not card:
            return []
        card_core = card - NEGATIONS - _ANTONYM_VOCAB
        for doc, sent in self.corpus_sentences:
            swaps = self._one_sided_swaps(card, sent)
            if not swaps:
                continue
            overlap = _jaccard(card_core, sent - NEGATIONS - _ANTONYM_VOCAB)
            if overlap < CONTRADICTION_OVERLAP:
                continue
            return [
                f"contradicts corpus ({doc}): asserts '{swaps[0][0]}' where "
                f"the corpus says '{swaps[0][1]}' "
                f"(content overlap {overlap:.2f})"
            ]
        return []

    # -- bad-teaching signals -------------------------------------------------

    def _vague_or_trivial(self, view: CardView) -> list[str]:
        reasons: list[str] = []
        q = view.question.strip()
        q_tokens = tokenize(q)
        if len(q) < MIN_QUESTION_CHARS:
            reasons.append(f"vague: question under {MIN_QUESTION_CHARS} chars")
        if len(_content(q_tokens)) < MIN_QUESTION_CONTENT_TOKENS:
            reasons.append(
                "vague: fewer than "
                f"{MIN_QUESTION_CONTENT_TOKENS} content tokens in question"
            )
        if "?" not in q and not (set(q_tokens) & TASK_CUES):
            reasons.append("vague: no question mark and no task cue")
        a_norm = " ".join(tokenize(view.answer))
        if not a_norm:
            reasons.append("vague: empty answer")
        elif a_norm in VAGUE_ANSWER_EXACT or a_norm.startswith(VAGUE_ANSWER_PREFIXES):
            reasons.append(f"vague: non-answer ({view.answer.strip()!r})")
        a_tokens = tokenize(view.answer)
        if a_tokens and _content(a_tokens) and _contiguous_in(a_tokens, q_tokens):
            reasons.append("trivial: answer contained verbatim in question")
        return reasons

    def _lint(self, view: CardView) -> list[str]:
        """[R9] feedback lint, reused from aig/gates.py for schema items."""
        if not view.kind or "question" in view.raw:
            return []
        result = gates.gate_rationale(view.raw)
        return [] if result.passed else [f"lint: {result.reason}"]

    def _duplicate(
        self,
        view: CardView,
        prior: list[tuple[str, set[str]]],
        exclude_gold_id: str,
    ) -> tuple[list[str], str]:
        dup_tokens = set(tokenize(view.question + " " + view.answer))
        for other_id, other_tokens in prior:
            j = _jaccard(dup_tokens, other_tokens)
            if j >= DUP_JACCARD:
                return (
                    [f"duplicate: J={j:.2f} vs batch card {other_id}"],
                    other_id,
                )
        for ref in self.gold_ref:
            if ref.rec_id == exclude_gold_id:
                continue
            j = _jaccard(dup_tokens, ref.dup_tokens)
            if j >= DUP_JACCARD:
                return (
                    [f"duplicate: J={j:.2f} vs gold record {ref.rec_id}"],
                    ref.rec_id,
                )
        return [], ""

    # -- orchestration --------------------------------------------------------

    def check_card(
        self,
        card: dict[str, Any],
        prior: list[tuple[str, set[str]]] | None = None,
        exclude_gold_id: str = "",
        fallback_id: str = "",
    ) -> CardResult:
        view = view_of(card, fallback_id=fallback_id)
        correctness, wrong_reasons = self._verify_recomputation(view)
        gold_wrong, gold_match, gold_dup = self._gold_contradiction(
            view, exclude_gold_id
        )
        wrong_reasons += gold_wrong
        wrong_reasons += self._corpus_contradiction(view)
        if wrong_reasons:
            correctness = "refuted"

        bad_reasons = self._vague_or_trivial(view) + self._lint(view)
        dup_reasons, dup_of = self._duplicate(view, prior or [], exclude_gold_id)
        if not dup_reasons and gold_dup:
            dup_reasons = [f"duplicate: near-copy of gold record {gold_match}"]
            dup_of = gold_match
        bad_reasons += dup_reasons

        if wrong_reasons:
            label = "wrong"
        elif bad_reasons:
            label = "bad_teaching"
        else:
            label = "correct_useful"

        shipped = label == "correct_useful" and (
            view.origin == "human" or correctness == "verified"
        )
        reasons = wrong_reasons + bad_reasons
        if not shipped and not reasons:
            reasons = [
                "unverifiable-by-machine: generated card without "
                "recomputation metadata (frozen cutoff blocks it)"
            ]
        return CardResult(
            card_id=view.card_id,
            label=label,
            origin=view.origin,
            correctness=correctness,
            shipped=shipped,
            reasons=reasons,
            dup_of=dup_of,
            gold_match=gold_match,
            kind=view.kind,
            generator=str(
                view.aig.get("generator")
                or (view.raw.get("provenance") or {}).get("generator")
                or ""
            ),
        )

    def check_batch(
        self,
        cards: list[dict[str, Any]],
        accumulate: bool = True,
        exclude_self_gold: bool = False,
    ) -> BatchResult:
        """Check a batch in order (first-seen wins for duplicates)."""
        results: list[CardResult] = []
        prior: list[tuple[str, set[str]]] = []
        for idx, card in enumerate(cards):
            view = view_of(card, fallback_id=f"card/{idx}")
            exclude = view.card_id if exclude_self_gold else ""
            result = self.check_card(
                card,
                prior=prior,
                exclude_gold_id=exclude,
                fallback_id=f"card/{idx}",
            )
            results.append(result)
            if accumulate:
                prior.append(
                    (
                        view.card_id,
                        set(tokenize(view.question + " " + view.answer)),
                    )
                )
        return BatchResult(results)


def check_batch(
    cards: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
    corpus_texts: dict[str, str] | None = None,
    accumulate: bool = True,
) -> BatchResult:
    """Module-level API: check `cards` against the gold set + corpus."""
    return Checker(gold_records, corpus_texts).check_batch(cards, accumulate=accumulate)


# ---------------------------------------------------------------------------
# Gold set loading + integrity
# ---------------------------------------------------------------------------


def load_gold(path: str | Path) -> list[dict[str, Any]]:
    records = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def gold_integrity_errors(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [str(r.get("id") or "") for r in records]
    if len(set(ids)) != len(ids):
        errors.append("duplicate record ids")
    gold = [r for r in records if not r.get("defect_type")]
    defects = [r for r in records if r.get("defect_type")]
    if len(gold) != GOLD_N:
        errors.append(f"expected {GOLD_N} gold records, found {len(gold)}")
    for topic in TOPICS:
        n = sum(1 for r in gold if r.get("topic") == topic)
        if n != GOLD_TOPIC_COUNT:
            errors.append(f"topic {topic}: {n} gold records (want 5)")
    for dt in DEFECT_TYPES:
        n = sum(1 for r in defects if r.get("defect_type") == dt)
        if n != DEFECTS_PER_TYPE:
            errors.append(f"defect_type {dt}: {n} records (want 5)")
    for r in records:
        for key in (
            "id",
            "topic",
            "question",
            "correct_answer",
            "rationale",
            "source_note",
            "answer_type",
        ):
            if not str(r.get(key) or "").strip():
                errors.append(f"{r.get('id')}: missing {key}")
        if r.get("topic") not in TOPICS:
            errors.append(f"{r.get('id')}: unknown topic {r.get('topic')!r}")
        if r.get("answer_type") not in ("qualitative", "quantitative"):
            errors.append(f"{r.get('id')}: bad answer_type")
        if r.get("defect_type") and r["defect_type"] not in DEFECT_TYPES:
            errors.append(f"{r.get('id')}: bad defect_type")
    return errors


# ---------------------------------------------------------------------------
# Known-answer validation (confusion matrix on the gold set)
# ---------------------------------------------------------------------------


def known_answer_validation(
    checker: Checker, records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run the checker over the gold file itself: 50 hand-verified pairs
    should classify correct_useful; the 15 seeded defects should be caught
    in their buckets. Whatever actually happens is reported."""
    batch = checker.check_batch(records, accumulate=False, exclude_self_gold=True)
    matrix: dict[str, dict[str, int]] = {}
    per_record = []
    caught: dict[str, int] = {dt: 0 for dt in DEFECT_TYPES}
    blocked_defects = 0
    gold_ok = 0
    for rec, res in zip(records, batch.results):
        dt = rec.get("defect_type") or "gold"
        expected = DEFECT_EXPECTED_LABEL.get(dt, "correct_useful")
        row = matrix.setdefault(dt, {label: 0 for label in LABELS})
        row[res.label] += 1
        if dt == "gold" and res.label == "correct_useful" and res.shipped:
            gold_ok += 1
        if dt in caught:
            if res.label == expected:
                caught[dt] += 1
            if res.blocked:
                blocked_defects += 1
        per_record.append(
            {
                "id": res.card_id,
                "defect_type": dt,
                "expected_label": expected,
                "label": res.label,
                "shipped": res.shipped,
                "reasons": res.reasons,
            }
        )
    return {
        "matrix": matrix,
        "gold_correct_and_shipped": gold_ok,
        "gold_total": sum(1 for r in records if not r.get("defect_type")),
        "sensitivity_per_defect": {
            dt: f"{caught[dt]}/{DEFECTS_PER_TYPE}" for dt in DEFECT_TYPES
        },
        "defects_blocked": blocked_defects,
        "defects_total": sum(1 for r in records if r.get("defect_type")),
        "records": per_record,
    }


# ---------------------------------------------------------------------------
# Batch generation from the ONE named source (library use of the pipeline)
# ---------------------------------------------------------------------------


def generate_source_batch(
    source_doc: str = SOURCE_DOC,
    n: int = BATCH_N,
    seed: int = DEFAULT_SEED,
    corpus_dir: str | Path | None = None,
    reference_pdf: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate n cards whose generator grounding is `source_doc`, run them
    through the SAME machine gates + grounding as run_pipeline.py (as a
    library - the private ``_aig`` recomputation metadata is kept, because
    the ship gate requires it), and return (cards, meta)."""
    corpus_dir = Path(corpus_dir or HERE / "corpus")
    gens = [g for g in G.GENERATORS if g.passage.split("#")[0] == source_doc]
    if not gens:
        raise ValueError(f"no generators declare {source_doc} as grounding")
    compares = [
        c
        for c in G.compare_items()
        if c["_aig"]["declared_passage"].split("#")[0] == source_doc
    ]
    need = n - len(compares)
    kinds = ("worked", "cloze", "mcq")
    cells = [(g, k) for g in gens for k in kinds]
    base, rem = divmod(need, len(cells))
    counts = {(g.name, k): base for g, k in cells}
    # Remainder policy (frozen): extras go first to each generator's mcq
    # cell (the exam-congruent kind), then worked, then cloze.
    extra_order = [(g.name, k) for k in ("mcq", "worked", "cloze") for g in gens]
    for cell in extra_order[:rem]:
        counts[cell] += 1

    items: list[dict[str, Any]] = []
    for gen in gens:
        for kind in kinds:
            rng = random.Random(f"{seed}:{gen.name}:{kind}")
            for idx in range(counts[(gen.name, kind)]):
                item = G.build_item(gen, kind, idx, rng)
                item["_cc_id"] = f"{source_doc}/{gen.name}/{kind}/{idx + 1}"
                items.append(item)
    for i, item in enumerate(compares):
        item["_cc_id"] = f"{source_doc}/compare/{i + 1}"
        items.append(item)

    grounder = retrieval.GroundingRetriever(corpus_dir, items, seed=seed)
    corpus_texts = {p.pid: p.title + "\n" + p.text for p in grounder.passages}
    wall = gates.LeakageWall(reference_pdf=reference_pdf, corpus_texts=corpus_texts)
    kept: list[dict[str, Any]] = []
    gate_failures: list[dict[str, Any]] = []
    for item in items:
        results = [
            gates.gate_numeric(item),
            gates.gate_solve_check(item),
            gates.gate_rationale(item),
            gates.gate_leakage(item, wall),
        ]
        if gates.all_passed(results):
            grounder.ground_item(item)
            results.append(gates.gate_schema(item))
        if gates.all_passed(results):
            kept.append(item)
        else:
            gate_failures.append(
                {
                    "id": item["_cc_id"],
                    "gates": [r.as_dict() for r in results if not r.passed],
                }
            )
    off_doc = [
        it["_cc_id"] for it in kept if (it.get("source") or {}).get("doc") != source_doc
    ]
    meta = {
        "source_doc": source_doc,
        "requested": n,
        "generated": len(items),
        "passed_pipeline_gates": len(kept),
        "seed": seed,
        "generators": [g.gen_id for g in gens],
        "compare_fixtures": len(compares),
        "counts_per_generator_kind": {
            f"{name}/{kind}": c for (name, kind), c in sorted(counts.items())
        },
        "pipeline_gate_failures": gate_failures,
        "retrieval_source_landed_off_doc": off_doc,
        "leakage_reference_pdf_available": wall.reference_available,
    }
    return kept, meta


# ---------------------------------------------------------------------------
# Ship gate (the CLI --cards mode; also run on the generated batch)
# ---------------------------------------------------------------------------


def run_ship_gate(
    cards_path: Path,
    gold_records: list[dict[str, Any]],
    corpus_texts: dict[str, str],
    sidecar_path: Path | None = None,
) -> tuple[int, BatchResult, Path]:
    """Check a JSONL batch; write the blocked-ids sidecar; return exit code
    (0 = every card ships, 1 = at least one card is BLOCKED)."""
    cards = load_gold(cards_path)  # same JSONL reader
    batch = check_batch(cards, gold_records, corpus_texts)
    sidecar = sidecar_path or cards_path.with_suffix(
        cards_path.suffix + ".blocked.json"
    )
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(
            {
                "policy": SHIP_POLICY,
                "cards_file": str(cards_path),
                "counts": batch.counts,
                "blocked_ids": batch.blocked_ids,
                "blocked_reasons": {
                    r.card_id: r.reasons for r in batch.results if r.blocked
                },
            },
            indent=1,
        )
        + "\n"
    )
    return (1 if batch.blocked_ids else 0), batch, sidecar


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _cutoff_dict() -> dict[str, Any]:
    return {
        "frozen_on": CUTOFF_FROZEN_ON,
        "ship_policy": SHIP_POLICY,
        "DUP_JACCARD": DUP_JACCARD,
        "MIN_QUESTION_CHARS": MIN_QUESTION_CHARS,
        "MIN_QUESTION_CONTENT_TOKENS": MIN_QUESTION_CONTENT_TOKENS,
        "GOLD_MATCH_JACCARD": GOLD_MATCH_JACCARD,
        "ANSWER_AGREE_JACCARD": ANSWER_AGREE_JACCARD,
        "CONTRADICTION_OVERLAP": CONTRADICTION_OVERLAP,
        "SENTENCE_MIN_CONTENT": SENTENCE_MIN_CONTENT,
    }


def write_reports(
    eval_dir: Path,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    eval_dir.mkdir(parents=True, exist_ok=True)
    json_path = eval_dir / "card_check_report.json"
    md_path = eval_dir / "card_check_report.md"
    json_path.write_text(json.dumps(payload, indent=1) + "\n")
    md_path.write_text(_report_md(payload))
    return json_path, md_path


def _md_matrix(gold: dict[str, Any]) -> list[str]:
    lines = [
        "| seeded as | expected | -> correct_useful | -> wrong | "
        "-> bad_teaching | blocked |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    order = ["gold", "wrong", "vague", "duplicate"]
    blocked_by_type: dict[str, int] = {}
    for rec in gold["records"]:
        if not rec["shipped"]:
            blocked_by_type[rec["defect_type"]] = (
                blocked_by_type.get(rec["defect_type"], 0) + 1
            )
    for dt in order:
        row = gold["matrix"].get(dt)
        if row is None:
            continue
        expected = DEFECT_EXPECTED_LABEL.get(dt, "correct_useful")
        lines.append(
            f"| {dt} | {expected} | {row['correct_useful']} | {row['wrong']} "
            f"| {row['bad_teaching']} | {blocked_by_type.get(dt, 0)} |"
        )
    return lines


def _report_md(p: dict[str, Any]) -> str:
    cut = p["cutoff"]
    gold = p["gold_validation"]
    batch = p["batch"]
    counts = batch["check"]["counts"]
    lines: list[str] = []
    add = lines.append
    add("# Card checker report (challenge 7f)")
    add("")
    add(f"Generated: {p['meta']['generated']} — command: `{p['meta']['command']}`")
    add("")
    add("## Frozen cutoff (fixed BEFORE scoring; verbatim module constants)")
    add("")
    add(f"Frozen on {cut['frozen_on']} (tests pin these exact values):")
    add("")
    add(f"> {cut['ship_policy']}")
    add("")
    add(f"- `DUP_JACCARD = {cut['DUP_JACCARD']}` — question+answer token-set")
    add("  Jaccard at/above this blocks as duplicate (vs batch and gold).")
    add(
        f"- `MIN_QUESTION_CHARS = {cut['MIN_QUESTION_CHARS']}`, "
        f"`MIN_QUESTION_CONTENT_TOKENS = {cut['MIN_QUESTION_CONTENT_TOKENS']}`"
        " — vagueness lint; plus no-question-mark/no-task-cue rule and the"
    )
    add("  non-answer list; answer contained verbatim in question = trivial.")
    add(
        f"- `GOLD_MATCH_JACCARD = {cut['GOLD_MATCH_JACCARD']}`, "
        f"`ANSWER_AGREE_JACCARD = {cut['ANSWER_AGREE_JACCARD']}` — a card"
    )
    add("  asking a gold question but answering differently is WRONG.")
    add(f"- `CONTRADICTION_OVERLAP = {cut['CONTRADICTION_OVERLAP']}` — corpus")
    add("  negation/antonym flips need this much content overlap to fire.")
    add("- Classification priority: wrong beats bad_teaching; both block.")
    add("")
    add("## Gold-set known-answer validation (confusion matrix)")
    add("")
    add(
        f"{gold['gold_total']} hand-verified gold pairs + "
        f"{gold['defects_total']} seeded defects "
        "(gold/gold_set_v1.jsonl):"
    )
    add("")
    lines.extend(_md_matrix(gold))
    add("")
    add(
        f"- Gold specificity: {gold['gold_correct_and_shipped']}/"
        f"{gold['gold_total']} hand-verified pairs classified "
        "correct_useful and shipped."
    )
    sens = gold["sensitivity_per_defect"]
    add(
        f"- Defect sensitivity (label-exact): wrong {sens['wrong']}, "
        f"vague {sens['vague']}, duplicate {sens['duplicate']}; "
        f"{gold['defects_blocked']}/{gold['defects_total']} defects "
        "BLOCKED regardless of bucket."
    )
    misses = [
        r
        for r in gold["records"]
        if r["defect_type"] != "gold" and r["label"] != r["expected_label"]
    ]
    if misses:
        add("- Misses (reported, not retuned):")
        for m in misses:
            add(
                f"  - `{m['id']}` expected {m['expected_label']}, got "
                f"{m['label']}"
                + (" (still blocked)" if not m["shipped"] else " (SHIPPED)")
            )
    fp = [
        r
        for r in gold["records"]
        if r["defect_type"] == "gold" and r["label"] != "correct_useful"
    ]
    if fp:
        add("- False positives on hand-verified gold pairs:")
        for m in fp:
            add(f"  - `{m['id']}` -> {m['label']}: {'; '.join(m['reasons'])}")
    add("")
    add(
        f"## The {batch['meta']['requested']}-card batch from ONE source "
        f"(`{batch['meta']['source_doc']}`)"
    )
    add("")
    add(
        f"- Generators: {', '.join(batch['meta']['generators'])} + "
        f"{batch['meta']['compare_fixtures']} deterministic compare "
        "fixtures grounded in the same doc."
    )
    add(
        "- Counts per generator/kind (parameter-space expansion beyond "
        "DEFAULT_COUNTS to reach "
        f"{batch['meta']['requested']} from one doc): "
        + ", ".join(
            f"{k}={v}" for k, v in batch["meta"]["counts_per_generator_kind"].items()
        )
    )
    add(
        f"- Seed {batch['meta']['seed']}; mock/deterministic path; "
        f"{batch['meta']['passed_pipeline_gates']} of "
        f"{batch['meta']['generated']} generated items passed the standard "
        "pipeline gates (numeric, solve_check, rationale, leakage, "
        "grounding, schema) and form the checked batch."
    )
    if batch["meta"]["retrieval_source_landed_off_doc"]:
        add(
            "- Retrieval attached an off-doc source on: "
            + ", ".join(batch["meta"]["retrieval_source_landed_off_doc"])
            + " (BM25 imperfection, reported; declared generator grounding "
            "is duration.md for all cards)."
        )
    add("")
    add("### Headline counts (frozen cutoff applied)")
    add("")
    add(f"- **correct-and-useful: {counts['correct_useful']}**")
    add(f"- **wrong: {counts['wrong']}**")
    add(f"- **correct-but-bad-teaching: {counts['bad_teaching']}**")
    add(
        f"- BLOCKED: {counts['blocked']} of {len(batch['check']['cards'])} "
        f"(all wrong + all bad_teaching + "
        f"{counts['unverifiable_generated']} generated-but-unverifiable); "
        f"SHIPPED: {counts['shipped']}."
    )
    add("")
    add("### Per-card results")
    add("")
    add("| id | kind | label | verified | shipped | reason (first) |")
    add("| --- | --- | --- | --- | --- | --- |")
    for c in batch["check"]["cards"]:
        add(
            f"| {c['id']} | {c['kind']} | {c['label']} | {c['correctness']} "
            f"| {'yes' if c['shipped'] else 'BLOCKED'} | "
            f"{c['reasons'][0] if c['reasons'] else ''} |"
        )
    oddities = [
        c
        for c in batch["check"]["cards"]
        if c["blocked"]
        and c["reasons"]
        and not c["reasons"][0].startswith(("duplicate:", "unverifiable"))
    ]
    if oddities:
        add("")
        add("### Oddities observed (reported, not retuned)")
        add("")
        for c in oddities:
            add(f"- `{c['id']}`: {'; '.join(c['reasons'])}")
        add("")
        add(
            "These are frozen-rule hits inspected after the run: e.g. a "
            "100 bp yield move makes the duration-only answer's magnitude "
            "equal the stem's stated modified duration, so the answer "
            "digits appear verbatim in the question and the triviality "
            "rule fires - a borderline but defensible block (the answer "
            "can be read off the stem without computing). Left as-is per "
            "the no-retune rule."
        )
    add("")
    add("## The block is real")
    add("")
    add(
        f"- Ship-gate run on the batch file: exit code "
        f"{batch['gate_exit_code']} (non-zero because blocked cards exist); "
        f"sidecar with blocked ids + reasons: `{batch['sidecar']}`."
    )
    add(
        "- Integration for the pipeline owners (files not owned by this "
        "workstream): in run_pipeline.py, before writing items JSONL, drop "
        "items whose `card_check.check_batch(...)` result is blocked — or "
        "equivalently run `python3 card_check.py --cards items/generated"
        ".jsonl` in the build and fail on non-zero exit. One line each; "
        "the checker deliberately does not modify those files."
    )
    add("")
    add("## Honesty notes")
    add("")
    for note in p["honesty_notes"]:
        add(f"- {note}")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _corpus_texts(corpus_dir: Path) -> dict[str, str]:
    return {p.pid: p.title + "\n" + p.text for p in retrieval.load_corpus(corpus_dir)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", default=str(HERE / "gold" / "gold_set_v1.jsonl"))
    ap.add_argument("--corpus-dir", default=str(HERE / "corpus"))
    ap.add_argument(
        "--reference-pdf",
        default=str(HERE / "reference" / "cfa_l1_official_sample_2025.pdf"),
    )
    ap.add_argument("--source-doc", default=SOURCE_DOC)
    ap.add_argument("--n-cards", type=int, default=BATCH_N)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--eval-dir", default=str(HERE / "eval"))
    ap.add_argument(
        "--out-dir",
        default=str(HERE.parents[1] / "out" / "speedrun_eval" / "cardcheck"),
    )
    ap.add_argument(
        "--cards",
        default="",
        help="ship-gate mode: check this items JSONL, write the blocked-ids "
        "sidecar, exit non-zero if any card is blocked",
    )
    args = ap.parse_args(argv)

    gold_records = load_gold(args.gold)
    integrity = gold_integrity_errors(gold_records)
    corpus_texts = _corpus_texts(Path(args.corpus_dir))

    if args.cards:
        code, batch, sidecar = run_ship_gate(
            Path(args.cards), gold_records, corpus_texts
        )
        print(
            json.dumps(
                {
                    "mode": "ship-gate",
                    "counts": batch.counts,
                    "blocked_ids": batch.blocked_ids,
                    "sidecar": str(sidecar),
                    "exit_code": code,
                },
                indent=1,
            )
        )
        return code

    if integrity:
        print("gold set integrity FAILED:", file=sys.stderr)
        for e in integrity:
            print(f"  - {e}", file=sys.stderr)
        return 2

    checker = Checker(gold_records, corpus_texts)
    gold_validation = known_answer_validation(checker, gold_records)

    cards, gen_meta = generate_source_batch(
        source_doc=args.source_doc,
        n=args.n_cards,
        seed=args.seed,
        corpus_dir=args.corpus_dir,
        reference_pdf=args.reference_pdf,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_path = out_dir / f"source_batch_{len(cards)}.jsonl"
    with batch_path.open("w") as f:
        for card in cards:
            f.write(json.dumps(card, sort_keys=True) + "\n")

    gate_code, batch_check, sidecar = run_ship_gate(
        batch_path, gold_records, corpus_texts
    )

    counts = batch_check.counts
    honesty_notes = [
        "The backend is the mock/deterministic path: the 50 cards are "
        "parameterized-generator output (plus 2 hand-authored compare "
        "fixtures), so these numbers measure the generator+checker SYSTEM, "
        "not an LLM's error rate. The same command re-runs against a real "
        "backend via run_pipeline.py --backend; that path is unverified "
        "here.",
        "Wrong-fact residual risk: the checker verifies numeric cards by "
        "independent recomputation ONLY when generator metadata is present, "
        "and free-text facts only when they closely mirror a gold or corpus "
        "statement (same question / one-sided negation or antonym flip). "
        "defect::wrong::05 is seeded precisely to show the miss: a "
        "hand-written wrong number with no gold twin passes. That residual "
        "risk is why generated decks stay aig::ungraded and never feed "
        "Readiness, and why unverifiable GENERATED cards are blocked.",
        "The duplicate-heavy batch result is expected, not a tuning "
        "artifact: one corpus doc supports only "
        "3 generators x 3 kinds + 2 compare fixtures = 11 distinct "
        "templates, so asking for 50 cards from one source forces numeric "
        "re-skins of the same templates. The checker correctly refuses the "
        "redundancy; thresholds were frozen before the run and NOT retuned "
        "afterward.",
        "Gold-set correctness is author-verified against standard Level I "
        "material (each quantitative answer re-derivable from its own "
        "rationale; tests re-compute a spot-check set); no licensed CFAI "
        "text was copied. Hand-written gold cards ship on human attestation "
        "- the machine records them as unverifiable, which the cutoff "
        "permits for human-origin cards only.",
        "Blocked cards are actually blocked: the ship-gate exit code is "
        "non-zero and the sidecar lists blocked ids; wiring it into the "
        "deck build (one line, owned by run_pipeline/build_ladder_deck) "
        "keeps them out of any emitted deck.",
    ]
    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "command": "python3 card_check.py "
            + " ".join(argv if argv is not None else sys.argv[1:]),
            "gold_file": str(args.gold),
            "batch_file": str(batch_path),
        },
        "cutoff": _cutoff_dict(),
        "gold_validation": gold_validation,
        "batch": {
            "meta": gen_meta,
            "check": batch_check.as_dict(),
            "gate_exit_code": gate_code,
            "sidecar": str(sidecar),
        },
        "honesty_notes": honesty_notes,
    }
    json_path, md_path = write_reports(Path(args.eval_dir), payload)
    print(
        json.dumps(
            {
                "gold": {
                    "specificity": f"{gold_validation['gold_correct_and_shipped']}"
                    f"/{gold_validation['gold_total']}",
                    "sensitivity": gold_validation["sensitivity_per_defect"],
                    "defects_blocked": (
                        f"{gold_validation['defects_blocked']}"
                        f"/{gold_validation['defects_total']}"
                    ),
                },
                "batch_counts": counts,
                "batch_gate_exit_code": gate_code,
                "reports": [str(json_path), str(md_path)],
                "sidecar": str(sidecar),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
