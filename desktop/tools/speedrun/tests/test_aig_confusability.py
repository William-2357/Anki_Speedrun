# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the computed confusability signal (aig/confusability.py, M1b)."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig import confusability as C  # noqa: E402


class SyntheticMiningTests(unittest.TestCase):
    """The synthetic-revlog self-test: engineered co-occurrence is found."""

    @classmethod
    def setUpClass(cls) -> None:
        reviews, fronts = C.synthetic_revlog()
        cls.result = C.compute(reviews, fronts)

    def test_markers_emitted_for_engineered_pair_only(self) -> None:
        self.assertTrue(self.result.emitted, self.result.reason)
        self.assertEqual(
            set(self.result.markers),
            {"fi::duration", "fi::convexity"},
            "only the engineered pair may be marked",
        )
        for tags in self.result.markers.values():
            self.assertEqual(tags, [C.CONFUSABLE_TAG])

    def test_surface_twins_without_behavior_not_marked(self) -> None:
        # fi::credit / fi::creditx have the HIGHEST surface similarity but
        # zero temporal co-occurrence - the behavioral term must demote them.
        self.assertNotIn("fi::credit", self.result.markers)
        self.assertNotIn("fi::creditx", self.result.markers)
        rows = {tuple(r["pair"]): r for r in self.result.report["pairs"]}
        dead = rows[("fi::credit", "fi::creditx")]
        live = rows[("fi::convexity", "fi::duration")]
        self.assertGreater(dead["surface_similarity"], live["surface_similarity"])
        self.assertGreater(live["confusability"], dead["confusability"])

    def test_full_score_beats_surface_baseline(self) -> None:
        rep = self.result.report
        self.assertIsNotNone(rep["auc_full"])
        self.assertIsNotNone(rep["auc_baseline_surface_only"])
        self.assertGreater(rep["auc_full"], rep["auc_baseline_surface_only"])

    def test_within_topic_scope(self) -> None:
        # All mined pairs share the single synthetic topic.
        for row in self.result.report["pairs"]:
            a, b = row["pair"]
            self.assertTrue(a.startswith("fi::") and b.startswith("fi::"))


class AbstentionTests(unittest.TestCase):
    def test_tiny_revlog_abstains(self) -> None:
        reviews, fronts = C.synthetic_revlog()
        result = C.compute(reviews[:10], fronts)
        self.assertFalse(result.emitted)
        self.assertIn("too little data", result.reason)
        self.assertEqual(result.markers, {})

    def test_no_behavioral_signal_abstains(self) -> None:
        """Independent clusters only -> one-class labels -> abstain."""
        reviews, fronts = C.synthetic_revlog()
        keep = [r for r in reviews if r.cluster in ("fi::credit", "fi::creditx")]
        # Duplicate to clear the minimum-volume bar while staying signal-free.
        keep = keep * 3
        keep.sort(key=lambda r: r.id_ms)
        result = C.compute(keep, fronts)
        self.assertFalse(result.emitted)
        self.assertEqual(result.markers, {})


class InputFormatTests(unittest.TestCase):
    def test_jsonl_roundtrip(self) -> None:
        reviews, fronts = C.synthetic_revlog()
        with tempfile.TemporaryDirectory() as tmp:
            revlog_path = Path(tmp) / "revlog.jsonl"
            notes_path = Path(tmp) / "notes.jsonl"
            with revlog_path.open("w") as f:
                for i, r in enumerate(reviews):
                    f.write(
                        json.dumps(
                            {
                                "card_id": 1000 + i,
                                "note_id": r.note_id,
                                "tags": [
                                    f"cluster::{r.cluster}",
                                    f"cfa::topic::{r.topic}",
                                ],
                                "button": 1 if r.lapse else 3,
                                "id_ms": r.id_ms,
                            }
                        )
                        + "\n"
                    )
            with notes_path.open("w") as f:
                for nid, front in fronts.items():
                    f.write(json.dumps({"note_id": nid, "front": front}) + "\n")

            loaded = C.load_revlog_jsonl(revlog_path)
            self.assertEqual(len(loaded), len(reviews))
            self.assertEqual(loaded[0].cluster, reviews[0].cluster)
            self.assertEqual(loaded[0].lapse, reviews[0].lapse)

            result = C.run(
                revlog=str(revlog_path),
                notes=str(notes_path),
                out_report=Path(tmp) / "report.json",
                out_markers=Path(tmp) / "markers.json",
            )
            self.assertTrue(result.emitted, result.reason)
            markers = json.loads((Path(tmp) / "markers.json").read_text())
            self.assertEqual(set(markers["markers"]), {"fi::duration", "fi::convexity"})
            report = json.loads((Path(tmp) / "report.json").read_text())
            self.assertTrue(report["meta"]["no_human_labels"])

    def test_sqlite_reader(self) -> None:
        reviews, fronts = C.synthetic_revlog()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "collection.anki2"
            con = sqlite3.connect(db)
            con.executescript(
                """
                create table notes (id integer primary key, tags text, flds text);
                create table cards (id integer primary key, nid integer);
                create table revlog (id integer primary key, cid integer, ease integer);
                """
            )
            note_cluster = {}
            for r in reviews:
                note_cluster[r.note_id] = (r.cluster, r.topic)
            for nid, (cluster, topic) in note_cluster.items():
                con.execute(
                    "insert into notes values (?, ?, ?)",
                    (
                        nid,
                        f" cluster::{cluster} cfa::topic::{topic} ",
                        fronts[nid] + "\x1fback side",
                    ),
                )
                con.execute("insert into cards values (?, ?)", (nid * 10, nid))
            for r in reviews:
                con.execute(
                    "insert into revlog values (?, ?, ?)",
                    (r.id_ms, r.note_id * 10, 1 if r.lapse else 3),
                )
            con.commit()
            con.close()

            loaded, loaded_fronts = C.load_revlog_sqlite(db)
            self.assertEqual(len(loaded), len(reviews))
            self.assertEqual(loaded_fronts[1], fronts[1], "front = first field of flds")
            result = C.compute(loaded, loaded_fronts)
            self.assertTrue(result.emitted, result.reason)
            self.assertEqual(set(result.markers), {"fi::duration", "fi::convexity"})


class ScoringPrimitiveTests(unittest.TestCase):
    def test_auc(self) -> None:
        self.assertEqual(C.auc([0.9, 0.1], [True, False]), 1.0)
        self.assertEqual(C.auc([0.1, 0.9], [True, False]), 0.0)
        self.assertEqual(C.auc([0.5, 0.5], [True, False]), 0.5)
        self.assertIsNone(C.auc([0.5, 0.6], [True, True]), "one-class -> None")

    def test_jaccard(self) -> None:
        self.assertEqual(C.jaccard({"a", "b"}, {"a", "b"}), 1.0)
        self.assertEqual(C.jaccard({"a"}, {"b"}), 0.0)
        self.assertEqual(C.jaccard(set(), {"a"}), 0.0)

    def test_surface_similarity_fallback_flagged(self) -> None:
        sim, basis = C.surface_similarity("fi::duration", "fi::convexity", {})
        self.assertIn("weak", basis)
        self.assertGreater(sim, 0.0)  # shares the 'fi' token

    def test_apply_markers_is_importable_but_not_run(self) -> None:
        # --apply is code-only at authoring time: it must exist and defer
        # its pylib import (so this repo's tests run without anki built).
        self.assertTrue(callable(C.apply_markers))


if __name__ == "__main__":
    unittest.main()
