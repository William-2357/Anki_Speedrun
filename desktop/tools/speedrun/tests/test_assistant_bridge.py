# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""S2/S3 tests: the desktop host bridge's gating and read-only invariants.

Loads ``qt/aqt/speedrun_assistant.py`` by path with ``aqt``/``anki`` stubbed
out, so the bridge's default-OFF gating, its degraded modes and its
read-only behavior are testable without Qt or a real collection.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_SPEEDRUN = Path(__file__).resolve().parents[1]
_DESKTOP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SPEEDRUN))


def _install_stubs() -> types.ModuleType:
    """Stub the aqt/anki imports the bridge needs, then load it by path."""
    aqt_stub = sys.modules.get("aqt")
    if aqt_stub is None or not hasattr(aqt_stub, "_speedrun_test_stub"):
        aqt_stub = types.ModuleType("aqt")
        aqt_stub._speedrun_test_stub = True  # type: ignore[attr-defined]
        aqt_stub.mw = None  # type: ignore[attr-defined]
        sys.modules["aqt"] = aqt_stub

        anki_stub = types.ModuleType("anki")
        collection_stub = types.ModuleType("anki.collection")

        class Collection:  # noqa: D401 - typing placeholder only
            pass

        def search_node(**kwargs: Any) -> dict[str, Any]:
            return kwargs

        collection_stub.Collection = Collection  # type: ignore[attr-defined]
        collection_stub.SearchNode = search_node  # type: ignore[attr-defined]
        utils_stub = types.ModuleType("anki.utils")
        utils_stub.strip_html = lambda text: re.sub(r"<[^>]+>", "", str(text))  # type: ignore[attr-defined]
        sys.modules["anki"] = anki_stub
        sys.modules["anki.collection"] = collection_stub
        sys.modules["anki.utils"] = utils_stub

    spec = importlib.util.spec_from_file_location(
        "speedrun_assistant_under_test",
        _DESKTOP / "qt" / "aqt" / "speedrun_assistant.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _install_stubs()


class FakeDb:
    """Answers the bridge's two read-only SQL shapes from canned rows."""

    def __init__(
        self, revlog_rows: list[tuple[int, int, int]], notes: dict[int, tuple[str, str]]
    ):
        self.revlog_rows = revlog_rows
        self.notes = notes  # nid -> (tags string, flds string)
        self.statements: list[str] = []

    def all(self, sql: str, *args: Any) -> list[Any]:
        self.statements.append(sql)
        lowered = " ".join(sql.lower().split())
        assert lowered.startswith("select"), f"non-SELECT through the bridge: {sql}"
        if "from revlog" in lowered:
            return list(self.revlog_rows)
        if "select id, tags from notes" in lowered:
            return [(nid, self.notes[nid][0]) for nid in args if nid in self.notes]
        if "select id, flds from notes" in lowered:
            return [(nid, self.notes[nid][1]) for nid in args if nid in self.notes]
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeCollection:
    """Read-only stand-in: any write attempt fails the test."""

    def __init__(self, config: dict[str, Any] | None = None, db: FakeDb | None = None):
        self.config = config or {}
        self.db = db or FakeDb([], {})

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        raise AssertionError(f"the bridge must never write config ({key})")

    def find_notes(self, query: Any) -> list[int]:
        return []

    def build_search_string(self, node: Any) -> str:
        return str(node)

    def get_note(self, nid: int) -> Any:
        raise AssertionError("unexpected note load")


def call(col: FakeCollection, action: str, **payload: Any) -> dict[str, Any]:
    sys.modules["aqt"].mw = SimpleNamespace(col=col)  # type: ignore[attr-defined]
    raw = bridge.handle_assistant_request(
        json.dumps({"action": action, **payload}).encode()
    )
    reply = json.loads(raw.decode())
    assert isinstance(reply, dict)
    return reply


ALL_ON = {
    "speedrun:aiAssist": True,
    "speedrun:debriefEnabled": True,
    "speedrun:coachEnabled": True,
    "speedrun:tagSuggestEnabled": True,
}


class StatusTests(unittest.TestCase):
    def test_defaults_are_all_off(self) -> None:
        reply = call(FakeCollection(), "status")
        self.assertTrue(reply["bridge"])
        self.assertTrue(reply["available"])
        for flag in ("aiAssist", "debriefEnabled", "coachEnabled", "tagSuggestEnabled"):
            self.assertFalse(reply[flag], flag)
        self.assertEqual(reply["backend"], "")

    def test_unknown_backend_value_degrades_to_env_default(self) -> None:
        reply = call(FakeCollection({"speedrun:aiBackend": "skynet"}), "status")
        self.assertEqual(reply["backend"], "")

    def test_unknown_action_is_an_error_not_a_crash(self) -> None:
        reply = call(FakeCollection(), "grade_my_cards")
        self.assertIn("error", reply)


class DefaultOffGatingTests(unittest.TestCase):
    """With the master switch or the feature flag off, no work happens."""

    def assert_disabled(self, config: dict[str, Any]) -> None:
        col = FakeCollection(config)
        for action in ("debrief", "coach", "suggestTags"):
            reply = call(col, action)
            self.assertEqual(reply.get("enabled"), False, (action, config))
        # no SQL ran: gating short-circuits before any collection read
        self.assertEqual(col.db.statements, [])

    def test_everything_defaults_off(self) -> None:
        self.assert_disabled({})

    def test_master_switch_off_beats_feature_flags(self) -> None:
        self.assert_disabled({**ALL_ON, "speedrun:aiAssist": False})

    def test_feature_flag_off_beats_master_switch(self) -> None:
        col = FakeCollection({"speedrun:aiAssist": True})
        for action in ("debrief", "coach", "suggestTags"):
            reply = call(col, action)
            self.assertEqual(reply.get("enabled"), False, action)


class EnabledPathTests(unittest.TestCase):
    """Master + feature flags on: the mock-backend path works end to end
    and stays read-only."""

    def test_coach_runs_grounded_on_mock_backend(self) -> None:
        col = FakeCollection(dict(ALL_ON))
        facts = {
            "exam": "CFA Level I",
            "days_to_exam": 34,
            "best_next": "Derivatives",
            "readiness": {"kind": "abstain", "missing": ["Only 42 graded reviews."]},
            "subjects": [{"name": "Derivatives", "weighted_gap": 0.031}],
        }
        reply = call(col, "coach", facts=facts)
        self.assertTrue(reply["enabled"])
        self.assertIsNotNone(reply["plan"])
        self.assertIn("Derivatives", reply["plan"]["summary"])
        self.assertIn("AI-generated", reply["disclosure"])

    def test_coach_requires_facts(self) -> None:
        reply = call(FakeCollection(dict(ALL_ON)), "coach")
        self.assertIn("error", reply)

    def test_debrief_with_empty_history_abstains_honestly(self) -> None:
        col = FakeCollection(dict(ALL_ON), FakeDb([], {}))
        reply = call(col, "debrief")
        self.assertTrue(reply["enabled"])
        self.assertIsNone(reply["report"])
        self.assertIsNone(reply["narrative"])

    def test_debrief_reads_revlog_and_narrates_via_mock(self) -> None:
        hour = 3_600_000
        base = 1_700_000_000_000
        rows = []
        # an old session (well outside the trailing window)
        rows += [(base - 100 * hour, 3, 1)]
        # the trailing session: repeated duration lapses alongside convexity
        for i in range(4):
            rows.append((base + i * 60_000, 1, 1))  # lapse on duration note
            rows.append((base + i * 60_000 + 1_000, 3, 2))  # clean convexity
        notes = {
            1: ("cluster::fi::duration cfa::topic::fixed_income", "Q\x1fA"),
            2: ("cluster::fi::convexity cfa::topic::fixed_income", "Q\x1fA"),
        }
        col = FakeCollection(dict(ALL_ON), FakeDb(rows, notes))
        reply = call(col, "debrief")
        self.assertTrue(reply["enabled"])
        report = reply["report"]
        self.assertIsNotNone(report)
        self.assertEqual(report["window"]["n_lapses"], 4)
        topics = {row["topic"] for row in report["topics_missed"]}
        self.assertIn("fixed_income", topics)
        # mock narration is deterministic and grounded
        self.assertIsNotNone(reply["narrative"])
        self.assertIn("disclosure", reply)
        # only SELECTs ran
        self.assertTrue(
            all(s.lower().lstrip().startswith("select") for s in col.db.statements)
        )

    def test_suggest_tags_pre_fills_but_never_saves(self) -> None:
        col = FakeCollection(dict(ALL_ON))
        reply = call(
            col,
            "suggestTags",
            tags=[
                {"tag": "fixed_income_notes", "cards": 5},
                {"tag": "zz9", "cards": 1},
            ],
            topics=["fixed_income", "derivatives"],
        )
        self.assertTrue(reply["enabled"])
        suggestions = reply["suggestions"]
        self.assertEqual(suggestions["fixed_income_notes"]["topic"], "fixed_income")
        # the unsure tag is left blank, not forced
        self.assertNotIn("zz9", suggestions)
        self.assertEqual(reply["consideredTags"], 2)


if __name__ == "__main__":
    unittest.main()
