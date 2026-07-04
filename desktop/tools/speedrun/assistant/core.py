# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""S1 - the assistant backend adapter: grounded-or-abstain completions.

Reuses the pluggable backends from ``aig/models.py`` (``ClaudeCliBackend``,
``OpenAICompatBackend``, ``parse_json_reply``) and adds the runtime
semantics the assistant layer needs:

- ``make_backend(name)``: build a backend from an explicit name, the
  ``SPEEDRUN_AI_BACKEND`` env var, or the ``mock`` default. The mock here is
  assistant-specific (``MockAssistantBackend``): ``aig.models.MockBackend``
  cans drafter/critic/solver replies for the item pipeline, which are
  meaningless for narration prompts, so the assistant ships its own
  deterministic offline mock speaking the same ``Backend`` protocol.
- ``grounded_complete(system, facts, *, schema)``: ONE completion call that
  (1) hands the model only facts the app already computed, (2) demands a
  JSON object, and (3) ABSTAINS - returns ``None`` - whenever the reply is
  unparseable, fails the schema, declares low confidence, or states a
  number that does not appear in the supplied facts. Any error or timeout
  also returns ``None``; the caller falls back to its deterministic view.

Nothing here writes anywhere: no collection handle, no files, no state.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aig import models

BACKEND_NAMES = ("mock", "claude-cli", "openai-compatible")
#: Runtime calls sit behind an interactive page; keep the hard cap well
#: below aig's authoring-time 120s.
DEFAULT_TIMEOUT_SECONDS = 30
#: Replies self-reporting confidence below this abstain ("low-confidence").
MIN_CONFIDENCE = 0.5
#: Integers up to this magnitude pass the grounding check without a fact
#: match: list ordinals and small counts ("the 3 topics below") would
#: otherwise force spurious abstention. Everything larger - scores,
#: percentages, day counts - must appear in the facts.
SMALL_INT_ALLOWANCE = 12

_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


@dataclass
class MockAssistantBackend:
    """Deterministic offline backend for tests and the default config.

    Reads the TASK marker and the FACTS JSON out of the prompt built by
    ``grounded_complete`` and answers with a canned, fact-grounded reply,
    so the full grounded-or-abstain path is exercised without a network.

    ``failure_mode`` lets tests drive the abstention paths:
    - ``garbage``:       a non-JSON reply (unparseable).
    - ``invent_number``: valid JSON asserting a number absent from facts.
    - ``abstain``:       an explicit {"abstain": true} reply.
    - ``low_confidence``: a valid reply with confidence below the bar.
    - ``raise``:         raises (models a backend outage).
    """

    failure_mode: str = ""
    name: str = "mock"

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        if self.failure_mode == "raise":
            raise RuntimeError("mock backend outage")
        if self.failure_mode == "garbage":
            return "I am not JSON at all."
        if self.failure_mode == "abstain":
            return json.dumps({"abstain": True, "reason": "mock declines to answer"})
        task = _prompt_task(prompt)
        facts = _prompt_facts(prompt)
        if self.failure_mode == "invent_number":
            return json.dumps(_MOCK_REPLIES["invent_number"](facts))
        reply = _MOCK_REPLIES.get(task, _mock_generic)(facts)
        if self.failure_mode == "low_confidence":
            reply = dict(reply)
            reply["confidence"] = 0.1
        return json.dumps(reply)


def _prompt_task(prompt: str) -> str:
    match = re.search(r"^TASK: (\S+)$", prompt, re.M)
    return match.group(1) if match else "generic"


def _prompt_facts(prompt: str) -> dict[str, Any]:
    match = re.search(r"^FACTS.*?:\n(\{.*?\})\n\nRESPONSE", prompt, re.S | re.M)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mock_generic(facts: dict[str, Any]) -> dict[str, Any]:
    return {"text": f"Mock reply grounded in {len(facts)} fact keys."}


def _mock_debrief(facts: dict[str, Any]) -> dict[str, Any]:
    topics = facts.get("topics_missed") or []
    topic = topics[0].get("topic", "your weakest topic") if topics else "this session"
    pairs = facts.get("confusable_pairs") or []
    sentences = [
        f"This session's misses concentrated in {topic}.",
    ]
    if pairs:
        a, b = pairs[0].get("pair", ["?", "?"])[:2]
        sentences.append(
            f"The confusable pair {a} vs {b} co-occurred with your lapses, "
            "so the errors look like discrimination failures, not blanks."
        )
    best_next = facts.get("best_next") or "review the table below"
    return {
        "narrative": " ".join(sentences),
        "next_step": str(best_next),
    }


def _mock_coach(facts: dict[str, Any]) -> dict[str, Any]:
    best = facts.get("best_next") or "the largest-gap topic"
    readiness = facts.get("readiness") or {}
    if readiness.get("kind") != "value":
        # Model the compliant behavior: echo the gauge's abstention reasons
        # verbatim and never mention a pass probability (the coach reject
        # hook bans any such wording while the gauge abstains).
        reasons = readiness.get("missing") or ["insufficient data"]
        summary = (
            f"Readiness is abstaining ({' '.join(str(r) for r in reasons)}) "
            f"- no score is available, so prioritise by weighted gap: "
            f"start with {best}."
        )
    else:
        summary = f"Start with {best}; it carries the largest weighted gap."
    subjects = facts.get("subjects") or []
    priorities = [
        {
            "topic": row.get("name", "?"),
            "why": "largest weighted gap in the dashboard model",
        }
        for row in subjects[:1]
    ] or [{"topic": str(best), "why": "named best-next by the dashboard"}]
    return {"summary": summary, "priorities": priorities}


def _mock_tag_suggest(facts: dict[str, Any]) -> dict[str, Any]:
    """Keyword-match each tag against the supplied topic ids; else unsure."""
    suggestions = {}
    topics = [str(t) for t in facts.get("topics", [])]
    for row in facts.get("tags", []):
        tag = str(row.get("tag", ""))
        tokens = set(re.split(r"[^a-z]+", tag.lower()))
        match = next(
            (topic for topic in topics if tokens & set(str(topic).lower().split("_"))),
            None,
        )
        if match:
            suggestions[tag] = {"topic": match, "confidence": 0.9}
        else:
            suggestions[tag] = {"topic": "unsure", "confidence": 0.2}
    return {"suggestions": suggestions}


def _mock_invent_number(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": "Your probability of passing is 0.8731, nearly certain.",
        "priorities": [],
        "narrative": "You are 87.31% of the way there.",
        "next_step": "coast",
    }


_MOCK_REPLIES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "debrief": _mock_debrief,
    "coach": _mock_coach,
    "tag_suggest": _mock_tag_suggest,
    "generic": _mock_generic,
    "invent_number": _mock_invent_number,
}


def make_backend(
    name: str | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    failure_mode: str = "",
) -> models.Backend:
    """Backend from an explicit name > $SPEEDRUN_AI_BACKEND > mock."""
    name = name or os.environ.get("SPEEDRUN_AI_BACKEND") or "mock"
    if name == "mock":
        return MockAssistantBackend(failure_mode=failure_mode)
    if name == "claude-cli":
        return models.ClaudeCliBackend(
            model=os.environ.get("SPEEDRUN_AI_MODEL", "sonnet"), timeout=timeout
        )
    if name == "openai-compatible":
        return models.OpenAICompatBackend(
            model=os.environ.get("SPEEDRUN_AI_MODEL", "gpt-4o-mini"),
            timeout=timeout,
        )
    raise ValueError(f"unknown assistant backend {name!r}")


# ---------------------------------------------------------------------------
# Grounding: every number in the reply must already exist in the facts
# ---------------------------------------------------------------------------


#: Reply fields that carry sanctioned pipeline metadata, not fact claims.
#: The prompt template itself invites a "confidence" number, so grounding
#: must not treat one as an invented statistic.
_METADATA_KEYS = frozenset({"confidence"})


def _walk_strings(value: Any, *, skip_metadata: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for key, item in value.items():
            if skip_metadata and str(key) in _METADATA_KEYS:
                continue
            out.append(str(key))
            out.extend(_walk_strings(item, skip_metadata=skip_metadata))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_walk_strings(item, skip_metadata=skip_metadata))
        return out
    return []


def _walk_numbers(value: Any, *, skip_metadata: bool = False) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, Mapping):
        out: list[float] = []
        for key, item in value.items():
            if skip_metadata and str(key) in _METADATA_KEYS:
                continue
            out.extend(_walk_numbers(item, skip_metadata=skip_metadata))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_walk_numbers(item, skip_metadata=skip_metadata))
        return out
    return []


def _tokens_in_text(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in _NUMBER_RE.findall(text)]


def allowed_numbers(facts: Mapping[str, Any]) -> set[float]:
    """Every number a grounded reply may state, in the renderings a model
    plausibly echoes: the value itself, common roundings, and the percent
    form of 0-1 fractions. Numbers inside fact STRINGS count too (dates,
    pre-written reasons)."""
    allowed: set[float] = set()
    numbers = _walk_numbers(facts)
    for text in _walk_strings(facts):
        numbers.extend(_tokens_in_text(text))
    for n in numbers:
        allowed.update({n, round(n, 2), round(n, 1), round(n, 0)})
        if 0 <= n <= 1:
            allowed.update({round(n * 100, 1), round(n * 100, 0)})
    return allowed


def ungrounded_numbers(reply: Any, facts: Mapping[str, Any]) -> list[float]:
    """Numeric claims in the reply that no fact backs."""
    allowed = allowed_numbers(facts)
    bad: list[float] = []
    tokens = _walk_numbers(reply, skip_metadata=True)
    for text in _walk_strings(reply, skip_metadata=True):
        tokens.extend(_tokens_in_text(text))
    for token in tokens:
        if token == int(token) and abs(token) <= SMALL_INT_ALLOWANCE:
            continue
        if not any(abs(token - a) < 1e-6 for a in allowed):
            bad.append(token)
    return bad


# ---------------------------------------------------------------------------
# Minimal reply-schema validation
# ---------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "str": lambda v: isinstance(v, str) and bool(v.strip()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "list[str]": lambda v: isinstance(v, list)
    and all(isinstance(item, str) for item in v),
    "list[dict]": lambda v: isinstance(v, list)
    and all(isinstance(item, Mapping) for item in v),
    "dict": lambda v: isinstance(v, Mapping),
}


def schema_errors(reply: Mapping[str, Any], schema: Mapping[str, str]) -> list[str]:
    """Field names in ``schema`` map to type names in ``_TYPE_CHECKS``;
    a trailing ``?`` marks the field optional."""
    errors = []
    for field, type_name in schema.items():
        optional = type_name.endswith("?")
        type_name = type_name.rstrip("?")
        if field not in reply:
            if not optional:
                errors.append(f"missing field {field!r}")
            continue
        if not _TYPE_CHECKS[type_name](reply[field]):
            errors.append(f"field {field!r} is not a valid {type_name}")
    return errors


# ---------------------------------------------------------------------------
# The single-call grounded completion
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """{system}

TASK: {task}

FACTS (JSON; the ONLY numbers, names and quotes you may use):
{facts_json}

RESPONSE FORMAT: return ONLY a JSON object with these fields (no markdown
fence, no commentary): {schema_hint}

HARD RULES:
- Ground every statement in FACTS. NEVER state a number, score, count or
  probability that does not appear there - not even an estimate.
- If FACTS are too thin to answer, or you are unsure, return exactly
  {{"abstain": true, "reason": "<why>"}} instead.
- You may include a "confidence" number (0-1); low confidence abstains.
"""


def build_prompt(
    system: str,
    facts: Mapping[str, Any],
    schema: Mapping[str, str],
    task: str,
) -> str:
    schema_hint = json.dumps(dict(schema))
    return _PROMPT_TEMPLATE.format(
        system=system.strip(),
        task=task,
        facts_json=json.dumps(dict(facts), indent=1, sort_keys=True),
        schema_hint=schema_hint,
    )


def _complete_with_deadline(
    backend: models.Backend, prompt: str, timeout: float
) -> str | None:
    """Run one completion with a hard wall-clock cap.

    Backends carry their own socket/subprocess timeouts; this daemon-thread
    guard is the belt-and-suspenders cap so a wedged backend can never hang
    a caller past ``timeout`` seconds.
    """
    out: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            out.put(("ok", backend.complete(prompt)))
        except BaseException as exc:  # noqa: BLE001 - must never propagate
            out.put(("err", exc))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        kind, value = out.get(timeout=timeout)
    except queue.Empty:
        return None
    return value if kind == "ok" else None


def grounded_complete(
    system: str,
    facts: Mapping[str, Any],
    *,
    schema: Mapping[str, str],
    backend: models.Backend | None = None,
    task: str = "generic",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    reject: Callable[[Mapping[str, Any]], str | None] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One grounded completion; ``None`` means "abstained - use the
    deterministic view".

    ``reject`` is an optional feature-specific post-check: it receives the
    parsed reply and returns a rejection reason (abstain) or ``None``.
    ``diagnostics``, when supplied, is filled with the outcome for
    disclosure/debug surfaces; it never changes behavior.
    """

    def abstain(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["outcome"] = "abstained"
            diagnostics["reason"] = reason

    backend = backend or make_backend()
    prompt = build_prompt(system, facts, schema, task)
    raw = _complete_with_deadline(backend, prompt, timeout)
    if raw is None:
        return abstain("backend error or timeout")
    reply = models.parse_json_reply(raw)
    if reply is None:
        return abstain("reply was not a JSON object")
    if reply.get("abstain") is True:
        return abstain(f"model abstained: {reply.get('reason', 'unstated')}")
    confidence = reply.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < MIN_CONFIDENCE:
        return abstain(f"low confidence ({confidence})")
    errors = schema_errors(reply, schema)
    if errors:
        return abstain("schema: " + "; ".join(errors))
    bad = ungrounded_numbers(reply, facts)
    if bad:
        return abstain(
            "ungrounded numbers in reply: " + ", ".join(str(b) for b in bad[:5])
        )
    if reject is not None:
        reason = reject(reply)
        if reason:
            return abstain(reason)
    if diagnostics is not None:
        diagnostics["outcome"] = "ok"
        diagnostics["backend"] = getattr(backend, "name", "?")
    return reply
