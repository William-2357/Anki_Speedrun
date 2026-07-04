# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Feature A tests: the post-session error-pattern debrief (assistant/debrief.py)."""

from __future__ import annotations

import html
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig import confusability as C  # noqa: E402
from assistant import core, debrief  # noqa: E402

MS_PER_DAY = C.MS_PER_DAY
MS_PER_MINUTE = 60_000


def _review(
    minute: int,
    *,
    lapse: bool = False,
    note_id: int = 1,
    cluster: str = "",
    topic: str = "",
) -> C.Review:
    id_ms = minute * MS_PER_MINUTE
    return C.Review(
        note_id=note_id,
        cluster=cluster,
        topic=topic,
        lapse=lapse,
        day=id_ms // MS_PER_DAY,
        id_ms=id_ms,
    )


def _synthetic_history() -> tuple[list[C.Review], list[C.Review]]:
    """The engineered revlog plus a crafted trailing session.

    The raw synthetic history's own trailing session is a lone day-39
    review (day gaps far exceed 60 minutes), so we append one session a
    quiet week later in which fi::duration, fi::convexity and fi::credit
    lapse and fi::creditx is reviewed cleanly. Every cluster is then
    present this session and at least one side of every pair lapsed, so
    the ONLY thing keeping a pair out of the report is the full-history
    lift threshold - exactly what the dead-pair test must prove.
    """
    reviews, _fronts = C.synthetic_revlog()
    quiet_day = max(r.day for r in reviews) + 10
    base = quiet_day * MS_PER_DAY
    topic = "fixed_income"
    session = [
        C.Review(1, "fi::duration", topic, True, quiet_day, base),
        C.Review(2, "fi::convexity", topic, True, quiet_day, base + MS_PER_MINUTE),
        C.Review(3, "fi::credit", topic, True, quiet_day, base + 2 * MS_PER_MINUTE),
        C.Review(4, "fi::creditx", topic, False, quiet_day, base + 3 * MS_PER_MINUTE),
    ]
    return reviews + session, session


# --------------------------------------------------------------------------
# Misconception-index fixtures (speedrun-item-v1 JSONL)
# --------------------------------------------------------------------------

MCQ_TITLE = "Duration price estimate & sign"
MCQ_STEM = (
    "A bond has a modified duration of 6.5. If its yield rises by 50 bp, "
    "the approximate percentage price change is closest to:"
)
MCQ_MISCONCEPTIONS = {"B": "duration.sign_direction", "C": "duration.shock_scaling"}
MCQ_IDS_SORTED = ["duration.shock_scaling", "duration.sign_direction"]
WORKED_TITLE = "Modified duration from Macaulay duration"


def _write_items(tmp: Path) -> str:
    """One MCQ with misconceptions, one worked item without, one MCQ with an
    empty map, plus a malformed line and a blank line (skipped silently).
    Returns the glob pattern."""
    records = [
        {
            "schema": "speedrun-item-v1",
            "kind": "mcq",
            "title": MCQ_TITLE,
            "stem": MCQ_STEM,
            "misconceptions": MCQ_MISCONCEPTIONS,
        },
        {
            "schema": "speedrun-item-v1",
            "kind": "worked",
            "title": WORKED_TITLE,
            "prompt": "Compute modified duration from Macaulay duration.",
        },
        {
            "schema": "speedrun-item-v1",
            "kind": "mcq",
            "title": "Empty misconception map",
            "stem": "This solve item tagged no misconceptions.",
            "misconceptions": {},
        },
    ]
    lines = [json.dumps(record) for record in records]
    lines.insert(1, "{this line is not JSON")
    lines.append("")
    (tmp / "items.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(tmp / "*.jsonl")


class _CountingExplodingBackend:
    """Fails loudly if narrate touches the model; counts attempts."""

    name = "exploding"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        self.calls += 1
        raise AssertionError("narrate must not call the backend")


def _report(n_lapses: int) -> dict:
    return {
        "window": {
            "start_ms": 0,
            "end_ms": 4 * MS_PER_MINUTE,
            "n_reviews": 6,
            "n_lapses": n_lapses,
            "gap_minutes": 60,
        },
        "topics_missed": [{"topic": "fixed_income", "lapses": n_lapses, "reviews": 6}],
        "confusable_pairs": [
            {
                "pair": ["fi::convexity", "fi::duration"],
                "lift": 2.43,
                "session_lapses": 3,
            }
        ],
        "misconceptions": [{"id": "duration.sign_direction", "count": 2}],
        "best_next": "Drill fi::convexity vs fi::duration back-to-back - their lapses co-occur.",
    }


# --------------------------------------------------------------------------
# reviews_from_rows
# --------------------------------------------------------------------------


class ReviewsFromRowsTests(unittest.TestCase):
    def test_tags_lapse_and_sorting(self) -> None:
        rows = [
            (2 * MS_PER_DAY + 500, 1, 11),  # lapse, newest, string tags
            (1_000, 3, 12),  # pass, oldest, list tags
            (MS_PER_DAY + 1, 2, 13),  # "hard" is not a lapse; untagged note
        ]
        tags_by_nid: dict[int, str | list[str]] = {
            11: "other::tag cluster::fi::duration cfa::topic::fixed_income",
            12: ["cluster::fi::convexity", "cfa::topic::fixed_income"],
        }
        reviews = debrief.reviews_from_rows(rows, tags_by_nid)
        self.assertEqual([r.note_id for r in reviews], [12, 13, 11])
        self.assertEqual(
            [r.id_ms for r in reviews],
            [1_000, MS_PER_DAY + 1, 2 * MS_PER_DAY + 500],
        )

        by_nid = {r.note_id: r for r in reviews}
        self.assertEqual(by_nid[11].cluster, "fi::duration")
        self.assertEqual(by_nid[11].topic, "fixed_income")
        self.assertTrue(by_nid[11].lapse)
        self.assertEqual(by_nid[11].day, 2)

        self.assertEqual(by_nid[12].cluster, "fi::convexity")
        self.assertFalse(by_nid[12].lapse)
        self.assertEqual(by_nid[12].day, 0)

        self.assertEqual(by_nid[13].cluster, "")
        self.assertEqual(by_nid[13].topic, "")
        self.assertFalse(by_nid[13].lapse)
        self.assertEqual(by_nid[13].day, 1)

    def test_empty_rows(self) -> None:
        self.assertEqual(debrief.reviews_from_rows([], {}), [])


# --------------------------------------------------------------------------
# sessionize
# --------------------------------------------------------------------------


class SessionizeTests(unittest.TestCase):
    def test_gap_over_an_hour_splits_the_trailing_session(self) -> None:
        reviews = [_review(m) for m in (0, 30, 95, 120, 150)]
        session = debrief.sessionize(reviews)
        # 95 -> 30 is a 65-minute gap: the run older than it is excluded.
        self.assertEqual([r.id_ms // MS_PER_MINUTE for r in session], [95, 120, 150])

    def test_exact_gap_still_extends(self) -> None:
        reviews = [_review(0), _review(60)]
        self.assertEqual(len(debrief.sessionize(reviews)), 2)

    def test_custom_gap_minutes(self) -> None:
        reviews = [_review(0), _review(11)]
        self.assertEqual(len(debrief.sessionize(reviews, gap_minutes=10)), 1)
        self.assertEqual(len(debrief.sessionize(reviews, gap_minutes=11)), 2)

    def test_unsorted_input_is_sorted_first(self) -> None:
        reviews = [_review(150), _review(0), _review(120), _review(95)]
        session = debrief.sessionize(reviews)
        self.assertEqual([r.id_ms // MS_PER_MINUTE for r in session], [95, 120, 150])

    def test_single_review(self) -> None:
        only = _review(7)
        self.assertEqual(debrief.sessionize([only]), [only])

    def test_empty(self) -> None:
        self.assertEqual(debrief.sessionize([]), [])


# --------------------------------------------------------------------------
# build_report on the engineered synthetic revlog
# --------------------------------------------------------------------------


class SyntheticReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.history, cls.session = _synthetic_history()
        cls.report = debrief.build_report(cls.history)

    def test_window_is_the_trailing_session(self) -> None:
        self.assertIsNotNone(self.report)
        window = self.report["window"]
        self.assertEqual(window["start_ms"], self.session[0].id_ms)
        self.assertEqual(window["end_ms"], self.session[-1].id_ms)
        self.assertEqual(window["n_reviews"], 4)
        self.assertEqual(window["n_lapses"], 3)
        self.assertEqual(window["gap_minutes"], debrief.SESSION_GAP_MINUTES)

    def test_topics_missed_counts(self) -> None:
        self.assertEqual(
            self.report["topics_missed"],
            [{"topic": "fixed_income", "lapses": 3, "reviews": 4}],
        )

    def test_engineered_pair_reported_dead_pair_excluded(self) -> None:
        rows = self.report["confusable_pairs"]
        pairs = [tuple(row["pair"]) for row in rows]
        self.assertIn(("fi::convexity", "fi::duration"), pairs)
        engineered = rows[pairs.index(("fi::convexity", "fi::duration"))]
        self.assertGreaterEqual(engineered["lift"], debrief.MIN_PAIR_LIFT)
        # Its session lapses: the duration lapse + the convexity lapse.
        self.assertEqual(engineered["session_lapses"], 2)
        # The surface-twin dead pair was present this session, credit even
        # lapsed - so only the full-history lift filter can exclude it.
        for row in rows:
            self.assertNotIn("fi::credit", row["pair"])
            self.assertNotIn("fi::creditx", row["pair"])

    def test_dead_pair_excluded_by_lift_not_by_presence(self) -> None:
        stats = C.mine_discrimination_need(self.history)
        self.assertLess(
            stats[("fi::credit", "fi::creditx")].lift, debrief.MIN_PAIR_LIFT
        )
        self.assertGreaterEqual(
            stats[("fi::convexity", "fi::duration")].lift, debrief.MIN_PAIR_LIFT
        )

    def test_best_next_names_the_confusable_drill(self) -> None:
        self.assertEqual(
            self.report["best_next"],
            "Drill fi::convexity vs fi::duration back-to-back - their lapses co-occur.",
        )

    def test_none_for_empty_history_or_empty_session(self) -> None:
        self.assertIsNone(debrief.build_report([]))
        self.assertIsNone(debrief.build_report(self.history, session=[]))

    def test_pair_requires_both_clusters_in_session(self) -> None:
        # Same full-history lift, but the session saw only fi::duration.
        report = debrief.build_report(self.history, session=[self.session[0]])
        self.assertEqual(report["confusable_pairs"], [])
        self.assertEqual(
            report["best_next"], "Review fixed_income: 1 lapses this session."
        )

    def test_pair_requires_a_session_lapse(self) -> None:
        # Both clusters present but clean: no pair, no topic, no pattern.
        quiet_day = self.session[0].day + 5
        base = quiet_day * MS_PER_DAY
        clean = [
            C.Review(1, "fi::duration", "fixed_income", False, quiet_day, base),
            C.Review(
                2, "fi::convexity", "fixed_income", False, quiet_day, base + 60_000
            ),
        ]
        report = debrief.build_report(self.history, session=clean)
        self.assertEqual(report["confusable_pairs"], [])
        self.assertEqual(report["topics_missed"], [])
        self.assertEqual(report["window"]["n_lapses"], 0)
        self.assertEqual(
            report["best_next"],
            "No repeated error pattern this session - keep going.",
        )


class ReportShapeTests(unittest.TestCase):
    def test_untagged_reviews_bucket_and_topic_best_next(self) -> None:
        session = [
            _review(0, lapse=True, note_id=1),
            _review(1, lapse=True, note_id=2),
            _review(2, note_id=3),
        ]
        report = debrief.build_report(session)
        self.assertEqual(
            report["topics_missed"],
            [{"topic": "(untagged)", "lapses": 2, "reviews": 3}],
        )
        self.assertEqual(
            report["best_next"], "Review (untagged): 2 lapses this session."
        )

    def test_topics_sorted_by_lapses_then_name_and_truncated(self) -> None:
        session = [
            _review(0, lapse=True, note_id=1, topic="b_topic"),
            _review(1, lapse=True, note_id=2, topic="a_topic"),
            _review(2, lapse=True, note_id=3, topic="c_topic"),
            _review(3, lapse=True, note_id=4, topic="c_topic"),
            _review(4, note_id=5, topic="a_topic"),
        ]
        report = debrief.build_report(session, top_n=2)
        self.assertEqual(
            report["topics_missed"],
            [
                {"topic": "c_topic", "lapses": 2, "reviews": 2},
                {"topic": "a_topic", "lapses": 1, "reviews": 2},
            ],
        )

    def test_misconception_histogram_counts_missed_notes_once(self) -> None:
        session = [
            _review(0, lapse=True, note_id=1),
            _review(1, lapse=True, note_id=1),  # same note twice: one miss
            _review(2, lapse=True, note_id=2),
            _review(3, note_id=3),  # note 3 passed: its ids never count
        ]
        report = debrief.build_report(
            session,
            misconceptions_by_nid={
                1: ["x.shared"],
                2: ["x.shared", "y.other"],
                3: ["never.counted"],
            },
        )
        self.assertEqual(
            report["misconceptions"],
            [{"id": "x.shared", "count": 2}, {"id": "y.other", "count": 1}],
        )

    def test_misconceptions_sorted_and_truncated(self) -> None:
        session = [_review(0, lapse=True, note_id=1)]
        report = debrief.build_report(
            session,
            misconceptions_by_nid={1: [f"m{i}" for i in range(7)]},
            top_n=5,
        )
        self.assertEqual(
            [row["id"] for row in report["misconceptions"]],
            ["m0", "m1", "m2", "m3", "m4"],
        )


# --------------------------------------------------------------------------
# Misconception index: item JSONL -> normalized keys -> note-field matches
# --------------------------------------------------------------------------


class MisconceptionIndexTests(unittest.TestCase):
    def test_index_and_note_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            pattern = _write_items(Path(tmp_str))
            index = debrief.load_misconception_index(pattern)

            # Only the MCQ with a non-empty map is indexed: title + stem.
            self.assertEqual(len(index), 2)
            self.assertIn("duration price estimate & sign", index)
            self.assertEqual(
                index["duration price estimate & sign"], MCQ_MISCONCEPTIONS
            )

            fields_by_nid = {
                # Notes store html.escaped item text: Title then Stem.
                101: [html.escape(MCQ_TITLE), html.escape(MCQ_STEM)],
                # Stem alone matches too.
                102: ["A different title", html.escape(MCQ_STEM)],
                # Markup, case and whitespace differences must not matter.
                103: [
                    "<b>  " + html.escape("DURATION   price Estimate & SIGN") + " </b>",
                    "",
                ],
                # The worked item was never indexed (no misconceptions).
                104: [html.escape(WORKED_TITLE), "irrelevant"],
                # Only the first two fields are consulted.
                105: ["nope", "nope", html.escape(MCQ_TITLE)],
                106: ["no match at all", ""],
            }
            self.assertEqual(
                debrief.misconceptions_for_notes(index, fields_by_nid),
                {101: MCQ_IDS_SORTED, 102: MCQ_IDS_SORTED, 103: MCQ_IDS_SORTED},
            )

    def test_no_matching_files_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            pattern = str(Path(tmp_str) / "missing" / "*.jsonl")
            self.assertEqual(debrief.load_misconception_index(pattern), {})

    def test_histogram_lands_in_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            index = debrief.load_misconception_index(_write_items(Path(tmp_str)))
        misconceptions_by_nid = debrief.misconceptions_for_notes(
            index, {1: [html.escape(MCQ_TITLE), html.escape(MCQ_STEM)]}
        )
        session = [
            _review(0, lapse=True, note_id=1, topic="fixed_income"),
            _review(1, note_id=2, topic="fixed_income"),
        ]
        report = debrief.build_report(
            session, misconceptions_by_nid=misconceptions_by_nid
        )
        self.assertEqual(
            report["misconceptions"],
            [{"id": mid, "count": 1} for mid in MCQ_IDS_SORTED],
        )


# --------------------------------------------------------------------------
# narrate: threshold abstention + the grounded-or-abstain model path
# --------------------------------------------------------------------------


class NarrateTests(unittest.TestCase):
    def test_abstains_below_min_mistakes_without_calling_the_model(self) -> None:
        backend = _CountingExplodingBackend()
        diagnostics: dict = {}
        result = debrief.narrate(_report(2), backend, diagnostics=diagnostics)
        self.assertIsNone(result)
        self.assertEqual(backend.calls, 0)
        self.assertEqual(diagnostics["outcome"], "abstained")
        self.assertEqual(
            diagnostics["reason"],
            "only 2 mistakes in this session (< 3); showing the deterministic table",
        )

    def test_custom_min_mistakes_threshold(self) -> None:
        backend = _CountingExplodingBackend()
        diagnostics: dict = {}
        result = debrief.narrate(
            _report(4), backend, min_mistakes=5, diagnostics=diagnostics
        )
        self.assertIsNone(result)
        self.assertEqual(backend.calls, 0)
        self.assertIn("(< 5)", diagnostics["reason"])

    def test_abstention_tolerates_missing_diagnostics(self) -> None:
        self.assertIsNone(debrief.narrate(_report(0), _CountingExplodingBackend()))

    def test_mock_narrative_is_deterministic_and_grounded(self) -> None:
        diagnostics: dict = {}
        first = debrief.narrate(
            _report(3), core.MockAssistantBackend(), diagnostics=diagnostics
        )
        second = debrief.narrate(_report(3), core.MockAssistantBackend())
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(diagnostics["outcome"], "ok")
        self.assertIn("fixed_income", first["narrative"])
        self.assertEqual(first["next_step"], _report(3)["best_next"])

    def test_narrates_the_real_synthetic_report(self) -> None:
        history, _session = _synthetic_history()
        report = debrief.build_report(history)
        self.assertEqual(report["window"]["n_lapses"], 3)  # meets the floor
        diagnostics: dict = {}
        narrative = debrief.narrate(
            report, core.MockAssistantBackend(), diagnostics=diagnostics
        )
        self.assertIsNotNone(narrative)
        self.assertEqual(diagnostics["outcome"], "ok")
        self.assertIn("fixed_income", narrative["narrative"])
        self.assertEqual(narrative["next_step"], report["best_next"])

    def test_invented_number_abstains(self) -> None:
        diagnostics: dict = {}
        result = debrief.narrate(
            _report(3),
            core.MockAssistantBackend(failure_mode="invent_number"),
            diagnostics=diagnostics,
        )
        self.assertIsNone(result)
        self.assertEqual(diagnostics["outcome"], "abstained")
        self.assertIn("ungrounded numbers", diagnostics["reason"])

    def test_backend_outage_abstains(self) -> None:
        diagnostics: dict = {}
        result = debrief.narrate(
            _report(3),
            core.MockAssistantBackend(failure_mode="raise"),
            diagnostics=diagnostics,
        )
        self.assertIsNone(result)
        self.assertEqual(diagnostics["reason"], "backend error or timeout")

    def test_garbage_reply_abstains(self) -> None:
        diagnostics: dict = {}
        result = debrief.narrate(
            _report(3),
            core.MockAssistantBackend(failure_mode="garbage"),
            diagnostics=diagnostics,
        )
        self.assertIsNone(result)
        self.assertIn("not a JSON object", diagnostics["reason"])


# --------------------------------------------------------------------------
# The wall: the whole feature runs on plain lists/dicts - no Collection,
# no pylib, no writes.
# --------------------------------------------------------------------------


class WallTests(unittest.TestCase):
    def test_whole_feature_runs_on_plain_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            index = debrief.load_misconception_index(_write_items(Path(tmp_str)))
        rows = [
            (1_000, 1, 1),
            (MS_PER_MINUTE + 1_000, 1, 1),
            (2 * MS_PER_MINUTE + 1_000, 1, 1),
            (3 * MS_PER_MINUTE + 1_000, 3, 1),
        ]
        tags = {1: "cluster::fi::duration cfa::topic::fixed_income"}
        reviews = debrief.reviews_from_rows(rows, tags)
        session = debrief.sessionize(reviews)
        self.assertEqual(len(session), 4)
        misconceptions_by_nid = debrief.misconceptions_for_notes(
            index, {1: [html.escape(MCQ_TITLE), html.escape(MCQ_STEM)]}
        )
        report = debrief.build_report(
            reviews, session=session, misconceptions_by_nid=misconceptions_by_nid
        )
        self.assertEqual(report["window"]["n_lapses"], 3)
        self.assertEqual(
            [row["id"] for row in report["misconceptions"]], MCQ_IDS_SORTED
        )
        diagnostics: dict = {}
        narrative = debrief.narrate(
            report, core.MockAssistantBackend(), diagnostics=diagnostics
        )
        self.assertIsNotNone(narrative)
        self.assertEqual(diagnostics["outcome"], "ok")

    def test_module_references_no_collection_machinery(self) -> None:
        for name, value in vars(debrief).items():
            if isinstance(value, types.ModuleType):
                origin = value.__name__
            else:
                origin = getattr(value, "__module__", "") or ""
            self.assertFalse(
                origin.startswith(("anki", "aqt")),
                f"debrief.{name} references {origin}",
            )


if __name__ == "__main__":
    unittest.main()
