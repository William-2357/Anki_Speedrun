# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Anki Speedrun: the desktop host bridge for the runtime assistant layer.

The CFA dashboard (an API-enabled ``AnkiWebView`` page) POSTs JSON to
``/_anki/speedrunAssistant``; this module answers. It is the ONE place the
webview's AI features touch the desktop host, and it enforces the plan's
invariants server-side rather than trusting the page:

- **Read-only.** Every action only reads the open collection (config,
  revlog, note tags/fields). Nothing here writes to grading, scheduling,
  the Readiness inputs, or anything else - the toggles themselves are
  written by the page through the standard ``setConfigJson`` RPC, not
  through this bridge.
- **Default-OFF.** Each action checks the synced ``speedrun:aiAssist``
  master switch AND its per-feature flag; with either off it returns
  ``{"enabled": false}`` and does no work.
- **Grounded-or-abstain.** Model calls go through
  ``tools/speedrun/assistant`` (S1), which returns ``None`` unless the
  reply is parseable, schema-valid and numerically grounded in the
  supplied facts; the page then falls back to its deterministic view.
- **Desktop only.** AnkiDroid never routes this endpoint; the page
  feature-detects via the ``status`` action and hides its AI affordances
  when the bridge is absent.

The assistant package lives in the repo's ``tools/speedrun`` (a dev-mode
research checkout); when it cannot be imported the bridge reports
``available: false`` and every feature degrades to deterministic behavior.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import aqt
from anki.collection import Collection, SearchNode
from anki.utils import strip_html

# Synced collection-config keys (S3). The TS side mirrors these names in
# ts/routes/dashboard/config.ts; collection config is the single source of
# truth and syncs natively.
CONFIG_AI_ASSIST = "speedrun:aiAssist"
CONFIG_DEBRIEF_ENABLED = "speedrun:debriefEnabled"
CONFIG_COACH_ENABLED = "speedrun:coachEnabled"
CONFIG_TAG_SUGGEST_ENABLED = "speedrun:tagSuggestEnabled"
CONFIG_AI_BACKEND = "speedrun:aiBackend"

#: Cap the per-call workload of the tag suggester: one grounded call with at
#: most this many tags (the page sends most-frequent first).
MAX_TAGS_PER_SUGGEST_CALL = 40
#: Sample note fronts are truncated to keep prompts bounded.
MAX_FRONT_CHARS = 200

_import_lock = threading.Lock()
_modules: dict[str, Any] | None = None
_import_error: str | None = None


def _speedrun_tools_dir() -> Path | None:
    """<repo>/tools/speedrun in a dev checkout, else None."""
    candidate = Path(__file__).resolve().parents[2] / "tools" / "speedrun"
    if (candidate / "assistant" / "__init__.py").exists():
        return candidate
    return None


def _assistant_modules() -> dict[str, Any] | None:
    """Import (once) the S1 package from tools/speedrun; None if absent."""
    global _modules, _import_error
    with _import_lock:
        if _modules is not None or _import_error is not None:
            return _modules
        tools_dir = _speedrun_tools_dir()
        if tools_dir is None:
            _import_error = "tools/speedrun not present (not a dev checkout)"
            return None
        import sys

        if str(tools_dir) not in sys.path:
            sys.path.append(str(tools_dir))
        try:
            from assistant import (  # type: ignore[import-not-found]
                coach,
                core,
                debrief,
                tag_suggest,
            )
        except Exception as exc:  # degrade, never break the dashboard
            _import_error = f"assistant package failed to import: {exc}"
            return None
        _modules = {
            "core": core,
            "debrief": debrief,
            "coach": coach,
            "tag_suggest": tag_suggest,
        }
        return _modules


#: Valid values of speedrun:aiBackend; "" = decide from the environment.
KNOWN_BACKENDS = ("", "mock", "claude-cli", "openai-compatible")


def _flags(col: Collection) -> dict[str, Any]:
    def flag(key: str) -> bool:
        return bool(col.get_config(key, default=False))

    backend = col.get_config(CONFIG_AI_BACKEND, default="")
    if backend not in KNOWN_BACKENDS:
        # A hand-edited config value must degrade to the env default, not
        # break the feature.
        backend = ""
    return {
        "aiAssist": flag(CONFIG_AI_ASSIST),
        "debriefEnabled": flag(CONFIG_DEBRIEF_ENABLED),
        "coachEnabled": flag(CONFIG_COACH_ENABLED),
        "tagSuggestEnabled": flag(CONFIG_TAG_SUGGEST_ENABLED),
        "backend": backend,
    }


def _make_backend(flags: dict[str, Any]) -> Any:
    modules = _assistant_modules()
    assert modules is not None
    return modules["core"].make_backend(flags["backend"] or None)


def _disclosure(flags: dict[str, Any]) -> str:
    backend = flags["backend"] or "mock"
    if backend == "mock":
        return "AI-generated by the offline mock backend (no data leaves this machine)."
    return (
        f"AI-generated via the '{backend}' backend; the numbers shown to you "
        "were sent to that model."
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _action_status(col: Collection, _req: dict[str, Any]) -> dict[str, Any]:
    flags = _flags(col)
    available = _assistant_modules() is not None
    return {
        "bridge": True,
        "available": available,
        "unavailableReason": None if available else _import_error,
        **flags,
    }


def _collect_reviews(col: Collection, modules: dict[str, Any]) -> list[Any]:
    """All graded reviews joined to their notes' tags (read-only SQL)."""
    rows = col.db.all(
        "select r.id, r.ease, c.nid from revlog r"
        " join cards c on c.id = r.cid where r.ease >= 1 order by r.id"
    )
    nids = sorted({nid for _, _, nid in rows})
    tags_by_nid: dict[int, str] = {}
    for chunk_start in range(0, len(nids), 500):
        chunk = nids[chunk_start : chunk_start + 500]
        placeholders = ",".join("?" * len(chunk))
        for nid, tags in col.db.all(
            f"select id, tags from notes where id in ({placeholders})", *chunk
        ):
            tags_by_nid[nid] = tags
    return modules["debrief"].reviews_from_rows(rows, tags_by_nid)


def _note_fields(col: Collection, nids: list[int]) -> dict[int, list[str]]:
    fields_by_nid: dict[int, list[str]] = {}
    for chunk_start in range(0, len(nids), 500):
        chunk = nids[chunk_start : chunk_start + 500]
        placeholders = ",".join("?" * len(chunk))
        for nid, flds in col.db.all(
            f"select id, flds from notes where id in ({placeholders})", *chunk
        ):
            fields_by_nid[nid] = str(flds).split("\x1f")
    return fields_by_nid


def _action_debrief(col: Collection, req: dict[str, Any]) -> dict[str, Any]:
    flags = _flags(col)
    if not (flags["aiAssist"] and flags["debriefEnabled"]):
        return {"enabled": False, "reason": "debrief is switched off"}
    modules = _assistant_modules()
    if modules is None:
        return {"enabled": False, "reason": _import_error}
    debrief = modules["debrief"]

    reviews = _collect_reviews(col, modules)
    session = debrief.sessionize(reviews)
    missed_nids = sorted({r.note_id for r in session if r.lapse})
    misconceptions_by_nid: dict[int, list[str]] = {}
    tools_dir = _speedrun_tools_dir()
    if missed_nids and tools_dir is not None:
        index = debrief.load_misconception_index(str(tools_dir / "items" / "*.jsonl"))
        if index:
            misconceptions_by_nid = debrief.misconceptions_for_notes(
                index, _note_fields(col, missed_nids)
            )
    report = debrief.build_report(
        reviews, session=session, misconceptions_by_nid=misconceptions_by_nid
    )
    if report is None:
        return {
            "enabled": True,
            "report": None,
            "narrative": None,
            "narrativeStatus": "no graded reviews in the last session",
        }

    diagnostics: dict[str, Any] = {}
    narrative = debrief.narrate(report, _make_backend(flags), diagnostics=diagnostics)
    return {
        "enabled": True,
        "report": report,
        "narrative": narrative,
        "narrativeStatus": diagnostics.get("reason")
        or diagnostics.get("outcome", "ok"),
        "disclosure": _disclosure(flags),
    }


def _action_coach(col: Collection, req: dict[str, Any]) -> dict[str, Any]:
    flags = _flags(col)
    if not (flags["aiAssist"] and flags["coachEnabled"]):
        return {"enabled": False, "reason": "coach is switched off"}
    modules = _assistant_modules()
    if modules is None:
        return {"enabled": False, "reason": _import_error}
    facts = req.get("facts")
    if not isinstance(facts, dict):
        return {"error": "facts object required"}
    diagnostics: dict[str, Any] = {}
    plan = modules["coach"].coach_plan(
        facts, _make_backend(flags), diagnostics=diagnostics
    )
    return {
        "enabled": True,
        "plan": plan,
        "planStatus": diagnostics.get("reason") or diagnostics.get("outcome", "ok"),
        "disclosure": _disclosure(flags),
    }


def _sample_fronts(col: Collection, tag: str, limit: int) -> list[str]:
    """Up to ``limit`` note fronts carrying the tag, plain-text, truncated."""
    fronts: list[str] = []
    try:
        query = col.build_search_string(SearchNode(tag=tag))
        nids = list(col.find_notes(query))[:limit]
    except Exception:
        return []
    for nid in nids:
        try:
            front = strip_html(col.get_note(nid).fields[0]).strip()
        except Exception:
            continue
        if front:
            fronts.append(front[:MAX_FRONT_CHARS])
    return fronts


def _action_suggest_tags(col: Collection, req: dict[str, Any]) -> dict[str, Any]:
    flags = _flags(col)
    if not (flags["aiAssist"] and flags["tagSuggestEnabled"]):
        return {"enabled": False, "reason": "tag suggestions are switched off"}
    modules = _assistant_modules()
    if modules is None:
        return {"enabled": False, "reason": _import_error}
    tag_suggest = modules["tag_suggest"]

    raw_tags = req.get("tags")
    topics = req.get("topics")
    if not isinstance(raw_tags, list) or not isinstance(topics, list):
        return {"error": "tags and topics arrays required"}
    considered = raw_tags[:MAX_TAGS_PER_SUGGEST_CALL]
    tags_payload = []
    for row in considered:
        if not isinstance(row, dict) or not isinstance(row.get("tag"), str):
            continue
        tag = row["tag"]
        tags_payload.append(
            {
                "tag": tag,
                "cards": int(row.get("cards") or 0),
                "sample_fronts": _sample_fronts(
                    col, tag, tag_suggest.SAMPLE_FRONTS_PER_TAG
                ),
            }
        )
    diagnostics: dict[str, Any] = {}
    suggestions = tag_suggest.suggest_mappings(
        tags_payload,
        [str(t) for t in topics],
        _make_backend(flags),
        diagnostics=diagnostics,
    )
    return {
        "enabled": True,
        "suggestions": suggestions,
        "consideredTags": len(tags_payload),
        "totalTags": len(raw_tags),
        "suggestStatus": diagnostics.get("reason") or diagnostics.get("outcome", "ok"),
        "disclosure": _disclosure(flags),
    }


_ACTIONS = {
    "status": _action_status,
    "debrief": _action_debrief,
    "coach": _action_coach,
    "suggestTags": _action_suggest_tags,
}


def handle_assistant_request(data: bytes) -> bytes:
    """Entry point called from mediasrv's ``speedrunAssistant`` POST route."""
    try:
        req = json.loads(data or b"{}")
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
        action = _ACTIONS.get(str(req.get("action")))
        if action is None:
            response: dict[str, Any] = {
                "error": f"unknown action {req.get('action')!r}"
            }
        else:
            col = aqt.mw.col
            assert col is not None  # mediasrv guarantees an open collection
            response = action(col, req)
    except Exception as exc:
        # Any failure degrades to "AI unavailable"; the page falls back to
        # its deterministic view rather than surfacing a broken feature.
        response = {"error": str(exc)}
    return json.dumps(response).encode()
