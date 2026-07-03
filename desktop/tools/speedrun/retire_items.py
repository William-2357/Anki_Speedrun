# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Auto-retire non-discriminating generated items from live responses [R24].

Phase 2's content pipeline is fully automated (no human validation), so the
compensating runtime control is that generated items must *prove themselves*
on live response data or get pulled: ungraded items never feed readiness, and
items whose responses do not discriminate between stronger and weaker recall
are auto-retired as study material.

Discrimination metric: the point-biserial correlation between an item's
per-review correctness (button > 1 on a graded, scheduling-affecting review)
and the learner's same-day accuracy on *other* cards (the criterion). An item
that cannot correlate with anything else the learner does carries no signal -
it is either broken, ambiguous, or trivially guessable.

Honesty limits (disclosed, not hidden):

* Choice-level telemetry does not exist: the solve MCQ is self-graded via the
  normal answer buttons and deliberately records no tapped choice (no JS
  bridge -> works identically on desktop and AnkiDroid). The [R22]
  "retire distractors chosen by <5% of examinees" check therefore cannot be
  computed from live data yet and is NOT implemented here; distractor-level
  pruning happens only at generation time (misconception grounding). This is
  a documented measurement gap, not a silent proxy.
* n=1 learner: the point-biserial here is within-learner across days, not the
  classic across-examinee statistic. The report labels it as such.

Usage:
    python3 retire_items.py --collection path/to/collection.anki2 \
        [--min-responses 6] [--threshold 0.1] [--report out.json] [--apply]

`--apply` adds the `aig::retired` tag to flagged notes (via pylib; requires
PYTHONPATH=out/pylib). Without it the tool only reports. Retired items keep
their history; they are excluded from readiness by tag and can be
resurrected by removing the tag.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

AIG_TAG_MARKERS = ("aig::ungraded", "aig::graded")
RETIRED_TAG = "aig::retired"
# revlog.type values that reflect real graded study (learn/review/relearn)
GRADED_REVIEW_KINDS = (0, 1, 2)


@dataclass
class Review:
    """One graded review: correct = button > 1 (Again is the only failure)."""

    card_id: int
    note_id: int
    epoch_day: int
    correct: bool


@dataclass
class ItemStats:
    note_id: int
    responses: int = 0
    correct: int = 0
    point_biserial: float | None = None
    retire: bool = False
    reason: str = ""
    pairs: list[tuple[float, float]] = field(default_factory=list)


def load_reviews_from_collection(path: Path) -> tuple[list[Review], dict[int, str]]:
    """Reviews + note tags straight from an Anki collection (read-only)."""
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            """
            SELECT r.cid, c.nid, r.id, r.ease, r.type
            FROM revlog r JOIN cards c ON c.id = r.cid
            WHERE r.ease > 0 AND r.type IN (0, 1, 2)
            ORDER BY r.id
            """
        ).fetchall()
        tag_rows = db.execute("SELECT id, tags FROM notes").fetchall()
    reviews = [
        Review(
            card_id=cid,
            note_id=nid,
            epoch_day=(rid // 1000) // 86_400,
            correct=ease > 1,
        )
        for cid, nid, rid, ease, _kind in rows
    ]
    note_tags = {nid: tags for nid, tags in tag_rows}
    return reviews, note_tags


def load_reviews_from_jsonl(path: Path) -> tuple[list[Review], dict[int, str]]:
    """JSONL rows: {card_id, note_id, id_ms, button, tags?, kind?}."""
    reviews: list[Review] = []
    note_tags: dict[int, str] = {}
    with open(path, encoding="utf8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("button", 0) <= 0:
                continue
            if row.get("kind", 1) not in GRADED_REVIEW_KINDS:
                continue
            reviews.append(
                Review(
                    card_id=row["card_id"],
                    note_id=row["note_id"],
                    epoch_day=(row["id_ms"] // 1000) // 86_400,
                    correct=row["button"] > 1,
                )
            )
            if "tags" in row:
                note_tags[row["note_id"]] = row["tags"]
    return reviews, note_tags


def aig_note_ids(note_tags: dict[int, str]) -> set[int]:
    ids = set()
    for nid, tags in note_tags.items():
        padded = f" {tags.strip()} ".lower()
        if any(f" {marker} " in padded for marker in AIG_TAG_MARKERS):
            ids.add(nid)
    return ids


def point_biserial(pairs: list[tuple[float, float]]) -> float | None:
    """Pearson r over (item_correct, criterion) pairs; None if degenerate."""
    n = len(pairs)
    if n < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        # an all-correct or all-wrong item cannot discriminate (yet)
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    return cov / math.sqrt(var_x * var_y)


def analyse(
    reviews: list[Review],
    generated_notes: set[int],
    min_responses: int,
    threshold: float,
) -> dict[int, ItemStats]:
    """Per generated note: correctness vs same-day accuracy on other notes."""
    by_day: dict[int, list[Review]] = defaultdict(list)
    for review in reviews:
        by_day[review.epoch_day].append(review)

    stats: dict[int, ItemStats] = {
        nid: ItemStats(note_id=nid) for nid in generated_notes
    }
    for day_reviews in by_day.values():
        day_total = len(day_reviews)
        day_correct = sum(r.correct for r in day_reviews)
        # group the day's reviews of each generated note
        per_note: dict[int, list[Review]] = defaultdict(list)
        for review in day_reviews:
            if review.note_id in generated_notes:
                per_note[review.note_id].append(review)
        for nid, note_reviews in per_note.items():
            item = stats[nid]
            others_total = day_total - len(note_reviews)
            if others_total <= 0:
                continue
            others_correct = day_correct - sum(r.correct for r in note_reviews)
            criterion = others_correct / others_total
            for review in note_reviews:
                item.responses += 1
                item.correct += int(review.correct)
                item.pairs.append((1.0 if review.correct else 0.0, criterion))

    for item in stats.values():
        if item.responses < min_responses:
            item.reason = (
                f"insufficient data ({item.responses}/{min_responses} responses)"
            )
            continue
        item.point_biserial = point_biserial(item.pairs)
        if item.point_biserial is None:
            item.reason = "degenerate variance (all same outcome); keep watching"
        elif item.point_biserial < threshold:
            item.retire = True
            item.reason = (
                f"point-biserial {item.point_biserial:.3f} < {threshold} "
                f"after {item.responses} responses"
            )
        else:
            item.reason = f"discriminating (r_pb {item.point_biserial:.3f})"
    return stats


def apply_retirement(collection_path: Path, note_ids: list[int]) -> None:
    """Tag flagged notes aig::retired via pylib (requires built out/pylib)."""
    from anki.collection import Collection  # deferred import
    from anki.notes import NoteId

    col = Collection(str(collection_path))
    try:
        col.tags.bulk_add([NoteId(nid) for nid in note_ids], RETIRED_TAG)
    finally:
        col.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--collection", type=Path, help="collection.anki2 path")
    source.add_argument("--revlog-jsonl", type=Path, help="revlog JSONL path")
    parser.add_argument("--min-responses", type=int, default=6)
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="tag flagged notes aig::retired (collection input only)",
    )
    args = parser.parse_args()

    if args.collection:
        reviews, note_tags = load_reviews_from_collection(args.collection)
    else:
        reviews, note_tags = load_reviews_from_jsonl(args.revlog_jsonl)
    generated = aig_note_ids(note_tags)

    stats = analyse(reviews, generated, args.min_responses, args.threshold)
    retired = sorted(nid for nid, item in stats.items() if item.retire)
    report = {
        "generated_items": len(generated),
        "with_enough_responses": sum(
            1 for item in stats.values() if item.responses >= args.min_responses
        ),
        "retired": retired,
        "threshold": args.threshold,
        "min_responses": args.min_responses,
        "note": (
            "Within-learner point-biserial (n=1): item correctness vs same-day "
            "accuracy on other cards. Distractor-level <5% pruning [R22] is "
            "not measurable live (self-graded MCQ records no tapped choice); "
            "that gap is disclosed, not proxied."
        ),
        "items": {
            str(nid): {
                "responses": item.responses,
                "correct": item.correct,
                "point_biserial": item.point_biserial,
                "retire": item.retire,
                "reason": item.reason,
            }
            for nid, item in sorted(stats.items())
        },
    }
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text, encoding="utf8")
    else:
        print(text)

    if args.apply and retired:
        if not args.collection:
            print("--apply needs --collection", file=sys.stderr)
            return 2
        apply_retirement(args.collection, retired)
        print(f"tagged {len(retired)} notes {RETIRED_TAG}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
