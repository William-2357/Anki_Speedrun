# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""M5 - BYO-deck onboarding: the "Prepare this deck for Speedrun" engine.

Given a deck's notes (plain text, already HTML-stripped by the caller), this
module PROPOSES the scheduling structure the graph scheduler needs - topic
(``cfa::topic::``), cluster (``cluster::``), rung (``rung::``) and
interactivity (``interactivity::``) tags, plus behavioral confusability
markers (``confusable::high``) and, optionally, generated missing-rung items
- and returns it all as data for a preview dialog. NOTHING persists unless
``apply()`` is called with the user's accepted subset; nothing here runs
automatically at import time.

Design rules (PHASE3_PLAN_V2.md M5 / RUNTIME_AI_PLAN.md invariants):

- **Deterministic first, AI optional.** The topic proposer is a keyword
  lexicon; an optional AI pass (``assistant.core.grounded_complete``,
  grounded-or-abstain) only fills notes the lexicon ABSTAINED on, obeying
  the same >= 0.6 confidence floor as ``assistant/tag_suggest.py``, and it
  can never override a deterministic proposal. AI off/unavailable/erroring
  degrades to deterministic-only - never an error.
- **Abstain over guess.** A topic is proposed only when the top lexicon
  score clears ``MIN_TOPIC_SCORE`` (2.0) AND is >= ``TOPIC_MARGIN`` (1.5x)
  the runner-up; ties and thin evidence leave the note blank. Clusters
  exist for contrast scheduling, so a singleton "cluster" is useless: only
  groups of >= 2 notes sharing a distinctive term are proposed. Most BYO
  cards are plain flashcards - a rung tag without a full ladder is noise -
  so a rung is proposed only on a strong shape signal (cloze markup,
  numbered worked steps, compare framing, bare numeric-answer question) and
  otherwise omitted. ``interactivity::high`` needs formula AND numbers.
- **Confusability is behavioral, within-topic, auto-validated.** Edges come
  from ``aig/confusability.py`` (lapse co-occurrence, 70/30 time split,
  abstains when it cannot beat the surface baseline [R18]) - never raw
  embedding similarity. Reviews are relabelled with the deck's
  existing-or-proposed topic/cluster tags before mining (a BYO deck has no
  cluster tags yet - that is the point of onboarding). No revlog => the
  step is skipped and says so.
- **Missing-rung generation is off by default** and reuses the AIG path
  wholesale: ``aig.models.make_llm_path`` (drafter + critic + solver
  consensus) -> retrieval grounding against the corpus (BM25; abstains when
  the corpus does not cover the cluster) -> ``aig.gates.run_gates`` ->
  ``speedrun-item-v1`` records. The shipped drafter only drafts MCQ (solve)
  items, so only a missing ``rung::solve`` can be generated; other missing
  rungs are reported as abstentions. Generated items carry
  ``provenance.graded = False`` / the ``aig::ungraded`` tag ([R24]: memory
  credit only, zero performance-transfer credit until validated;
  ``aig::graded`` is never flipped here) and are returned in the proposal,
  NOT auto-added. Clusters with no rung at all are left alone (there is no
  partial ladder to complete).
- **Honesty.** Every proposal carries ``evidence`` (which keywords or
  heuristics fired, or the miner's validation statement) and ``confidence``
  (deterministic heuristics use fixed documented values; the lexicon uses
  the score margin; confusability uses the held-out validation AUC) so the
  preview dialog never silently attributes.
- **Undoable, additive apply.** ``apply()`` adds tags through ONE
  ``col.update_notes(...)`` call (single undo step, pylib imported only by
  the caller - this module stays importable without Qt or pylib) and never
  removes or edits anything else.

stdlib + sibling tools/speedrun modules only; no anki/aqt imports anywhere.
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SPEEDRUN_DIR = Path(__file__).resolve().parent
if str(_SPEEDRUN_DIR) not in sys.path:  # direct script/e.g. aqt-side import
    sys.path.insert(0, str(_SPEEDRUN_DIR))

import ladder_schema  # noqa: E402
from aig import confusability  # noqa: E402
from aig.pdf_text import tokenize  # noqa: E402

# --------------------------------------------------------------------------
# Tunables (documented in the module docstring)
# --------------------------------------------------------------------------

#: A topic is proposed only when top score >= MIN_TOPIC_SCORE ...
MIN_TOPIC_SCORE = 2.0
#: ... AND top score >= TOPIC_MARGIN x runner-up score (ties abstain).
TOPIC_MARGIN = 1.5
#: Clusters need >= 2 members (contrast scheduling needs a contrast).
MIN_CLUSTER_SIZE = 2
#: A shared term counts as cluster glue only when it appears in at most
#: this fraction of the topic's notes (else it is topic vocabulary).
CLUSTER_MAX_DF_FRACTION = 0.6
#: The corpus "covers" a cluster when the best BM25 passage shares at least
#: this many distinct informative tokens with the cluster's fronts.
CORPUS_COVER_MIN_TOKENS = 3

#: Mirrors assistant.tag_suggest.CONFIDENCE_FLOOR - the AI fill obeys the
#: same bar; import keeps the two in lockstep, the literal is the fallback.
try:
    from assistant.tag_suggest import CONFIDENCE_FLOOR as AI_CONFIDENCE_FLOOR
except Exception:  # pragma: no cover - assistant package always ships
    AI_CONFIDENCE_FLOOR = 0.6

#: Fixed, documented confidences for the deterministic heuristics.
CONFIDENCE_CLOZE = 0.95  # native cloze markup is definitive
CONFIDENCE_WORKED = 0.8  # numbered-steps detection
CONFIDENCE_COMPARE = 0.7  # versus/compare framing
CONFIDENCE_SOLVE = 0.7  # bare question + numeric/MCQ answer shape
CONFIDENCE_INTERACTIVITY = 0.7  # formula + numbers heuristic
CONFIDENCE_CLUSTER = 0.7  # shared-distinctive-term grouping

LADDER_RUNGS = ("worked", "faded", "solve")

CONFUSABLE_TAG = confusability.CONFUSABLE_TAG

#: The 10 canonical CFA Level I topic ids (cfa_sample_cards.py).
CFA_TOPICS = (
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

#: cluster::<abbrev>::<slug> - the abbreviations cfa_sample_cards.py uses.
TOPIC_ABBREV = {
    "ethics": "ethics",
    "quantitative_methods": "quant",
    "economics": "econ",
    "financial_statement_analysis": "fsa",
    "corporate_issuers": "corp",
    "equity_investments": "equity",
    "fixed_income": "fi",
    "derivatives": "deriv",
    "alternative_investments": "alt",
    "portfolio_management": "pm",
}

# --------------------------------------------------------------------------
# Topic lexicon - phrases are matched on the tokenized text (punctuation-
# and case-insensitive), so "P/E ratio" is the phrase "p e ratio".
# Weights: 3 = near-unambiguous term, 2 = strong subject term, 1 = weak /
# shared vocabulary. Spec-seeded, extended with common CFA L1 terms.
# --------------------------------------------------------------------------

TOPIC_LEXICON: dict[str, dict[str, float]] = {
    "fixed_income": {
        "duration": 2,
        "convexity": 2,
        "bond": 2,
        "bonds": 2,
        "coupon": 2,
        "yield to maturity": 3,
        "ytm": 2,
        "macaulay": 3,
        "credit spread": 2,
        "zero coupon": 2,
        "par value": 1,
        "maturity": 1,
        "securitization": 2,
    },
    "economics": {
        "gdp": 2,
        "elasticity": 2,
        "inflation": 2,
        "monopoly": 2,
        "oligopoly": 2,
        "fiscal policy": 2,
        "monetary policy": 2,
        "exchange rate": 2,
        "supply curve": 2,
        "demand curve": 2,
        "comparative advantage": 2,
        "business cycle": 2,
        "deadweight loss": 2,
    },
    "financial_statement_analysis": {
        "fifo": 3,
        "lifo": 3,
        "depreciation": 2,
        "accrual": 2,
        "accruals": 2,
        "balance sheet": 2,
        "income statement": 2,
        "cash flow statement": 2,
        "inventory": 1,
        "goodwill": 2,
        "impairment": 2,
        "deferred tax": 2,
        "current ratio": 2,
        "quick ratio": 2,
        "amortization": 1,
    },
    "corporate_issuers": {
        "npv": 2,
        "irr": 2,
        "capital structure": 3,
        "dividend": 2,
        "wacc": 3,
        "cost of capital": 2,
        "capital budgeting": 3,
        "payback period": 2,
        "share repurchase": 2,
        "corporate governance": 2,
        "working capital": 2,
    },
    "equity_investments": {
        "p e ratio": 3,
        "price to earnings": 3,
        "ddm": 3,
        "dividend discount": 3,
        "free cash flow": 2,
        "intrinsic value": 2,
        "market efficiency": 2,
        "preferred stock": 2,
        "common stock": 2,
        "market capitalization": 2,
        "equity": 1,
        "stock": 1,
    },
    "derivatives": {
        "forward": 2,
        "futures": 2,
        "option": 2,
        "options": 2,
        "swap": 2,
        "call option": 3,
        "put option": 3,
        "strike": 2,
        "underlying": 2,
        "notional": 2,
        "arbitrage": 1,
        "hedge": 1,
    },
    "alternative_investments": {
        "real estate": 2,
        "hedge fund": 3,
        "hedge funds": 3,
        "private equity": 3,
        "commodity": 2,
        "commodities": 2,
        "reit": 2,
        "venture capital": 2,
        "infrastructure": 1,
    },
    "portfolio_management": {
        "capm": 3,
        "beta": 2,
        "sharpe": 3,
        "portfolio": 2,
        "efficient frontier": 3,
        "systematic risk": 2,
        "diversification": 2,
        "capital market line": 3,
        "security market line": 3,
        "treynor": 3,
        "information ratio": 2,
    },
    "ethics": {
        "code of ethics": 3,
        "standards of professional conduct": 3,
        "gips": 3,
        "cfa institute": 2,
        "professional conduct": 2,
        "misconduct": 2,
        "material nonpublic": 3,
        "material non public": 3,
        "fiduciary": 2,
        "plagiarism": 2,
        "standard": 1,
        "standards": 1,
    },
    "quantitative_methods": {
        "probability": 2,
        "regression": 2,
        "hypothesis": 2,
        "null hypothesis": 2,
        "mean": 1,
        "median": 1,
        "variance": 2,
        "standard deviation": 2,
        "correlation": 2,
        "confidence interval": 2,
        "p value": 2,
        "skewness": 2,
        "kurtosis": 2,
        "monte carlo": 2,
        "bayes": 2,
        "time value of money": 2,
    },
}

#: Tokens too generic to be cluster glue (question scaffolding, rung words).
_CLUSTER_STOPWORDS = frozenset(
    """
    the a an of in on for to and or is are was were vs versus what which how
    why when who does do did its it this that with between under per from by
    as at be not no one two their they you your if then than into about over
    each both all any can could would should define definition means measure
    measures measured using use used following closest correct true false
    statement question answer card note step steps formula give state list
    describe explain name
    """.split()
)

_CLOZE_RE = re.compile(r"\{\{c\d+::")
_STEP_WORD_RE = re.compile(r"(?i)\bstep\s*[1-9]")
_NUMBERED_LINE_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")
_MCQ_SHAPE_RE = re.compile(r"(?ms)^\s*A[.)]\s.*^\s*B[.)]\s")
_NUMERIC_ANSWER_RE = re.compile(
    r"(?i)^[~$\u2248\u20ac\u00a3-]{0,2}\s*\d[\d,]*(?:\.\d+)?"
    r"\s*(?:%|percent|years?|months?|days?|bps|x|times)?[.\s]*$"
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class NoteInfo:
    """One note as the caller read it (plain text, HTML already stripped)."""

    note_id: int
    front: str
    back: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class TagProposal:
    """One proposed tag addition, with its honesty trail."""

    tag: str
    evidence: str
    confidence: float
    source: str  # lexicon | ai | cluster | rung | interactivity | confusability


@dataclass
class NoteProposal:
    note_id: int
    front: str
    tags: list[TagProposal] = field(default_factory=list)


@dataclass
class OnboardProposal:
    """Everything ``propose()`` computed; plain data for the preview UI.

    ``notes`` lists only notes with at least one proposal. ``cluster_markers``
    is the raw miner output (cluster suffix -> marker tags), already folded
    into per-note proposals. ``generated_items`` holds gate-passing
    ``speedrun-item-v1`` records as ``{"item", "tags", "gates", "audit"}``
    (never auto-added anywhere). ``generation_notes`` and ``confusability``
    record every abstention in plain language.
    """

    notes: list[NoteProposal] = field(default_factory=list)
    cluster_markers: dict[str, list[str]] = field(default_factory=dict)
    confusability: str = ""
    generated_items: list[dict[str, Any]] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def tag_count(self) -> int:
        return sum(len(np.tags) for np in self.notes)


# --------------------------------------------------------------------------
# Existing-tag introspection (a dimension the user already tagged is final;
# proposals are additive and must not fight or duplicate user structure)
# --------------------------------------------------------------------------


def _existing_suffix(tags: Sequence[str], prefix: str) -> str:
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return ""


def _match_text(note: NoteInfo) -> str:
    """Normalized token string for phrase matching (front + back)."""
    return " " + " ".join(tokenize(note.front + " " + note.back)) + " "


# --------------------------------------------------------------------------
# 1. Topic proposer - deterministic lexicon, then optional AI fill
# --------------------------------------------------------------------------


def score_topics(
    note: NoteInfo, topics: Sequence[str]
) -> dict[str, tuple[float, list[str]]]:
    """Weighted lexicon hits per topic: {topic: (score, matched phrases)}."""
    text = _match_text(note)
    scores: dict[str, tuple[float, list[str]]] = {}
    for topic in topics:
        lexicon = TOPIC_LEXICON.get(topic)
        if not lexicon:
            continue
        hits = [phrase for phrase in lexicon if f" {phrase} " in text]
        if hits:
            scores[topic] = (sum(lexicon[h] for h in hits), sorted(hits))
    return scores


def propose_topic(
    note: NoteInfo, topics: Sequence[str]
) -> tuple[str, str, float] | None:
    """(topic, evidence, confidence) or None (ABSTAIN).

    Propose only when top >= MIN_TOPIC_SCORE and top >= TOPIC_MARGIN x
    runner-up. Confidence is the score margin, top/(top + runner-up)
    (>= 0.6 by construction when the margin rule passes), capped at 0.9
    when there is no runner-up at all.
    """
    scores = score_topics(note, topics)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    top_topic, (top, hits) = ranked[0]
    runner = ranked[1][1][0] if len(ranked) > 1 else 0.0
    if top < MIN_TOPIC_SCORE or top < TOPIC_MARGIN * runner:
        return None
    confidence = 0.9 if runner == 0 else min(0.95, round(top / (top + runner), 2))
    evidence = f"keywords: {', '.join(hits)} (score {top:g} vs runner-up {runner:g})"
    return top_topic, evidence, confidence


_AI_TOPIC_SYSTEM = """\
You classify flashcard notes for a study dashboard.

Assign EVERY note in FACTS["notes"] exactly one of:
- a canonical topic id from FACTS["topics"], copied verbatim;
- "unsure" when the evidence is too thin to decide.

Judge only from each note's "front" text. Include a confidence number
between 0 and 1 for every note. Never guess: a wrong topic misattributes
mastery, so prefer "unsure" whenever in doubt.

Reply as {"suggestions": {<note_id as string>: {"topic": <topic id |
"unsure">, "confidence": <0-1>}}} with one entry per input note.
"""


def _ai_topic_fill(
    abstained: list[NoteInfo],
    topics: Sequence[str],
    backend: Any,
    diagnostics: dict[str, Any],
) -> dict[int, tuple[str, str, float]]:
    """AI proposals for lexicon-abstained notes only; grounded-or-abstain.

    Any failure returns {} - the deterministic view stands. Suggestions
    below AI_CONFIDENCE_FLOOR, for unknown notes, or naming topics outside
    the allowed list are dropped, mirroring tag_suggest's validation.
    """
    if not abstained or backend is None:
        diagnostics["reason"] = "ai fill skipped (no backend or nothing abstained)"
        return {}
    try:
        from assistant import core as assistant_core

        facts = {
            "notes": [
                {"note_id": note.note_id, "front": note.front[:200]}
                for note in abstained
            ],
            "topics": [str(t) for t in topics],
        }
        reply = assistant_core.grounded_complete(
            _AI_TOPIC_SYSTEM,
            facts,
            schema={"suggestions": "dict"},
            backend=backend,
            task="onboard_topics",
            diagnostics=diagnostics,
        )
    except Exception as exc:  # never an error: deterministic-only fallback
        diagnostics["reason"] = f"ai fill failed: {exc}"
        return {}
    if reply is None:
        return {}
    raw = reply.get("suggestions")
    entries: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    known = {note.note_id for note in abstained}
    allowed = set(str(t) for t in topics)
    backend_name = getattr(backend, "name", "?")
    kept: dict[int, tuple[str, str, float]] = {}
    for key, value in entries.items():
        try:
            note_id = int(key)
        except (TypeError, ValueError):
            continue
        if note_id not in known or not isinstance(value, Mapping):
            continue
        topic = value.get("topic")
        if not isinstance(topic, str) or topic == "unsure" or topic not in allowed:
            continue
        conf = value.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            continue
        if not 0 <= conf <= 1 or conf < AI_CONFIDENCE_FLOOR:
            continue
        evidence = (
            f"AI fill on lexicon abstention (backend {backend_name}); "
            f"self-reported confidence {float(conf):.2f}"
        )
        kept[note_id] = (topic, evidence, float(conf))
    diagnostics["kept"] = len(kept)
    diagnostics["dropped"] = len(known) - len(kept)
    return kept


# --------------------------------------------------------------------------
# 2. Cluster proposer - within one topic, shared distinctive terms
# --------------------------------------------------------------------------


def propose_clusters(
    notes: Sequence[NoteInfo], topic_of: Mapping[int, str]
) -> dict[int, tuple[str, str, float]]:
    """{note_id: (cluster tag, evidence, confidence)} - within-topic only.

    tf-idf-ish: a candidate term must appear in >= MIN_CLUSTER_SIZE of the
    topic's unclustered notes but at most CLUSTER_MAX_DF_FRACTION of them,
    scored by (in-topic count) x log(1 + total notes / count anywhere);
    greedy highest-score-first assignment, one cluster per note.
    """
    by_topic: dict[str, list[NoteInfo]] = {}
    for note in notes:
        topic = topic_of.get(note.note_id, "")
        if topic and not _existing_suffix(note.tags, ladder_schema.CLUSTER_TAG_PREFIX):
            by_topic.setdefault(topic, []).append(note)

    total = max(len(notes), 1)
    df_all: dict[str, int] = {}
    note_tokens: dict[int, set[str]] = {}
    for note in notes:
        toks = {
            t
            for t in tokenize(note.front)
            if len(t) >= 3 and not t.isdigit() and t not in _CLUSTER_STOPWORDS
        }
        note_tokens[note.note_id] = toks
        for t in toks:
            df_all[t] = df_all.get(t, 0) + 1

    out: dict[int, tuple[str, str, float]] = {}
    for topic, members in sorted(by_topic.items()):
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        max_df = max(MIN_CLUSTER_SIZE, int(CLUSTER_MAX_DF_FRACTION * len(members)))
        df_topic: dict[str, int] = {}
        for note in members:
            for t in note_tokens[note.note_id]:
                df_topic[t] = df_topic.get(t, 0) + 1
        candidates = sorted(
            (
                (df * math.log(1 + total / df_all[t]), t)
                for t, df in df_topic.items()
                if MIN_CLUSTER_SIZE <= df <= max_df
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )
        abbrev = TOPIC_ABBREV.get(topic, topic)
        assigned: set[int] = set()
        for _score, token in candidates:
            group = [
                n
                for n in members
                if n.note_id not in assigned and token in note_tokens[n.note_id]
            ]
            if len(group) < MIN_CLUSTER_SIZE:
                continue
            tag = f"{ladder_schema.CLUSTER_TAG_PREFIX}{abbrev}::{token}"
            for n in group:
                assigned.add(n.note_id)
                out[n.note_id] = (
                    tag,
                    f"shares distinctive term '{token}' with "
                    f"{len(group) - 1} other note(s) in {topic}",
                    CONFIDENCE_CLUSTER,
                )
    return out


# --------------------------------------------------------------------------
# 3. Rung + interactivity heuristics
# --------------------------------------------------------------------------


def propose_rung(note: NoteInfo) -> tuple[str, str, float] | None:
    """(rung tag, evidence, confidence) or None (most cards get NO rung).

    Precedence: cloze markup (definitive) > numbered steps > compare
    framing > numeric/MCQ solve shape. Plain flashcards fall through: a
    rung tag without a full ladder is scheduling noise.
    """
    text = note.front + "\n" + note.back
    prefix = ladder_schema.RUNG_TAG_PREFIX
    if _CLOZE_RE.search(text):
        evidence = "native cloze markup ({{cN::...}}) found"
        return f"{prefix}faded", evidence, CONFIDENCE_CLOZE
    numbered = len(_NUMBERED_LINE_RE.findall(text))
    if _STEP_WORD_RE.search(text) or numbered >= 2:
        detail = (
            "'Step N' wording"
            if _STEP_WORD_RE.search(text)
            else f"{numbered} numbered solution lines"
        )
        return f"{prefix}worked", f"worked-solution shape: {detail}", CONFIDENCE_WORKED
    front_tokens = set(tokenize(note.front))
    if front_tokens & {"versus", "vs", "compare", "contrast"}:
        evidence = "compare/contrast framing in the question"
        return f"{prefix}compare", evidence, CONFIDENCE_COMPARE
    is_question = note.front.rstrip().endswith("?") or "closest to" in (
        note.front.lower()
    )
    if is_question and (
        _NUMERIC_ANSWER_RE.match(note.back.strip()) or _MCQ_SHAPE_RE.search(note.front)
    ):
        shape = (
            "MCQ-shaped choices"
            if _MCQ_SHAPE_RE.search(note.front)
            else "bare numeric answer"
        )
        return f"{prefix}solve", f"question with {shape}", CONFIDENCE_SOLVE
    return None


def propose_interactivity(note: NoteInfo) -> tuple[str, str, float] | None:
    """interactivity::high only for multi-step computation; else abstain."""
    text = _CLOZE_RE.sub("", note.front + " " + note.back).replace("}}", "")
    numbers = _NUMBER_RE.findall(text)
    if "=" in text and len(numbers) >= 2:
        return (
            f"{ladder_schema.INTERACTIVITY_TAG_PREFIX}high",
            f"formula ('=') plus {len(numbers)} numeric values present",
            CONFIDENCE_INTERACTIVITY,
        )
    return None


# --------------------------------------------------------------------------
# 4. Confusability edges (behavioral, auto-validated, abstaining)
# --------------------------------------------------------------------------


def _reviews_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[Any]:
    """Rows {note_id, button, id_ms, tags?} -> confusability.Review list."""
    reviews = []
    for row in rows:
        tags = row.get("tags")
        if isinstance(tags, str):
            tags = tags.split()
        tags = [str(t) for t in tags or []]
        reviews.append(
            confusability.Review(
                note_id=int(row.get("note_id", 0)),
                cluster=_existing_suffix(tags, ladder_schema.CLUSTER_TAG_PREFIX),
                topic=_existing_suffix(tags, ladder_schema.TOPIC_TAG_PREFIX),
                lapse=int(row.get("button", 0)) == 1,
                day=int(row["id_ms"]) // confusability.MS_PER_DAY,
                id_ms=int(row["id_ms"]),
            )
        )
    reviews.sort(key=lambda r: r.id_ms)
    return reviews


def _load_reviews(
    revlog: Any,
) -> tuple[list[Any], dict[int, str], str]:
    """revlog (path to .anki2/JSONL, or iterable of row dicts) -> reviews.

    Returns (reviews, fronts-from-file, error). Reviews for notes the
    caller did not supply keep their recorded tags.
    """
    try:
        if isinstance(revlog, (str, Path)):
            path = str(revlog)
            if path.endswith((".anki2", ".sqlite", ".db")):
                reviews, fronts = confusability.load_revlog_sqlite(path)
                return reviews, fronts, ""
            return confusability.load_revlog_jsonl(path), {}, ""
        return _reviews_from_rows(revlog), {}, ""
    except Exception as exc:
        return [], {}, f"revlog unreadable ({exc})"


def mine_confusability(
    notes: Sequence[NoteInfo],
    revlog: Any,
    topic_of: Mapping[int, str],
    cluster_of: Mapping[int, str],
) -> tuple[Any | None, str]:
    """Relabel the revlog with existing-or-proposed tags, then mine.

    Returns (ConfusabilityResult | None, status text). The miner's own
    70/30 time-split validation and abstention are untouched.
    """
    if revlog is None:
        return None, "no revlog provided; confusability mining skipped"
    reviews, file_fronts, error = _load_reviews(revlog)
    if error:
        return None, f"{error}; confusability mining skipped"
    for review in reviews:
        if review.note_id in cluster_of:
            review.cluster = cluster_of[review.note_id]
        if review.note_id in topic_of:
            review.topic = topic_of[review.note_id]
    fronts = dict(file_fronts)
    fronts.update({note.note_id: note.front for note in notes})
    result = confusability.compute(reviews, fronts)
    return result, result.reason


# --------------------------------------------------------------------------
# 5. Missing-rung generation (optional, default OFF, [R24])
# --------------------------------------------------------------------------


def _corpus_index(corpus_dir: str | Path) -> Any | None:
    from aig import retrieval

    passages = retrieval.load_corpus(corpus_dir)
    if not passages:
        return None
    return retrieval.Bm25Index(passages)


def _corpus_cover(index: Any, query: str) -> tuple[Any | None, list[str]]:
    """Best passage if it shares >= CORPUS_COVER_MIN_TOKENS tokens, else None."""
    ranked = index.score(query)
    if not ranked or ranked[0][1] <= 0:
        return None, []
    best_pid = ranked[0][0]
    passage = next(p for p in index.passages if p.pid == best_pid)
    overlap = sorted(
        set(tokenize(query)) & set(tokenize(passage.title + " " + passage.text))
    )
    if len(overlap) < CORPUS_COVER_MIN_TOKENS:
        return None, overlap
    return passage, overlap


def draft_missing_rungs(
    clusters: Mapping[str, list[NoteInfo]],
    topic_of_cluster: Mapping[str, str],
    rungs_present: Mapping[str, set[str]],
    *,
    corpus_dir: str | Path | None = None,
    llm_path: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Draft gate-validated items for partially-laddered clusters.

    Reuses the AIG path unchanged: make_llm_path (drafter/critic/solver
    consensus) -> BM25 retrieval grounding (abstain when the corpus does
    not cover the cluster) -> run_gates -> speedrun-item-v1. Only
    ``rung::solve`` (MCQ) is draftable by the shipped pipeline; every other
    gap is an explicit abstention. Emitted items are graded=False /
    ``aig::ungraded`` - never flipped ([R24]).
    """
    from aig import gates, models
    from aig.generators import strip_private

    items: list[dict[str, Any]] = []
    notes_log: list[str] = []
    partial = {
        suffix: sorted(set(LADDER_RUNGS) - rungs_present[suffix])
        for suffix in sorted(clusters)
        if rungs_present.get(suffix) and set(LADDER_RUNGS) - rungs_present[suffix]
    }
    if not partial:
        return items, ["no cluster has a partial ladder; nothing to generate"]

    corpus_dir = corpus_dir or _SPEEDRUN_DIR / "corpus"
    index = _corpus_index(corpus_dir)
    if index is None:
        return items, [f"grounding corpus not found at {corpus_dir}; abstaining"]
    if llm_path is None:
        llm_path = models.make_llm_path("mock")
    corpus_texts = {p.pid: p.title + "\n" + p.text for p in index.passages}
    wall = gates.LeakageWall(reference_pdf=None, corpus_texts=corpus_texts)
    drafter_name = getattr(llm_path.drafter, "name", "?")

    for suffix, missing in partial.items():
        member_notes = clusters[suffix]
        for rung in missing:
            if rung != "solve":
                notes_log.append(
                    f"{suffix}: rung::{rung} missing, but the shipped AIG "
                    "drafter only drafts MCQ (solve) items; abstaining"
                )
                continue
            fronts = [n.front for n in member_notes[:3]]
            query = " ".join(fronts) or suffix
            passage, overlap = _corpus_cover(index, query)
            if passage is None:
                notes_log.append(
                    f"{suffix}: corpus does not cover this cluster "
                    f"(shared tokens: {overlap or 'none'}); abstaining "
                    "(grounded-or-abstain)"
                )
                continue
            topic = topic_of_cluster.get(suffix, "")
            slug = suffix.rsplit("::", 1)[-1].replace("_", " ")
            concept = f"{slug} - as studied in notes like: {fronts[0][:120]}"
            draft, audit = llm_path.generate_validated(topic, suffix, concept, [])
            if draft is None:
                notes_log.append(
                    f"{suffix}: drafter/critic/consensus rejected the draft "
                    f"({audit.get('outcome', 'no outcome')})"
                )
                continue
            item: dict[str, Any] = {
                "schema": ladder_schema.SCHEMA_LITERAL,
                "kind": "mcq",
                "rung": "solve",
                "topic": topic,
                "cluster": suffix,
                "interactivity": "high",
                "title": str(draft.get("title") or f"Generated solve item ({suffix})"),
                "stem": draft.get("stem", ""),
                "choices": draft.get("choices", {}),
                "correct": draft.get("correct", ""),
                "distractor_rationales": draft.get("distractor_rationales", {}),
                "misconceptions": draft.get("misconceptions", {}),
                "rationale": draft.get("rationale", ""),
                "source": {
                    "doc": passage.doc,
                    "loc": f"#{passage.pid.split('#', 1)[1]}",
                    "passage": passage.text[:500],
                },
                # graded stays False: [R24] memory credit only, zero
                # performance-transfer credit until validated on delayed
                # held-out probes; aig::graded is NEVER set here.
                "provenance": {
                    "generator": f"llm:{drafter_name}",
                    "gates": [],
                    "graded": False,
                },
            }
            results = gates.run_gates(item, wall, llm_path)
            if not gates.all_passed(results):
                failed = "; ".join(
                    f"{r.gate}: {r.reason}" for r in results if not r.passed
                )
                notes_log.append(f"{suffix}: gates rejected the draft ({failed})")
                continue
            item["provenance"]["gates"] = [r.gate for r in results]
            public = strip_private(item)
            items.append(
                {
                    "item": public,
                    "tags": ladder_schema.tags_for_item(public),
                    "gates": [r.as_dict() for r in results],
                    "audit": audit,
                }
            )
    return items, notes_log


# --------------------------------------------------------------------------
# The proposal entry point
# --------------------------------------------------------------------------


def propose(
    notes: list[NoteInfo],
    topics: list[str],
    *,
    backend: Any = None,
    revlog: Any = None,
    generate_missing_rungs: bool = False,
    corpus_dir: str | Path | None = None,
    llm_path: Any = None,
) -> OnboardProposal:
    """Compute the full onboarding proposal for one deck. Pure: no writes.

    ``backend``: an ``aig.models.Backend`` for the optional AI topic fill;
    None = deterministic only. ``revlog``: a path (.anki2/.sqlite/.db or
    JSONL, the formats aig/confusability.py accepts) or an iterable of
    ``{note_id, button, id_ms, tags?}`` rows read by the caller (the open
    desktop collection holds an exclusive sqlite lock, so the Qt layer
    passes rows, not the live db path). ``generate_missing_rungs`` defaults
    to False; ``llm_path`` defaults to the offline mock when generation is
    enabled.
    """
    proposals: dict[int, list[TagProposal]] = {note.note_id: [] for note in notes}

    # -- topics: deterministic lexicon, then AI fill of abstentions --------
    topic_of: dict[int, str] = {}
    abstained: list[NoteInfo] = []
    for note in notes:
        existing = _existing_suffix(note.tags, ladder_schema.TOPIC_TAG_PREFIX)
        if existing:
            topic_of[note.note_id] = existing
            continue
        picked = propose_topic(note, topics)
        if picked is None:
            abstained.append(note)
            continue
        topic, evidence, confidence = picked
        topic_of[note.note_id] = topic
        proposals[note.note_id].append(
            TagProposal(
                tag=f"{ladder_schema.TOPIC_TAG_PREFIX}{topic}",
                evidence=evidence,
                confidence=confidence,
                source="lexicon",
            )
        )
    ai_diag: dict[str, Any] = {}
    for note_id, (topic, evidence, confidence) in _ai_topic_fill(
        abstained, topics, backend, ai_diag
    ).items():
        topic_of[note_id] = topic
        proposals[note_id].append(
            TagProposal(
                tag=f"{ladder_schema.TOPIC_TAG_PREFIX}{topic}",
                evidence=evidence,
                confidence=confidence,
                source="ai",
            )
        )

    # -- clusters within (existing or proposed) topics [R8] ----------------
    cluster_of: dict[int, str] = {}
    for note in notes:
        existing = _existing_suffix(note.tags, ladder_schema.CLUSTER_TAG_PREFIX)
        if existing:
            cluster_of[note.note_id] = existing
    for note_id, (tag, evidence, confidence) in propose_clusters(
        notes, topic_of
    ).items():
        cluster_of[note_id] = tag[len(ladder_schema.CLUSTER_TAG_PREFIX) :]
        proposals[note_id].append(
            TagProposal(
                tag=tag, evidence=evidence, confidence=confidence, source="cluster"
            )
        )

    # -- rung + interactivity ----------------------------------------------
    rung_of: dict[int, str] = {}
    for note in notes:
        existing_rung = _existing_suffix(note.tags, ladder_schema.RUNG_TAG_PREFIX)
        if existing_rung:
            rung_of[note.note_id] = existing_rung
        else:
            picked_rung = propose_rung(note)
            if picked_rung is not None:
                tag, evidence, confidence = picked_rung
                rung_of[note.note_id] = tag[len(ladder_schema.RUNG_TAG_PREFIX) :]
                proposals[note.note_id].append(
                    TagProposal(
                        tag=tag,
                        evidence=evidence,
                        confidence=confidence,
                        source="rung",
                    )
                )
        if not _existing_suffix(note.tags, ladder_schema.INTERACTIVITY_TAG_PREFIX):
            picked_ia = propose_interactivity(note)
            if picked_ia is not None:
                tag, evidence, confidence = picked_ia
                proposals[note.note_id].append(
                    TagProposal(
                        tag=tag,
                        evidence=evidence,
                        confidence=confidence,
                        source="interactivity",
                    )
                )

    # -- confusability edges -------------------------------------------------
    markers: dict[str, list[str]] = {}
    conf_result, conf_status = mine_confusability(notes, revlog, topic_of, cluster_of)
    if conf_result is not None and conf_result.emitted:
        markers = conf_result.markers
        auc = float(conf_result.report.get("auc_full") or 0.0)
        pair_of: dict[str, str] = {}
        for row in conf_result.marked_pairs:
            a, b = row["pair"]
            pair_of.setdefault(a, b)
            pair_of.setdefault(b, a)
        for note in notes:
            suffix = cluster_of.get(note.note_id, "")
            if not suffix or suffix not in markers:
                continue
            if CONFUSABLE_TAG in note.tags:
                continue
            proposals[note.note_id].append(
                TagProposal(
                    tag=CONFUSABLE_TAG,
                    evidence=(
                        f"behavioral lapse co-occurrence with cluster "
                        f"'{pair_of.get(suffix, '?')}', validated on a 70/30 "
                        f"time split ({conf_result.reason}); confidence = "
                        "held-out validation AUC"
                    ),
                    confidence=round(auc, 3),
                    source="confusability",
                )
            )

    # -- optional missing-rung generation ------------------------------------
    generated: list[dict[str, Any]] = []
    generation_notes: list[str] = []
    if generate_missing_rungs:
        clusters: dict[str, list[NoteInfo]] = {}
        topic_of_cluster: dict[str, str] = {}
        rungs_present: dict[str, set[str]] = {}
        for note in notes:
            suffix = cluster_of.get(note.note_id, "")
            if not suffix:
                continue
            clusters.setdefault(suffix, []).append(note)
            topic = topic_of.get(note.note_id, "")
            if topic:
                topic_of_cluster.setdefault(suffix, topic)
            rung = rung_of.get(note.note_id, "")
            if rung in LADDER_RUNGS:
                rungs_present.setdefault(suffix, set()).add(rung)
        generated, generation_notes = draft_missing_rungs(
            clusters,
            topic_of_cluster,
            rungs_present,
            corpus_dir=corpus_dir,
            llm_path=llm_path,
        )

    note_proposals = [
        NoteProposal(
            note_id=note.note_id, front=note.front, tags=proposals[note.note_id]
        )
        for note in notes
        if proposals[note.note_id]
    ]
    by_source: dict[str, int] = {}
    for np in note_proposals:
        for tp in np.tags:
            by_source[tp.source] = by_source.get(tp.source, 0) + 1
    return OnboardProposal(
        notes=note_proposals,
        cluster_markers=markers,
        confusability=conf_status,
        generated_items=generated,
        generation_notes=generation_notes,
        summary={
            "n_notes": len(notes),
            "n_notes_with_proposals": len(note_proposals),
            "n_tag_proposals": sum(len(np.tags) for np in note_proposals),
            "by_source": by_source,
            "ai": ai_diag,
            "ai_backend": getattr(backend, "name", None) if backend else None,
        },
    )


# --------------------------------------------------------------------------
# The undoable apply (the ONLY writer; pylib imported by the caller)
# --------------------------------------------------------------------------


def apply(
    collection: Any,
    proposal: OnboardProposal,
    accepted: Iterable[tuple[int, str]] | None = None,
) -> int:
    """Add the accepted tags through ONE ``col.update_notes(...)`` call.

    ``accepted``: (note_id, tag) pairs the user approved; None = everything
    in the proposal. Tags are only ever ADDED (never removed, no other
    field touched), and the single bulk update is one undo step - the
    ``confusability.apply_markers`` contract. Returns notes changed.
    Nothing persists unless this is called.
    """
    accepted_set = None if accepted is None else set(accepted)
    wanted: dict[int, list[str]] = {}
    for note_proposal in proposal.notes:
        for tag_proposal in note_proposal.tags:
            pair = (note_proposal.note_id, tag_proposal.tag)
            if accepted_set is not None and pair not in accepted_set:
                continue
            wanted.setdefault(note_proposal.note_id, []).append(tag_proposal.tag)
    changed = []
    for note_id in sorted(wanted):
        note = collection.get_note(note_id)
        dirty = False
        for tag in wanted[note_id]:
            if not note.has_tag(tag):
                note.add_tag(tag)
                dirty = True
        if dirty:
            changed.append(note)
    if changed:
        collection.update_notes(changed)
    return len(changed)
