# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Validation for speedrun-item-v1 records + the [R9] feedback lint.

Implements the contract in ITEM_SCHEMA.md, shared by the AIG pipeline (which
emits records) and the deck builder (which consumes them):

* ``validate_item`` checks one JSON-decoded item and returns human-readable
  errors (empty list = valid).
* ``tags_for_item`` derives the mechanical note tags per the "Tagging"
  section (M2: tags only, no schema change, rides native sync).
* ``lint_notetype_feedback`` is the build-time template lint enforcing the
  mandatory-feedback invariant [R9]: a template passes only when its answer
  side actually *renders* the Rationale field (Back Extra for cloze).
  A rung shipping without a feedback step silently nulls the testing effect
  (Goncalves 2025 g=0.14 -> g=0.50 with feedback; Rowland 2014), so this is
  enforced mechanically, not by convention.

stdlib only: the unit tests and the AIG pipeline import this module without
the anki package; only the deck builder (build_ladder_deck.py) touches pylib.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_LITERAL = "speedrun-item-v1"

KINDS = ("worked", "cloze", "mcq", "compare")
RUNGS = ("worked", "faded", "solve", "compare")
#: Each kind sits on exactly one rung of the ladder (compare outside it, with
#: rung::compare for bookkeeping). A mismatched pair would corrupt the
#: engine's per-cluster ladder, so the pairing is validated, not assumed.
RUNG_FOR_KIND = {
    "worked": "worked",
    "cloze": "faded",
    "mcq": "solve",
    "compare": "compare",
}
INTERACTIVITY_LEVELS = ("high", "low")
CHOICE_KEYS = ("A", "B", "C")

TOPIC_TAG_PREFIX = "cfa::topic::"
CLUSTER_TAG_PREFIX = "cluster::"
RUNG_TAG_PREFIX = "rung::"
INTERACTIVITY_TAG_PREFIX = "interactivity::"
GRADED_TAG = "aig::graded"
UNGRADED_TAG = "aig::ungraded"

_CLOZE_INDEX_RE = re.compile(r"\{\{c(\d+)::")
_TEMPLATE_REF_RE = re.compile(r"\{\{([^{}]+)\}\}")


def cloze_indices(text: str) -> list[int]:
    """Distinct cloze indices referenced by native cloze markup, sorted."""
    return sorted({int(number) for number in _CLOZE_INDEX_RE.findall(text)})


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_whitespace(value: str) -> bool:
    return any(ch.isspace() for ch in value)


def validate_item(item: Any) -> list[str]:
    """Validate one speedrun-item-v1 record; returns human-readable errors.

    An empty list means the item satisfies ITEM_SCHEMA.md. The deck builder
    must refuse to emit any record for which this returns errors.
    """
    if not isinstance(item, Mapping):
        return ["item: expected a JSON object"]
    errors: list[str] = []

    schema = item.get("schema")
    if schema != SCHEMA_LITERAL:
        errors.append(
            f'schema: expected the literal "{SCHEMA_LITERAL}", got {schema!r}'
        )

    kind = item.get("kind")
    if kind not in KINDS:
        errors.append(f"kind: expected one of {'/'.join(KINDS)}, got {kind!r}")
    rung = item.get("rung")
    if rung not in RUNGS:
        errors.append(f"rung: expected one of {'/'.join(RUNGS)}, got {rung!r}")
    elif kind in KINDS and rung != RUNG_FOR_KIND[kind]:
        errors.append(
            f"rung: kind {kind!r} sits on rung {RUNG_FOR_KIND[kind]!r}, got {rung!r}"
        )

    _check_tag_component(item, "topic", TOPIC_TAG_PREFIX, errors)
    _check_tag_component(item, "cluster", CLUSTER_TAG_PREFIX, errors)

    interactivity = item.get("interactivity")
    if interactivity not in INTERACTIVITY_LEVELS:
        errors.append(
            f"interactivity: expected high or low ([R17]), got {interactivity!r}"
        )

    if not _is_nonempty_str(item.get("title")):
        errors.append("title: expected a non-empty string")

    if not _is_nonempty_str(item.get("rationale")):
        errors.append(
            "rationale: must be a non-empty string - every item ends in a "
            "feedback step [R9]"
        )

    _check_source(item.get("source"), errors)
    _check_provenance(item.get("provenance"), errors)
    _check_tags_extra(item.get("tags_extra"), errors)

    if kind == "worked":
        _check_worked(item, errors)
    elif kind == "cloze":
        _check_cloze(item, errors)
    elif kind == "mcq":
        _check_mcq(item, errors)
    elif kind == "compare":
        _check_compare(item, errors)

    return errors


def _check_tag_component(
    item: Mapping[str, Any], field: str, prefix: str, errors: list[str]
) -> None:
    value = item.get(field)
    if not _is_nonempty_str(value):
        errors.append(f"{field}: expected a non-empty string")
        return
    if _has_whitespace(value):
        errors.append(
            f"{field}: must not contain whitespace (tags are space-separated), "
            f"got {value!r}"
        )
    if value.startswith(prefix):
        errors.append(
            f"{field}: give the suffix only - the builder prepends {prefix!r}"
        )


def _check_source(source: Any, errors: list[str]) -> None:
    if not isinstance(source, Mapping):
        errors.append('source: expected an object {"doc", "loc", "passage"} [R21]')
        return
    for key in ("doc", "loc", "passage"):
        if not isinstance(source.get(key), str):
            errors.append(f"source.{key}: expected a string")
    if isinstance(source.get("doc"), str) and not source["doc"].strip():
        errors.append("source.doc: the named source must not be empty [R21]")


def _check_provenance(provenance: Any, errors: list[str]) -> None:
    if not isinstance(provenance, Mapping):
        errors.append("provenance: expected an object with generator/gates/graded")
        return
    if not _is_nonempty_str(provenance.get("generator")):
        errors.append("provenance.generator: expected a non-empty string")
    gates = provenance.get("gates")
    if not isinstance(gates, list) or not all(_is_nonempty_str(gate) for gate in gates):
        errors.append(
            "provenance.gates: expected a list of non-empty strings (may be empty)"
        )
    if not isinstance(provenance.get("graded"), bool):
        errors.append(
            "provenance.graded: expected true or false - it drives the "
            "aig::graded/aig::ungraded tag and readiness exclusion [R24]"
        )


def _check_tags_extra(tags_extra: Any, errors: list[str]) -> None:
    if tags_extra is None:
        return
    if not isinstance(tags_extra, list):
        errors.append("tags_extra: expected an array of tag strings")
        return
    for tag in tags_extra:
        if not _is_nonempty_str(tag) or _has_whitespace(tag):
            errors.append(
                "tags_extra: each entry must be a non-empty string without "
                f"whitespace, got {tag!r}"
            )


def _check_worked(item: Mapping[str, Any], errors: list[str]) -> None:
    if not _is_nonempty_str(item.get("prompt")):
        errors.append("prompt: expected a non-empty string")
    steps = item.get("worked_steps")
    if not isinstance(steps, list) or not steps:
        errors.append("worked_steps: expected a non-empty array of solution steps")
    elif not all(_is_nonempty_str(step) for step in steps):
        errors.append("worked_steps: every step must be a non-empty string")


def _check_cloze(item: Mapping[str, Any], errors: list[str]) -> None:
    if not _is_nonempty_str(item.get("prompt")):
        errors.append("prompt: expected a non-empty string")
    cloze_text = item.get("cloze_text")
    if not _is_nonempty_str(cloze_text):
        errors.append("cloze_text: expected a non-empty string of native cloze markup")
        return
    found = cloze_indices(cloze_text)
    if 0 in found:
        errors.append("cloze_text: cloze index 0 ({{c0::...}}) never generates a card")
    valid = [index for index in found if index >= 1]
    if len(valid) < 2:
        errors.append(
            "cloze_text: needs at least 2 distinct cloze indices "
            "({{c1::...}}, {{c2::...}}) so fading has an order to work with, "
            f"found {len(valid)}"
        )


def _check_mcq(item: Mapping[str, Any], errors: list[str]) -> None:
    if not _is_nonempty_str(item.get("stem")):
        errors.append("stem: expected a non-empty string")

    choices = item.get("choices")
    if not isinstance(choices, Mapping) or set(choices.keys()) != set(CHOICE_KEYS):
        got = sorted(choices.keys()) if isinstance(choices, Mapping) else choices
        errors.append(
            f"choices: expected exactly the keys A/B/C (CFA L1 format), got {got!r}"
        )
    elif not all(_is_nonempty_str(choices[key]) for key in CHOICE_KEYS):
        errors.append("choices: every choice must be a non-empty string")

    correct = item.get("correct")
    if correct not in CHOICE_KEYS:
        errors.append(f"correct: expected one of A/B/C, got {correct!r}")

    rationales = item.get("distractor_rationales")
    if not isinstance(rationales, Mapping):
        errors.append(
            "distractor_rationales: expected an object keyed by the two wrong "
            "letters [R22]"
        )
    else:
        if correct in CHOICE_KEYS:
            wrong_letters = {key for key in CHOICE_KEYS if key != correct}
            missing = sorted(wrong_letters - set(rationales.keys()))
            extra = sorted(str(key) for key in set(rationales.keys()) - wrong_letters)
            if missing:
                errors.append(
                    "distractor_rationales: missing entry for wrong letter(s) "
                    f"{', '.join(missing)} - every distractor's misconception "
                    "must be explained [R9][R22]"
                )
            if extra:
                errors.append(
                    "distractor_rationales: unexpected key(s) "
                    f"{', '.join(extra)} (only the two wrong letters belong here)"
                )
        for key, value in rationales.items():
            if not _is_nonempty_str(value):
                errors.append(
                    f"distractor_rationales.{key}: must be a non-empty string [R22]"
                )

    misconceptions = item.get("misconceptions")
    if misconceptions is not None:
        if not isinstance(misconceptions, Mapping):
            errors.append(
                "misconceptions: expected an object of wrong letter -> misconception id"
            )
        else:
            for key, value in misconceptions.items():
                if key not in CHOICE_KEYS or key == correct:
                    errors.append(f"misconceptions: key {key!r} is not a wrong letter")
                if not _is_nonempty_str(value):
                    errors.append(
                        f"misconceptions.{key}: expected a non-empty misconception id"
                    )


def _check_compare(item: Mapping[str, Any], errors: list[str]) -> None:
    for field in (
        "left_title",
        "left_body",
        "right_title",
        "right_body",
        "discriminator",
    ):
        if not _is_nonempty_str(item.get(field)):
            errors.append(f"{field}: expected a non-empty string")


def tags_for_item(item: Mapping[str, Any]) -> list[str]:
    """The note's mechanical tags, exactly as ITEM_SCHEMA.md "Tagging" derives
    them. Assumes the item already passed ``validate_item``.
    """
    provenance = item["provenance"]
    tags = [
        f"{TOPIC_TAG_PREFIX}{item['topic']}",
        f"{CLUSTER_TAG_PREFIX}{item['cluster']}",
        f"{RUNG_TAG_PREFIX}{item['rung']}",
        f"{INTERACTIVITY_TAG_PREFIX}{item['interactivity']}",
        GRADED_TAG if provenance["graded"] else UNGRADED_TAG,
    ]
    tags.extend(item.get("tags_extra", []))
    return tags


def rendered_fields(template_text: str) -> set[str]:
    """Field names a template side actually renders.

    Conditional markers ({{#Field}}, {{^Field}}, {{/Field}}) do not render
    the field, so they do not count. Filtered references resolve to their
    final component ({{cloze:Text}} -> Text, {{text:Rationale}} -> Rationale).
    """
    fields: set[str] = set()
    for match in _TEMPLATE_REF_RE.finditer(template_text):
        inner = match.group(1).strip()
        if inner.startswith(("#", "/", "^")):
            continue
        fields.add(inner.split(":")[-1].strip())
    return fields


def lint_notetype_feedback(
    templates: Iterable[Mapping[str, Any]],
    feedback_fields: tuple[str, ...] = ("Rationale", "Back Extra"),
) -> list[str]:
    """The [R9] feedback-invariant template lint.

    ``templates`` is an iterable of template mappings with at least "name"
    and "afmt" keys - both the specs in ladder_notetypes.py and Anki's own
    legacy template dicts ("tmpls") satisfy this shape.

    A template passes only if its answer side renders one of
    ``feedback_fields`` (the Rationale field for the custom note types,
    Back Extra for stock cloze). The deck builder refuses to create any
    notetype failing this lint.
    """
    errors: list[str] = []
    template_list = list(templates)
    if not template_list:
        errors.append("notetype has no templates - nothing ends in feedback [R9]")
        return errors
    wanted = set(feedback_fields)
    for index, template in enumerate(template_list):
        name = str(template.get("name") or f"ord {index}")
        afmt = str(template.get("afmt") or "")
        if not rendered_fields(afmt) & wanted:
            errors.append(
                f"template {name!r}: answer side never renders a feedback field "
                f"({' or '.join(feedback_fields)}) - every rung must end in a "
                "reveal/feedback step [R9]"
            )
    return errors
