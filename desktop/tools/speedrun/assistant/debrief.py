# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Feature A - the post-session error-pattern debrief (read-only).

Turns a session's graded reviews into a deterministic pattern report -
topics missed, confusable pairs that co-occurred (reusing
``aig.confusability.mine_discrimination_need``), a misconception histogram
for missed MCQs - plus an optional AI narration via
``core.grounded_complete`` that abstains below ``MIN_MISTAKES_FOR_NARRATION``
mistakes or whenever the reply is ungrounded.

Everything here is a pure function over already-loaded rows: no collection
handle, no writes, no scheduling, nothing feeding Readiness.
"""

from __future__ import annotations

import glob
import html
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aig import models
from aig.confusability import (
    MS_PER_DAY,
    Review,
    _cluster_of,
    _tags_list,
    _topic_of,
    mine_discrimination_need,
)

from . import core

#: A new session starts after this much quiet time (gap-based sessionizing).
SESSION_GAP_MINUTES = 60
#: Below this many session mistakes the narration abstains and the caller
#: shows the raw table instead.
MIN_MISTAKES_FOR_NARRATION = 3
#: Confusable pairs whose full-history lapse co-occurrence lift is below
#: this are noise, not a session pattern (mirrors LABEL_MIN_LIFT in
#: aig/confusability.py).
MIN_PAIR_LIFT = 1.5
#: Reply schema demanded from the narration model.
NARRATIVE_SCHEMA = {"narrative": "str", "next_step": "str"}

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_NARRATION_SYSTEM = (
    "You are the post-session debrief narrator for a spaced-repetition "
    "study app. Narrate this session's error pattern in 3-5 sentences, "
    "referencing ONLY rows present in FACTS (topics_missed, "
    "confusable_pairs, misconceptions). State counts exactly as given; "
    "NEVER state a grade, score, percentage, or pass probability. End "
    "with exactly one concrete next step, consistent with best_next."
)


def reviews_from_rows(
    rows: Iterable[tuple[int, int, int]],
    tags_by_nid: Mapping[int, str | list[str]],
) -> list[Review]:
    """(revlog id_ms, ease, note_id) rows + note tags -> sorted Reviews.

    ``rows`` are already filtered to graded reviews (``ease >= 1``);
    ``lapse`` is ``ease == 1``. ``tags_by_nid`` values may be the raw
    space-separated ``notes.tags`` string or an already-split list; notes
    missing from the mapping count as untagged.
    """
    reviews = []
    for id_ms, ease, note_id in rows:
        tags = _tags_list(tags_by_nid.get(note_id, []))
        reviews.append(
            Review(
                note_id=note_id,
                cluster=_cluster_of(tags),
                topic=_topic_of(tags),
                lapse=ease == 1,
                day=id_ms // MS_PER_DAY,
                id_ms=id_ms,
            )
        )
    reviews.sort(key=lambda r: r.id_ms)
    return reviews


def sessionize(
    reviews: list[Review], gap_minutes: int = SESSION_GAP_MINUTES
) -> list[Review]:
    """The trailing contiguous session: walk back from the newest review
    until a gap longer than ``gap_minutes`` appears.

    A gap of exactly ``gap_minutes`` still extends the session. Returns the
    trailing run in ascending ``id_ms`` order; empty input -> ``[]``.
    """
    if not reviews:
        return []
    ordered = sorted(reviews, key=lambda r: r.id_ms)
    gap_ms = gap_minutes * 60_000
    start = len(ordered) - 1
    while start > 0 and ordered[start].id_ms - ordered[start - 1].id_ms <= gap_ms:
        start -= 1
    return ordered[start:]


def _normalize(text: Any) -> str:
    """One key for item plain-text AND the html-escaped note field built
    from it: unescape entities, strip tags, collapse whitespace, casefold."""
    unescaped = html.unescape(str(text))
    stripped = _HTML_TAG_RE.sub(" ", unescaped)
    return " ".join(stripped.split()).casefold()


def load_misconception_index(items_glob: str) -> dict[str, dict[str, str]]:
    """speedrun-item-v1 records -> normalized stem/title ->
    {wrong letter: misconception id}. Missing files -> {}.

    Only records carrying a non-empty ``misconceptions`` mapping (the
    MCQ/solve items) are indexed, under BOTH their normalized ``title`` and
    normalized ``stem``; on key collisions the later record (files sorted,
    lines in order) wins. Unreadable files/lines are skipped silently.
    """
    index: dict[str, dict[str, str]] = {}
    for path in sorted(glob.glob(items_glob)):
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            misconceptions = record.get("misconceptions")
            if not isinstance(misconceptions, Mapping) or not misconceptions:
                continue
            entry = {str(k): str(v) for k, v in misconceptions.items()}
            for key_text in (record.get("title"), record.get("stem")):
                key = _normalize(key_text) if isinstance(key_text, str) else ""
                if key:
                    index[key] = entry
    return index


def misconceptions_for_notes(
    index: Mapping[str, Mapping[str, str]],
    fields_by_nid: Mapping[int, list[str]],
) -> dict[int, list[str]]:
    """Match each note's leading fields against the item index.

    Notes built from ladder items store Title in field 0 and Stem in field 1
    (both html-escaped at build time); a normalized hit on either collects
    that record's misconception ids. Returns {nid: sorted deduped ids},
    hits only.
    """
    out: dict[int, list[str]] = {}
    for nid, fields in fields_by_nid.items():
        ids: set[str] = set()
        for field_text in list(fields)[:2]:
            entry = index.get(_normalize(field_text))
            if entry:
                ids.update(str(value) for value in entry.values())
        if ids:
            out[nid] = sorted(ids)
    return out


def build_report(
    all_reviews: list[Review],
    *,
    session: list[Review] | None = None,
    misconceptions_by_nid: Mapping[int, list[str]] | None = None,
    gap_minutes: int = SESSION_GAP_MINUTES,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """A2: the deterministic pattern report, or None with no session data.

    Shape (all counts computed here, never by the model):
    {
      "window": {"start_ms", "end_ms", "n_reviews", "n_lapses", "gap_minutes"},
      "topics_missed": [{"topic", "lapses", "reviews"}],
      "confusable_pairs": [{"pair": [a, b], "lift", "session_lapses"}],
      "misconceptions": [{"id", "count"}],
      "best_next": str,
    }

    ``session`` defaults to ``sessionize(all_reviews, gap_minutes)``; an
    empty session (computed or supplied) -> ``None``. Confusable pairs come
    from ``mine_discrimination_need`` over the FULL history (stable base
    rates), kept only when both clusters were reviewed this session, at
    least one of them lapsed this session, and lift >= ``MIN_PAIR_LIFT``.
    The misconception histogram counts ids over the set of missed note ids.
    Every list is sorted deterministically and truncated to ``top_n``.
    """
    if session is None:
        session = sessionize(all_reviews, gap_minutes)
    if not session:
        return None

    window = {
        "start_ms": session[0].id_ms,
        "end_ms": session[-1].id_ms,
        "n_reviews": len(session),
        "n_lapses": sum(1 for r in session if r.lapse),
        "gap_minutes": gap_minutes,
    }

    lapses_by_topic: Counter[str] = Counter()
    reviews_by_topic: Counter[str] = Counter()
    for r in session:
        topic = r.topic or "(untagged)"
        reviews_by_topic[topic] += 1
        if r.lapse:
            lapses_by_topic[topic] += 1
    topics_missed = [
        {"topic": topic, "lapses": lapses, "reviews": reviews_by_topic[topic]}
        for topic, lapses in lapses_by_topic.items()
    ]
    topics_missed.sort(key=lambda row: (-row["lapses"], row["topic"]))
    topics_missed = topics_missed[:top_n]

    session_clusters = {r.cluster for r in session if r.cluster}
    lapsed_clusters = {r.cluster for r in session if r.lapse and r.cluster}
    confusable_pairs = []
    for (a, b), stats in mine_discrimination_need(all_reviews).items():
        if a not in session_clusters or b not in session_clusters:
            continue
        if a not in lapsed_clusters and b not in lapsed_clusters:
            continue
        if stats.lift < MIN_PAIR_LIFT:
            continue
        confusable_pairs.append(
            {
                "pair": [a, b],
                "lift": round(stats.lift, 2),
                "session_lapses": sum(
                    1 for r in session if r.lapse and r.cluster in (a, b)
                ),
            }
        )
    confusable_pairs.sort(key=lambda row: (-row["lift"], row["pair"]))
    confusable_pairs = confusable_pairs[:top_n]

    misconceptions_by_nid = misconceptions_by_nid or {}
    histogram: Counter[str] = Counter()
    for nid in {r.note_id for r in session if r.lapse}:
        for misconception_id in misconceptions_by_nid.get(nid, []):
            histogram[str(misconception_id)] += 1
    misconceptions = [
        {"id": misconception_id, "count": count}
        for misconception_id, count in histogram.items()
    ]
    misconceptions.sort(key=lambda row: (-row["count"], row["id"]))
    misconceptions = misconceptions[:top_n]

    if confusable_pairs:
        a, b = confusable_pairs[0]["pair"]
        best_next = f"Drill {a} vs {b} back-to-back - their lapses co-occur."
    elif topics_missed:
        top = topics_missed[0]
        best_next = f"Review {top['topic']}: {top['lapses']} lapses this session."
    else:
        best_next = "No repeated error pattern this session - keep going."

    return {
        "window": window,
        "topics_missed": topics_missed,
        "confusable_pairs": confusable_pairs,
        "misconceptions": misconceptions,
        "best_next": best_next,
    }


def narrate(
    report: Mapping[str, Any],
    backend: models.Backend,
    *,
    min_mistakes: int = MIN_MISTAKES_FOR_NARRATION,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """A3: 3-5 grounded sentences + one next step, or None (abstain).

    Below ``min_mistakes`` session lapses this abstains WITHOUT calling the
    model (there is no pattern to narrate); otherwise it makes one
    ``core.grounded_complete`` call whose facts are exactly the report, so
    every number the model may state was computed deterministically here.
    """
    n_lapses = report["window"]["n_lapses"]
    if n_lapses < min_mistakes:
        if diagnostics is not None:
            diagnostics["outcome"] = "abstained"
            diagnostics["reason"] = (
                f"only {n_lapses} mistakes in this session (< {min_mistakes}); "
                "showing the deterministic table"
            )
        return None
    return core.grounded_complete(
        _NARRATION_SYSTEM,
        report,
        schema=NARRATIVE_SCHEMA,
        backend=backend,
        task="debrief",
        diagnostics=diagnostics,
    )
