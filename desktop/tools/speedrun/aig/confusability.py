# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Computed confusability signal (M1b / [R18]) - no human labels anywhere.

    confusability(a, b) = surface_similarity(a, b) x discrimination_need(a, b)

- ``surface_similarity``: token-level Jaccard similarity of the two clusters'
  note fronts (the BM25/embedding-similarity-style surface term). If fronts
  are unavailable (tags-only revlog and no notes file) it falls back to
  cluster-name token Jaccard, which is weak and is flagged in the report.
- ``discrimination_need``: behavioral confusion-mining from the revlog -
  error-substitution / lapse co-occurrence: the tendency of cluster-a lapses
  (button == 1) to occur in temporal proximity (same day-session) to
  cluster-b reviews or lapses, normalized against base rates (a smoothed
  lift ratio over sessions). Scoped to WITHIN-TOPIC pairs only.

AUTO-VALIDATION (before any marker is emitted): the revlog is split by time
(first 70% of reviews train / last 30% held-out). The full score computed on
the train window must beat the surface-similarity-only baseline (the
BM25/embedding-similarity-only arm) at predicting held-out lapse
co-occurrence, measured by AUC (and precision@1 reported). If it cannot -
too little data, no positive pairs, or baseline >= full - the tool ABSTAINS
and emits no markers (honest failure).

Output: a confusability report (eval/confusability_report.json) + a markers
file (cluster -> ``confusable::high``) consumable as ``tags_extra`` by the
deck builder, and an optional ``--apply`` mode that adds the tag to notes
via pylib (code shipped, not run at authoring time).

Input: either a SQLite collection (collection.anki2) or a JSONL of revlog
rows ``{card_id, note_id, tags, button, id_ms}`` (tags = list or
space-separated string), plus an optional notes JSONL
``{note_id, front, tags}`` supplying fronts for the surface term.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig.pdf_text import tokenize

MS_PER_DAY = 86_400_000

TRAIN_FRACTION = 0.7
# A pair is a held-out POSITIVE (behavioral confusion) when its held-out
# lapse co-occurrence clears both bars:
LABEL_MIN_CO_SESSIONS = 2
LABEL_MIN_LIFT = 1.5
# Markers are written for pairs whose TRAIN window shows strong confusion:
MARKER_MIN_CO_SESSIONS = 2
MARKER_MIN_LIFT = 2.0
LAPLACE_ALPHA = 1.0

CONFUSABLE_TAG = "confusable::high"


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


@dataclass
class Review:
    note_id: int
    cluster: str  # cluster:: suffix, "" when untagged
    topic: str  # cfa::topic:: suffix, "" when untagged
    lapse: bool  # button == 1
    day: int  # id_ms // MS_PER_DAY (UTC day-session)
    id_ms: int


def _tags_list(tags: Any) -> list[str]:
    if isinstance(tags, str):
        return tags.split()
    if isinstance(tags, list):
        return [str(t) for t in tags]
    return []


def _cluster_of(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("cluster::"):
            return t[len("cluster::") :]
    return ""


def _topic_of(tags: list[str]) -> str:
    for t in tags:
        if t.startswith("cfa::topic::"):
            return t[len("cfa::topic::") :]
    return ""


def load_revlog_jsonl(path: str | Path) -> list[Review]:
    reviews: list[Review] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tags = _tags_list(row.get("tags"))
        reviews.append(
            Review(
                note_id=int(row.get("note_id", 0)),
                cluster=_cluster_of(tags),
                topic=_topic_of(tags),
                lapse=int(row.get("button", 0)) == 1,
                day=int(row["id_ms"]) // MS_PER_DAY,
                id_ms=int(row["id_ms"]),
            )
        )
    reviews.sort(key=lambda r: r.id_ms)
    return reviews


def load_revlog_sqlite(path: str | Path) -> tuple[list[Review], dict[int, str]]:
    """Read an Anki collection DB. Returns (reviews, note fronts)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        note_tags: dict[int, list[str]] = {}
        fronts: dict[int, str] = {}
        for nid, tags, flds in con.execute("select id, tags, flds from notes"):
            note_tags[nid] = _tags_list(tags)
            fronts[nid] = str(flds).split("\x1f", 1)[0]
        card_note = dict(con.execute("select id, nid from cards"))
        reviews: list[Review] = []
        for rid, cid, ease in con.execute("select id, cid, ease from revlog"):
            nid = card_note.get(cid)
            if nid is None:
                continue
            tags = note_tags.get(nid, [])
            reviews.append(
                Review(
                    note_id=nid,
                    cluster=_cluster_of(tags),
                    topic=_topic_of(tags),
                    lapse=ease == 1,
                    day=rid // MS_PER_DAY,
                    id_ms=rid,
                )
            )
        reviews.sort(key=lambda r: r.id_ms)
        return reviews, fronts
    finally:
        con.close()


def load_notes_jsonl(path: str | Path) -> dict[int, str]:
    """Optional {note_id, front, tags} JSONL supplying fronts."""
    fronts: dict[int, str] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        fronts[int(row["note_id"])] = str(row.get("front", ""))
    return fronts


# ---------------------------------------------------------------------------
# Surface similarity
# ---------------------------------------------------------------------------


def cluster_front_tokens(
    reviews: Iterable[Review], fronts: dict[int, str]
) -> dict[str, set[str]]:
    toks: dict[str, set[str]] = defaultdict(set)
    for r in reviews:
        if r.cluster and r.note_id in fronts:
            toks[r.cluster] |= set(tokenize(fronts[r.note_id]))
    return dict(toks)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def surface_similarity(
    a: str, b: str, front_tokens: dict[str, set[str]]
) -> tuple[float, str]:
    """Front-token Jaccard; falls back to cluster-NAME Jaccard when no fronts.

    Returns (similarity, basis) where basis records which signal was used.
    """
    ta, tb = front_tokens.get(a), front_tokens.get(b)
    if ta and tb:
        return jaccard(ta, tb), "fronts"
    return jaccard(set(tokenize(a)), set(tokenize(b))), "cluster-names(weak)"


# ---------------------------------------------------------------------------
# Behavioral confusion mining (discrimination_need)
# ---------------------------------------------------------------------------


@dataclass
class PairStats:
    co_sessions_ab: int = 0  # sessions with a-lapse AND b-presence
    co_sessions_ba: int = 0  # sessions with b-lapse AND a-presence
    lift: float = 0.0  # smoothed symmetric lift vs base rates
    sessions: int = 0


def mine_discrimination_need(reviews: list[Review]) -> dict[tuple[str, str], PairStats]:
    """Lapse co-occurrence lift for every within-topic cluster pair.

    A "session" is a UTC day. For the ordered direction (a, b):
    observed = #sessions(a lapses AND b present); expected under independence
    = #sessions(a lapses) * #sessions(b present) / N. The pair statistic is
    the Laplace-smoothed mean of the two directed lifts.
    """
    lapse_days: dict[str, set[int]] = defaultdict(set)
    present_days: dict[str, set[int]] = defaultdict(set)
    topic_of: dict[str, str] = {}
    all_days: set[int] = set()
    for r in reviews:
        if not r.cluster:
            continue
        all_days.add(r.day)
        present_days[r.cluster].add(r.day)
        if r.lapse:
            lapse_days[r.cluster].add(r.day)
        if r.topic:
            topic_of.setdefault(r.cluster, r.topic)

    n_days = len(all_days)
    stats: dict[tuple[str, str], PairStats] = {}
    clusters = sorted(present_days)
    for i, a in enumerate(clusters):
        for b in clusters[i + 1 :]:
            if topic_of.get(a) != topic_of.get(b) or not topic_of.get(a):
                continue  # within-topic pairs only [R18]
            co_ab = len(lapse_days[a] & present_days[b])
            co_ba = len(lapse_days[b] & present_days[a])
            lifts = []
            for co, la, pr in (
                (co_ab, len(lapse_days[a]), len(present_days[b])),
                (co_ba, len(lapse_days[b]), len(present_days[a])),
            ):
                expected = (la * pr) / n_days if n_days else 0.0
                lifts.append((co + LAPLACE_ALPHA) / (expected + LAPLACE_ALPHA))
            stats[(a, b)] = PairStats(
                co_sessions_ab=co_ab,
                co_sessions_ba=co_ba,
                lift=sum(lifts) / 2.0,
                sessions=n_days,
            )
    return stats


# ---------------------------------------------------------------------------
# AUC (Mann-Whitney, tie-aware) - stdlib only
# ---------------------------------------------------------------------------


def auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def precision_at_1(scores: list[float], labels: list[bool]) -> float | None:
    if not scores:
        return None
    best = max(range(len(scores)), key=lambda i: scores[i])
    return 1.0 if labels[best] else 0.0


# ---------------------------------------------------------------------------
# The full computed pass with time-split auto-validation
# ---------------------------------------------------------------------------


@dataclass
class ConfusabilityResult:
    emitted: bool
    reason: str
    markers: dict[str, list[str]] = field(default_factory=dict)
    marked_pairs: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def compute(
    reviews: list[Review],
    fronts: dict[int, str] | None = None,
) -> ConfusabilityResult:
    fronts = fronts or {}
    if len(reviews) < 20:
        return ConfusabilityResult(
            False,
            f"too little data: {len(reviews)} reviews (<20); abstaining",
            report={"n_reviews": len(reviews)},
        )

    # --- time split: first 70% train / last 30% held-out ------------------
    cut = int(len(reviews) * TRAIN_FRACTION)
    train_rows, holdout_rows = reviews[:cut], reviews[cut:]

    front_tokens = cluster_front_tokens(reviews, fronts)
    train_stats = mine_discrimination_need(train_rows)
    holdout_stats = mine_discrimination_need(holdout_rows)

    pairs = sorted(set(train_stats) & set(holdout_stats))
    if not pairs:
        return ConfusabilityResult(
            False,
            "no within-topic cluster pair appears in both windows; abstaining",
            report={"n_reviews": len(reviews), "n_pairs": 0},
        )

    rows: list[dict[str, Any]] = []
    full_scores: list[float] = []
    base_scores: list[float] = []
    labels: list[bool] = []
    surface_bases: set[str] = set()
    for a, b in pairs:
        sim, basis = surface_similarity(a, b, front_tokens)
        surface_bases.add(basis)
        t = train_stats[(a, b)]
        h = holdout_stats[(a, b)]
        full = sim * t.lift
        label = (
            min(h.co_sessions_ab, h.co_sessions_ba)
            + max(h.co_sessions_ab, h.co_sessions_ba)
            >= LABEL_MIN_CO_SESSIONS
            and h.lift >= LABEL_MIN_LIFT
        )
        rows.append(
            {
                "pair": [a, b],
                "surface_similarity": round(sim, 4),
                "surface_basis": basis,
                "train_lift": round(t.lift, 4),
                "train_co_sessions": t.co_sessions_ab + t.co_sessions_ba,
                "holdout_lift": round(h.lift, 4),
                "holdout_co_sessions": h.co_sessions_ab + h.co_sessions_ba,
                "confusability": round(full, 4),
                "holdout_label": label,
            }
        )
        full_scores.append(full)
        base_scores.append(sim)
        labels.append(label)

    auc_full = auc(full_scores, labels)
    auc_base = auc(base_scores, labels)
    p1_full = precision_at_1(full_scores, labels)
    p1_base = precision_at_1(base_scores, labels)

    report: dict[str, Any] = {
        "method": (
            "confusability(a,b) = surface_similarity x discrimination_need; "
            "surface = token Jaccard of note fronts (fallback: cluster-name "
            "tokens); discrimination_need = smoothed lapse co-occurrence "
            "lift over day-sessions, within-topic only [R18]; validated on "
            "a 70/30 time split against held-out lapse co-occurrence - "
            "fully computed, no human labels (owner decision 2026-07-02)"
        ),
        "n_reviews": len(reviews),
        "n_train_reviews": len(train_rows),
        "n_holdout_reviews": len(holdout_rows),
        "n_pairs": len(pairs),
        "n_holdout_positive_pairs": sum(labels),
        "surface_bases_used": sorted(surface_bases),
        "label_rule": (
            f"holdout co-occurrence sessions >= {LABEL_MIN_CO_SESSIONS} and "
            f"holdout lift >= {LABEL_MIN_LIFT}"
        ),
        "marker_rule": (
            f"train co-occurrence sessions >= {MARKER_MIN_CO_SESSIONS} and "
            f"train lift >= {MARKER_MIN_LIFT}, only when validation beats "
            "the baseline"
        ),
        "auc_full": auc_full,
        "auc_baseline_surface_only": auc_base,
        "precision_at_1_full": p1_full,
        "precision_at_1_baseline": p1_base,
        "pairs": rows,
    }

    if auc_full is None or auc_base is None:
        return ConfusabilityResult(
            False,
            "validation impossible: held-out labels are one-class "
            "(no positive or no negative pairs); abstaining",
            report=report,
        )
    if auc_full <= auc_base:
        return ConfusabilityResult(
            False,
            f"full score (AUC {auc_full:.3f}) does not beat the surface-"
            f"similarity-only baseline (AUC {auc_base:.3f}); abstaining",
            report=report,
        )

    # --- validated: emit markers from the train window --------------------
    markers: dict[str, list[str]] = {}
    marked_pairs: list[dict[str, Any]] = []
    for row, (a, b) in zip(rows, pairs):
        t = train_stats[(a, b)]
        if (
            t.co_sessions_ab + t.co_sessions_ba >= MARKER_MIN_CO_SESSIONS
            and t.lift >= MARKER_MIN_LIFT
        ):
            markers.setdefault(a, [CONFUSABLE_TAG])
            markers.setdefault(b, [CONFUSABLE_TAG])
            marked_pairs.append(row)
    if not marked_pairs:
        return ConfusabilityResult(
            False,
            "validation passed but no pair clears the marker thresholds; "
            "no markers emitted",
            report=report,
        )
    return ConfusabilityResult(
        True,
        f"validated: AUC {auc_full:.3f} > baseline {auc_base:.3f}; "
        f"{len(marked_pairs)} pair(s) marked",
        markers=markers,
        marked_pairs=marked_pairs,
        report=report,
    )


# ---------------------------------------------------------------------------
# Synthetic self-test revlog (proves the mining works end to end)
# ---------------------------------------------------------------------------


def synthetic_revlog(seed: int = 20260703) -> tuple[list[Review], dict[int, str]]:
    """Engineered revlog: clusters a/b confuse each other, c/d do not.

    - fi::duration (a) and fi::convexity (b): a's lapses repeatedly land in
      the same day-sessions as b reviews/lapses (error co-occurrence),
      throughout the whole timeline (so both the train and holdout windows
      see it).
    - fi::credit (c) and fi::creditx (d): HIGH surface similarity (near-
      identical fronts) but statistically independent behavior - the
      surface-only baseline ranks this dead pair top; the behavioral term
      must demote it.
    """
    rng = random.Random(seed)
    fronts = {
        1: "duration of a bond measures interest rate price sensitivity",
        2: "convexity of a bond measures curvature of price sensitivity",
        3: "credit spread risk premium of a corporate bond default",
        4: "credit spread risk premium of a corporate bond rating",
    }
    cluster_note = {
        "fi::duration": 1,
        "fi::convexity": 2,
        "fi::credit": 3,
        "fi::creditx": 4,
    }
    topic = "fixed_income"
    reviews: list[Review] = []

    def add(day: int, cluster: str, lapse: bool, offset_ms: int) -> None:
        reviews.append(
            Review(
                note_id=cluster_note[cluster],
                cluster=cluster,
                topic=topic,
                lapse=lapse,
                day=day,
                id_ms=day * MS_PER_DAY + offset_ms,
            )
        )

    for day in range(40):
        # Engineered pair: every 4th day, a duration LAPSE lands in the same
        # session as a convexity review (often lapsing too) - and convexity
        # is reviewed ONLY on those days, so the co-occurrence is far above
        # its base rate. Duration is also reviewed cleanly on other days.
        if day % 4 == 0:
            add(day, "fi::duration", True, 1_000_000)
            add(day, "fi::convexity", rng.random() < 0.5, 2_000_000)
        elif day % 4 == 2:
            add(day, "fi::duration", False, 1_500_000)
        # Dead pair: c and d are surface-twins but reviewed on DISJOINT days
        # (no temporal proximity), lapsing occasionally - the surface-only
        # baseline ranks this pair top; behavior must demote it.
        if day % 4 == 1:
            add(day, "fi::credit", rng.random() < 0.3, 3_000_000)
        if day % 4 == 3:
            add(day, "fi::creditx", rng.random() < 0.3, 3_500_000)

    reviews.sort(key=lambda r: r.id_ms)
    return reviews, fronts


# ---------------------------------------------------------------------------
# --apply mode: write confusable::high onto notes via pylib (code only; not
# executed at authoring time - runs only when a user passes --apply).
# ---------------------------------------------------------------------------


def apply_markers(collection_path: str, markers: dict[str, list[str]]) -> int:
    """Add the confusable::high tag to every note of each marked cluster.

    Requires pylib (run under the repo's python env, e.g.
    PYTHONPATH=out/pylib). Returns the number of notes updated.
    """
    from anki.collection import Collection  # deferred: pylib optional

    col = Collection(collection_path)
    updated = 0
    try:
        for cluster, tags in sorted(markers.items()):
            note_ids = col.find_notes(f"tag:cluster::{cluster}")
            for nid in note_ids:
                note = col.get_note(nid)
                changed = False
                for tag in tags:
                    if not note.has_tag(tag):
                        note.add_tag(tag)
                        changed = True
                if changed:
                    col.update_note(note)
                    updated += 1
    finally:
        col.close()
    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    revlog: str | None,
    notes: str | None,
    out_report: str | Path,
    out_markers: str | Path,
    apply_to: str | None = None,
    self_test: bool = False,
) -> ConfusabilityResult:
    if self_test:
        reviews, fronts = synthetic_revlog()
        source = "synthetic-self-test"
    elif revlog and revlog.endswith((".anki2", ".sqlite", ".db")):
        reviews, fronts = load_revlog_sqlite(revlog)
        source = revlog
    elif revlog:
        reviews = load_revlog_jsonl(revlog)
        fronts = load_notes_jsonl(notes) if notes else {}
        source = revlog
    else:
        raise SystemExit("need --revlog or --self-test")

    result = compute(reviews, fronts)
    payload = {
        "meta": {
            "input": source,
            "no_human_labels": True,
            "session_definition": "UTC day buckets (id_ms // 86,400,000)",
            "decision": "markers emitted" if result.emitted else "ABSTAINED",
            "reason": result.reason,
        },
        "report": result.report,
        "markers": result.markers,
        "marked_pairs": result.marked_pairs,
    }
    rp = Path(out_report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(payload, indent=1) + "\n")

    mp = Path(out_markers)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(
        json.dumps(
            {
                "consumable_as": "tags_extra by the deck builder",
                "input": source,
                "emitted": result.emitted,
                "markers": result.markers,
            },
            indent=1,
        )
        + "\n"
    )

    if apply_to and result.emitted:
        updated = apply_markers(apply_to, result.markers)
        print(f"applied markers to {updated} notes in {apply_to}")

    return result


def main(argv: list[str] | None = None) -> int:
    speedrun = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--revlog", help="collection.anki2 or revlog JSONL")
    ap.add_argument("--notes", help="optional notes JSONL {note_id, front, tags}")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="run on the engineered synthetic revlog instead of real input",
    )
    ap.add_argument(
        "--out-report", default=str(speedrun / "eval" / "confusability_report.json")
    )
    ap.add_argument(
        "--out-markers", default=str(speedrun / "eval" / "confusable_markers.json")
    )
    ap.add_argument(
        "--apply",
        metavar="COLLECTION",
        help="ALSO write confusable::high tags into this collection via pylib",
    )
    args = ap.parse_args(argv)
    result = run(
        args.revlog,
        args.notes,
        args.out_report,
        args.out_markers,
        args.apply,
        args.self_test,
    )
    print(result.reason)
    if result.emitted:
        for cluster in sorted(result.markers):
            print(f"  {cluster} -> {CONFUSABLE_TAG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
