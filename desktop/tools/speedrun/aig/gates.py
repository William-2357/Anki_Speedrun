# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Machine validation gates (M1) - applied to EVERY item regardless of origin.

Gates:

- ``solve_check`` - self-consistency: an independent solver must pick exactly
  ONE choice equal to the labelled correct answer. For parameterized items
  the solver is the generator's independently written recomputation; for LLM
  items it is k solver samples (consensus). An item where more than one
  choice is defensible - the #1 AIG defect - is rejected.
- ``schema`` - validation against ITEM_SCHEMA.md. If a sibling
  ``ladder_schema.py`` exists (it may be written in parallel by the deck-
  builder workstream) it is imported lazily/defensively and consulted; its
  absence or breakage falls back to the equivalent internal checks below,
  which always run.
- ``rationale`` - feedback completeness [R9].
- ``numeric`` - dual-implementation agreement (1e-6 relative) + distractor
  margins for parameterized items.
- ``leakage`` - n-gram wall: stem-bearing fields are checked against the
  local CFA reference PDF text (extracted at runtime only, never stored) and
  against the grounding corpus; any verbatim overlap of >= 8 tokens rejects
  the item.

Acceptance is AUTOMATIC: an item clearing all gates is emitted. There is no
human sign-off anywhere (owner decision 2026-07-02; automation-bias risk
[R23] accepted - ungraded items never feed readiness [R24]).

Every decision is logged to eval/validation_report.json via
``ValidationReport``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from aig import pdf_text
from aig.generators import (
    GENERATOR_DECIMALS,
    INDEPENDENT_SOLVERS,
    MISCONCEPTIONS,
    NUMERIC_TOLERANCE,
    margins_ok,
)

NGRAM_N = 8

KINDS = {"worked", "cloze", "mcq", "compare"}
RUNGS = {"worked", "faded", "solve", "compare"}

_COMMON_FIELDS = {
    "schema",
    "kind",
    "rung",
    "topic",
    "cluster",
    "interactivity",
    "title",
    "rationale",
    "source",
    "provenance",
    "tags_extra",
}
_KIND_FIELDS = {
    "worked": {"prompt", "worked_steps"},
    "cloze": {"prompt", "cloze_text"},
    "mcq": {"stem", "choices", "correct", "distractor_rationales", "misconceptions"},
    "compare": {
        "left_title",
        "left_body",
        "right_title",
        "right_body",
        "discriminator",
    },
}

# Fields the leakage wall scans (the stem-like, learner-facing problem text).
_STEM_FIELDS = {
    "worked": ("prompt",),
    "cloze": ("prompt",),
    "mcq": ("stem",),
    "compare": ("discriminator", "left_body", "right_body"),
}


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "passed": self.passed, "reason": self.reason}


# ---------------------------------------------------------------------------
# schema gate (internal implementation + defensive external ladder_schema)
# ---------------------------------------------------------------------------


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_schema_internal(item: dict[str, Any]) -> list[str]:
    """Field-level validation implementing ITEM_SCHEMA.md. Returns errors."""
    errors: list[str] = []
    public = {k: v for k, v in item.items() if not k.startswith("_")}

    if public.get("schema") != "speedrun-item-v1":
        errors.append("schema must be the literal 'speedrun-item-v1'")
    kind = public.get("kind")
    if kind not in KINDS:
        errors.append(f"kind must be one of {sorted(KINDS)}")
        return errors  # kind-specific checks meaningless from here
    if public.get("rung") not in RUNGS:
        errors.append(f"rung must be one of {sorted(RUNGS)}")
    for f in ("topic", "cluster", "title", "rationale"):
        if not _nonempty_str(public.get(f)):
            errors.append(f"{f} must be a non-empty string")
    if public.get("interactivity") not in {"high", "low"}:
        errors.append("interactivity must be 'high' or 'low'")

    source = public.get("source")
    if not isinstance(source, dict) or not all(
        _nonempty_str(source.get(k)) for k in ("doc", "loc", "passage")
    ):
        errors.append("source must be {'doc','loc','passage'} with non-empty strings")

    prov = public.get("provenance")
    if not isinstance(prov, dict):
        errors.append("provenance must be an object")
    else:
        if not _nonempty_str(prov.get("generator")):
            errors.append("provenance.generator must be a non-empty string")
        if not isinstance(prov.get("gates"), list):
            errors.append("provenance.gates must be a list")
        if not isinstance(prov.get("graded"), bool):
            errors.append("provenance.graded must be a bool")

    if "tags_extra" in public and not (
        isinstance(public["tags_extra"], list)
        and all(_nonempty_str(t) for t in public["tags_extra"])
    ):
        errors.append("tags_extra must be a list of non-empty strings")

    allowed = _COMMON_FIELDS | _KIND_FIELDS[kind]
    unknown = set(public) - allowed
    if unknown:
        errors.append(f"unknown fields for kind {kind}: {sorted(unknown)}")

    if kind == "worked":
        if not _nonempty_str(public.get("prompt")):
            errors.append("worked.prompt must be a non-empty string")
        steps = public.get("worked_steps")
        if not (
            isinstance(steps, list) and steps and all(_nonempty_str(s) for s in steps)
        ):
            errors.append("worked_steps must be a non-empty list of steps")
    elif kind == "cloze":
        if not _nonempty_str(public.get("prompt")):
            errors.append("cloze.prompt must be a non-empty string")
        indices = _cloze_indices(public.get("cloze_text") or "")
        if len(indices) < 2:
            errors.append(
                "cloze_text needs >= 2 cloze indices ({{c1::..}}, {{c2::..}})"
            )
    elif kind == "mcq":
        if not _nonempty_str(public.get("stem")):
            errors.append("mcq.stem must be a non-empty string")
        choices = public.get("choices")
        if not (
            isinstance(choices, dict)
            and set(choices) == {"A", "B", "C"}
            and all(_nonempty_str(v) for v in choices.values())
        ):
            errors.append("choices must have exactly keys A,B,C with non-empty values")
        correct = public.get("correct")
        if correct not in {"A", "B", "C"}:
            errors.append("correct must be one of A,B,C")
        else:
            wrong = {"A", "B", "C"} - {correct}
            dr = public.get("distractor_rationales")
            if not (
                isinstance(dr, dict)
                and set(dr) == wrong
                and all(_nonempty_str(v) for v in dr.values())
            ):
                errors.append(
                    "distractor_rationales must cover exactly the two wrong letters"
                )
            mc = public.get("misconceptions")
            if mc is not None and not (
                isinstance(mc, dict)
                and set(mc) <= wrong
                and all(_nonempty_str(v) for v in mc.values())
            ):
                errors.append("misconceptions keys must be wrong letters")
    elif kind == "compare":
        for f in (
            "left_title",
            "left_body",
            "right_title",
            "right_body",
            "discriminator",
        ):
            if not _nonempty_str(public.get(f)):
                errors.append(f"compare.{f} must be a non-empty string")

    return errors


def _cloze_indices(text: str) -> set[str]:
    import re

    return set(re.findall(r"\{\{(c\d+)::", text))


def _external_validator() -> Callable[[dict[str, Any]], Any] | None:
    """Locate a sibling ladder_schema.py validator, defensively.

    The deck-builder workstream may be writing ladder_schema.py in parallel;
    import lazily and probe for a plausible entry point. Any breakage makes
    us fall back to the internal checks (which always run anyway).
    """
    try:
        import ladder_schema  # type: ignore[import-not-found]
    except Exception:
        return None
    for name in ("validate_item", "validate", "check_item", "validate_record"):
        fn = getattr(ladder_schema, name, None)
        if callable(fn):
            return fn
    return None


def gate_schema(item: dict[str, Any]) -> GateResult:
    errors = validate_schema_internal(item)
    external_note = ""
    fn = _external_validator()
    if fn is not None:
        public = {k: v for k, v in item.items() if not k.startswith("_")}
        try:
            result = fn(public)
            if result is False:
                errors.append("ladder_schema rejected the item")
            elif isinstance(result, (list, tuple)) and result:
                errors.extend(f"ladder_schema: {r}" for r in result)
        except (ValueError, AssertionError) as e:
            errors.append(f"ladder_schema rejected the item: {e}")
        except Exception as e:  # API mismatch and similar - do not block
            external_note = (
                f" (ladder_schema present but uncallable: {type(e).__name__})"
            )
    return GateResult(
        "schema",
        not errors,
        "; ".join(errors) + external_note if (errors or external_note) else "",
    )


# ---------------------------------------------------------------------------
# rationale gate ([R9] feedback completeness)
# ---------------------------------------------------------------------------


def gate_rationale(item: dict[str, Any]) -> GateResult:
    problems: list[str] = []
    if not _nonempty_str(item.get("rationale")):
        problems.append("rationale empty")
    if item.get("kind") == "mcq":
        wrong = {"A", "B", "C"} - {item.get("correct")}
        dr = item.get("distractor_rationales") or {}
        for letter in sorted(wrong):
            if not _nonempty_str(dr.get(letter)):
                problems.append(f"missing distractor rationale for {letter}")
        mc = item.get("misconceptions") or {}
        for letter, mid in mc.items():
            if mid not in MISCONCEPTIONS:
                problems.append(f"unknown misconception id {mid!r} on {letter}")
    if item.get("kind") == "worked" and not item.get("worked_steps"):
        problems.append("worked_steps empty")
    if (
        item.get("kind") == "cloze"
        and len(_cloze_indices(item.get("cloze_text") or "")) < 2
    ):
        problems.append("cloze needs >= 2 indices for fading to have an order")
    return GateResult("rationale", not problems, "; ".join(problems))


# ---------------------------------------------------------------------------
# numeric gate (parameterized items)
# ---------------------------------------------------------------------------


def gate_numeric(item: dict[str, Any]) -> GateResult:
    aig = item.get("_aig") or {}
    gen_id = (item.get("provenance") or {}).get("generator", "")
    if not gen_id.startswith("param:"):
        return GateResult("numeric", True, "not a parameterized item; skipped")
    if "answer" not in aig:
        # Compare items are deterministic prose - nothing numeric to check.
        if item.get("kind") == "compare":
            return GateResult("numeric", True, "compare item; skipped")
        return GateResult("numeric", False, "missing numeric metadata")
    a, b = aig["answer"], aig["answer_check"]
    if not (math.isfinite(a) and math.isfinite(b)):
        return GateResult("numeric", False, "non-finite answer")
    rel = abs(a - b) / max(abs(a), abs(b), 1.0)
    if rel > NUMERIC_TOLERANCE:
        return GateResult(
            "numeric",
            False,
            f"independent recomputation disagrees: {a!r} vs {b!r} (rel {rel:.3g})",
        )
    if item.get("kind") == "mcq":
        values = list((aig.get("choice_values") or {}).values())
        if len(values) != 3:
            return GateResult("numeric", False, "mcq missing choice values")
        if not margins_ok(values):
            return GateResult(
                "numeric", False, "choice margin violation (<0.5% relative)"
            )
    return GateResult("numeric", True, "")


# ---------------------------------------------------------------------------
# solve-check gate (self-consistency; the #1 AIG defect filter)
# ---------------------------------------------------------------------------


def gate_solve_check(item: dict[str, Any], llm_path: Any = None) -> GateResult:
    if item.get("kind") != "mcq":
        return GateResult("solve_check", True, "not an mcq; skipped")
    gen_id = (item.get("provenance") or {}).get("generator", "")
    if gen_id.startswith("param:"):
        return _solve_check_param(item, gen_id)
    if llm_path is None:
        return GateResult("solve_check", False, "no solver available for llm item")
    agree, picks = llm_path.solver_consensus(item)
    if not agree:
        return GateResult(
            "solve_check",
            False,
            f"solver consensus failed: picks {picks} vs labelled {item.get('correct')}",
        )
    return GateResult("solve_check", True, f"{len(picks)} solver samples agree")


def _solve_check_param(item: dict[str, Any], gen_id: str) -> GateResult:
    aig = item.get("_aig") or {}
    solver = INDEPENDENT_SOLVERS.get(gen_id)
    if solver is None or "params" not in aig:
        return GateResult("solve_check", False, f"no independent solver for {gen_id}")
    decimals = GENERATOR_DECIMALS.get(gen_id, 2)
    recomputed = round(solver(aig["params"]), decimals)
    choice_values: dict[str, float] = aig.get("choice_values") or {}
    if set(choice_values) != {"A", "B", "C"}:
        return GateResult("solve_check", False, "missing numeric choice values")
    # A choice "matches" when it equals the recomputed answer at display
    # precision. More than one match means two defensible answers.
    tol = 0.5 * 10.0**-decimals
    matches = [
        letter
        for letter, v in sorted(choice_values.items())
        if abs(v - recomputed) <= tol + 1e-12
    ]
    if len(matches) != 1:
        return GateResult(
            "solve_check",
            False,
            f"expected exactly one defensible choice, found {matches or 'none'} "
            f"(recomputed {recomputed})",
        )
    if matches[0] != item.get("correct"):
        return GateResult(
            "solve_check",
            False,
            f"independent solver picked {matches[0]}, labelled {item.get('correct')}",
        )
    return GateResult("solve_check", True, "")


# ---------------------------------------------------------------------------
# leakage gate (n-gram wall)
# ---------------------------------------------------------------------------


class LeakageWall:
    """8-gram wall against the reference PDF and the grounding corpus.

    Reference text is extracted at runtime only and held in memory; it is
    never written anywhere (the PDF itself stays local and git-ignored).
    """

    def __init__(
        self,
        reference_pdf: str | Path | None = None,
        corpus_texts: dict[str, str] | None = None,
        n: int = NGRAM_N,
    ) -> None:
        self.n = n
        self.reference_available = False
        self._ref_ngrams: set[tuple[str, ...]] = set()
        self._corpus_ngrams: set[tuple[str, ...]] = set()
        if reference_pdf and Path(reference_pdf).exists():
            try:
                tokens = pdf_text.tokenize(pdf_text.extract_pdf_text(reference_pdf))
                if len(tokens) >= 500:
                    self._ref_ngrams = pdf_text.ngram_set(tokens, n)
                    self.reference_available = True
            except Exception:
                self.reference_available = False
        for text in (corpus_texts or {}).values():
            self._corpus_ngrams |= pdf_text.ngram_set(pdf_text.tokenize(text), n)

    def add_reference_text(self, text: str) -> None:
        """Testing hook: inject additional reference text into the wall."""
        self._ref_ngrams |= pdf_text.ngram_set(pdf_text.tokenize(text), self.n)
        self.reference_available = True

    def check(self, text: str) -> tuple[bool, str]:
        grams = pdf_text.ngram_set(pdf_text.tokenize(text), self.n)
        hit = grams & self._ref_ngrams
        if hit:
            return (
                False,
                f"{self.n}-gram overlap with reference: {' '.join(next(iter(hit)))!r}",
            )
        hit = grams & self._corpus_ngrams
        if hit:
            return (
                False,
                f"{self.n}-gram overlap with corpus: {' '.join(next(iter(hit)))!r}",
            )
        return True, ""


def gate_leakage(item: dict[str, Any], wall: LeakageWall | None) -> GateResult:
    if wall is None:
        return GateResult("leakage", True, "no wall configured; skipped")
    problems: list[str] = []
    for field in _STEM_FIELDS.get(item.get("kind", ""), ()):
        text = item.get(field) or ""
        ok, reason = wall.check(text)
        if not ok:
            problems.append(f"{field}: {reason}")
    note = (
        "" if wall.reference_available else " (reference pdf unavailable; corpus-only)"
    )
    return GateResult(
        "leakage", not problems, "; ".join(problems) + (note if problems else "")
    )


# ---------------------------------------------------------------------------
# Orchestration + report
# ---------------------------------------------------------------------------


# Gate execution order. schema runs LAST because the source field is only
# attached by the retrieval-grounding step.
def run_gates(
    item: dict[str, Any],
    wall: LeakageWall | None = None,
    llm_path: Any = None,
) -> list[GateResult]:
    results = [
        gate_numeric(item),
        gate_solve_check(item, llm_path=llm_path),
        gate_rationale(item),
        gate_leakage(item, wall),
        gate_schema(item),
    ]
    return results


def all_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)


class ValidationReport:
    """Collects every gate decision; serialized to eval/validation_report.json."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(
        self,
        item_id: str,
        item: dict[str, Any],
        results: list[GateResult],
        emitted: bool,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.entries.append(
            {
                "id": item_id,
                "title": item.get("title", ""),
                "kind": item.get("kind", ""),
                "cluster": item.get("cluster", ""),
                "generator": (item.get("provenance") or {}).get("generator", ""),
                "gates": [r.as_dict() for r in results],
                "all_passed": all_passed(results),
                "emitted": emitted,
                **(extra or {}),
            }
        )

    def summary(self) -> dict[str, Any]:
        per_gate: dict[str, dict[str, int]] = {}
        for e in self.entries:
            for g in e["gates"]:
                s = per_gate.setdefault(g["gate"], {"pass": 0, "fail": 0})
                s["pass" if g["passed"] else "fail"] += 1
        return {
            "items_seen": len(self.entries),
            "items_emitted": sum(1 for e in self.entries if e["emitted"]),
            "items_rejected_by_gates": sum(
                1 for e in self.entries if not e["all_passed"]
            ),
            "items_validated_not_emitted": sum(
                1 for e in self.entries if e["all_passed"] and not e["emitted"]
            ),
            "per_gate": per_gate,
        }

    def write(self, path: str | Path, meta: dict[str, Any] | None = None) -> None:
        payload = {
            "meta": {
                "description": (
                    "Per-item machine gate decisions for the fully-automated "
                    "AIG pipeline. Acceptance is automatic: an item clearing "
                    "all gates is emitted; no human sign-off anywhere (owner "
                    "decision 2026-07-02; [R23] automation-bias risk accepted, "
                    "compensated by aig::ungraded never feeding readiness [R24])."
                ),
                **(meta or {}),
            },
            "summary": self.summary(),
            "items": self.entries,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=1) + "\n")
