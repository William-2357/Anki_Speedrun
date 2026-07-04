# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Feature B - the Study Coach (prioritise and explain; never invent).

Input is the facts dict the dashboard page serialized from its
already-computed ``DashboardModel`` (per-subject Memory/Performance,
coverage, weighted gaps, ``bestNext``) plus ``days_to_exam``. The coach
prompt is prioritisation-only and abstention-preserving: when the Readiness
gauge abstains, the model must echo the abstention reasons and must NOT
state any pass probability - enforced here by a reject hook on top of
``core.grounded_complete``'s number grounding, not by trusting the model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from aig import models

from . import core

#: Reply schema for the coach plan.
PLAN_SCHEMA = {"summary": "str", "priorities": "list[dict]", "note": "str?"}

#: Wordings that assert a pass probability. While Readiness abstains, ANY of
#: these in a reply is a violation - core's number grounding already blocks
#: invented figures, but a numberless "you will probably pass" must die here.
_PASS_CLAIM_RE = re.compile(
    r"p\s*\(\s*pass\s*\)"
    r"|pass(?:ing)?\s+probabilit"
    r"|probabilit\w*[^.]{0,40}?\bpass"
    r"|(?:chance|chances|odds|likelihood)\b[^.]{0,60}?\bpass"
    r"|(?:will|would|going\s+to|likely\s+to|probably)\s+pass",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = """You are the study coach for the {exam} exam inside a
spaced-repetition app. Produce a short, concrete "what should I do today"
plan from FACTS alone:
- Prioritise by the largest weighted gaps and the coverage holes in
  FACTS.subjects (they arrive worst-first); factor in days_to_exam.
- Reference only numbers present in FACTS; do not compute new ones.
- The app's gauges are authoritative. If FACTS.readiness.kind is not
  "value", the Readiness gauge is abstaining: echo its "missing" reasons
  verbatim if you mention readiness at all, and NEVER state, estimate or
  imply a pass probability or how likely the user is to pass.
- "priorities" is an ordered list of {{"topic": <a topic named in FACTS>,
  "why": <one grounded clause>}} entries, most urgent first."""


def _fact_topics(facts: Mapping[str, Any]) -> set[str]:
    """Every topic name the facts mention (lower-cased for comparison)."""
    topics = {
        str(row.get("name", "")).strip().lower()
        for row in facts.get("subjects", [])
        if isinstance(row, Mapping)
    }
    best_next = facts.get("best_next")
    if isinstance(best_next, str) and best_next.strip():
        topics.add(best_next.strip().lower())
    topics.discard("")
    return topics


def _readiness_is_value(facts: Mapping[str, Any]) -> bool:
    readiness = facts.get("readiness")
    return isinstance(readiness, Mapping) and readiness.get("kind") == "value"


def _make_reject(facts: Mapping[str, Any]) -> Any:
    """The abstention-preserving post-check for one facts dict."""
    known_topics = _fact_topics(facts)
    readiness_value = _readiness_is_value(facts)

    def reject(reply: Mapping[str, Any]) -> str | None:
        for entry in reply.get("priorities", []):
            topic = entry.get("topic") if isinstance(entry, Mapping) else None
            if not isinstance(topic, str) or not topic.strip():
                return "priorities entries need a non-empty topic string"
            if topic.strip().lower() not in known_topics:
                return f"priorities name a topic absent from the facts: {topic!r}"
        if not readiness_value:
            for text in core._walk_strings(reply, skip_metadata=True):
                if _PASS_CLAIM_RE.search(text):
                    return (
                        "readiness is abstaining; the coach may not state "
                        "a pass probability"
                    )
        return None

    return reject


def coach_plan(
    facts: Mapping[str, Any],
    backend: models.Backend,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """B3: a grounded study plan {summary, priorities: [{topic, why}]},
    or None (abstain -> the dashboard's deterministic view stands)."""
    return core.grounded_complete(
        _SYSTEM_PROMPT.format(exam=facts.get("exam", "target")),
        facts,
        schema=PLAN_SCHEMA,
        backend=backend,
        task="coach",
        reject=_make_reject(facts),
        diagnostics=diagnostics,
    )
