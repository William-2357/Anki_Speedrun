# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Feature C - the tag->topic mapping suggester (pre-fill only; never save).

Classifies each unmapped raw tag (plus up to a few sample note fronts) onto
one of the canonical dashboard topics, ``ignore``, or ``unsure`` (abstain).
The result only pre-fills the Map-tags editor's dropdowns; persisting
``speedrun:tagTopicMap`` stays behind the user's explicit Save, unchanged.
Low-confidence suggestions are dropped here (left blank in the editor),
never forced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aig import models

from . import core

#: Suggestions below this confidence are dropped (the dropdown stays blank).
CONFIDENCE_FLOOR = 0.6
#: How many sample note fronts per tag the bridge sends for context.
SAMPLE_FRONTS_PER_TAG = 3

#: The schema only pins the outer shape; every entry inside "suggestions"
#: is re-validated per tag in ``_validated_suggestion``.
_REPLY_SCHEMA = {"suggestions": "dict"}

_SYSTEM = """\
You classify raw flashcard deck tags for a study dashboard.

Assign EVERY tag in FACTS["tags"] exactly one of:
- a canonical topic id from FACTS["topics"], copied verbatim;
- "ignore" for noise or administrative tags (bookkeeping such as leech,
  marked, todo, import batches) that describe no subject matter;
- "unsure" when the evidence is too thin to decide.

Judge only from the tag's name and its "sample_fronts" (sample note
fronts for that tag). Include a confidence number between 0 and 1 for
every tag. Never guess: a wrong mapping misattributes topic mastery, so
prefer "unsure" whenever in doubt.

Reply as {"suggestions": {<tag>: {"topic": <topic id | "ignore" |
"unsure">, "confidence": <0-1>}}} with one entry per input tag.
"""


def suggest_mappings(
    tags: Sequence[Mapping[str, Any]],
    topics: Sequence[str],
    backend: models.Backend,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """C1: one grounded call classifying every tag.

    ``tags``: [{"tag": str, "cards": int, "sample_fronts": [str]}].
    ``topics``: the canonical topic ids (sent by the page - the exam list
    lives in the frontend).

    Returns {tag: {"topic": <topic id | "ignore">, "confidence": float}}
    with only validated, confident suggestions; "unsure"/low-confidence
    tags are omitted. Any backend/parse failure returns {} (abstain).

    ``diagnostics``, when given, records reason "nothing to classify" on
    an empty-input skip, ``grounded_complete``'s outcome fields on the
    call itself, and - on success - ``kept``/``dropped`` counts, where
    ``dropped`` is the number of input tags left blank in the editor.
    """
    if not tags or not topics:
        if diagnostics is not None:
            diagnostics["reason"] = "nothing to classify"
        return {}
    topic_ids = [str(topic) for topic in topics]
    # The tags are passed through as-is: name, card count and sample
    # fronts are all app-computed facts the model may quote.
    facts = {"tags": [dict(row) for row in tags], "topics": topic_ids}
    reply = core.grounded_complete(
        _SYSTEM,
        facts,
        schema=_REPLY_SCHEMA,
        backend=backend,
        task="tag_suggest",
        diagnostics=diagnostics,
    )
    if reply is None:
        return {}
    raw = reply.get("suggestions")
    entries: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    known_tags = {str(row.get("tag", "")) for row in tags}
    allowed_topics = set(topic_ids)
    kept: dict[str, dict[str, Any]] = {}
    for tag, value in entries.items():
        if tag not in known_tags:
            continue
        validated = _validated_suggestion(value, allowed_topics)
        if validated is not None:
            kept[tag] = validated
    if diagnostics is not None:
        diagnostics["kept"] = len(kept)
        diagnostics["dropped"] = len(known_tags) - len(kept)
    return kept


def _validated_suggestion(
    value: Any, allowed_topics: set[str]
) -> dict[str, Any] | None:
    """One reply entry -> {"topic", "confidence"}, or None (drop).

    The model's labels are never trusted: the topic must be a known id or
    "ignore" ("unsure" is preserved abstention and stays dropped even at
    high confidence), and the confidence must be a real number in [0, 1]
    at or above ``CONFIDENCE_FLOOR``. Dropped tags stay blank; they are
    never coerced into a pick.
    """
    if not isinstance(value, Mapping):
        return None
    topic = value.get("topic")
    if not isinstance(topic, str) or topic == "unsure":
        return None
    if topic != "ignore" and topic not in allowed_topics:
        return None
    confidence = value.get("confidence")
    if isinstance(confidence, bool):
        return None
    if not isinstance(confidence, (int, float)):
        return None
    if not 0 <= confidence <= 1 or confidence < CONFIDENCE_FLOOR:
        return None
    return {"topic": topic, "confidence": float(confidence)}
