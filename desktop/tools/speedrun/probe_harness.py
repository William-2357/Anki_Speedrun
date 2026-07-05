# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The one-command held-out probe harness (Anki Speedrun, Phase 3 M3).

Validates the hand-authored delayed-paraphrase probe bank
(probes/probe_bank.jsonl, contract in probes/PROBE_SCHEMA.md), walls it off
from every generator input (challenge 7e leakage scan, BOTH directions),
extracts real probe outcomes from a collection's revlog with the SAME
delay rule as rslib/src/readiness/probes.rs, reports the memory->
performance bridge proof (challenge 7d) and the study->probe lag
distribution, fits temperature calibration on the disjoint calibration
pool, and (only with --apply) writes the `speedrun:readinessCalibration`
config record the readiness RPC surfaces.

Modes (one command does everything available - challenge 7h):

* no flags            -> validation + leakage scan + self-test (CI default;
                         stdlib only, no collection, no pylib)
* --leakage-scan      -> kept for explicitness; the scan always runs
* --self-test         -> force the synthetic end-to-end run
* --collection PATH   -> ALSO read the collection read-only (sqlite3) and
                         report outcomes / bridge proof / calibration
* --apply             -> with --collection: write the calibration record
                         via pylib Collection.set_config and verify by
                         reading it back (pylib imported lazily, only here)

Reports: eval/probe_harness_report.json + eval/probe_harness_report.md.
Exit code is non-zero on any validation failure, leakage hit, self-test
failure, or a refused --apply.

stdlib only. The leakage n-gram logic is reused from aig/gates.py
(LeakageWall) and aig/pdf_text.py, which are themselves stdlib-only.
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import glob
import json
import math
import random
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from aig.gates import LeakageWall  # noqa: E402  (stdlib-only module)
from aig.pdf_text import tokenize  # noqa: E402

# ---------------------------------------------------------------------------
# Contract constants (PROBE_SCHEMA.md; must mirror rslib/src/readiness/)
# ---------------------------------------------------------------------------

SCHEMA_LITERAL = "speedrun-probe-v1"
CONCEPT_COUNT = 35
#: Deterministic pool partition: c01..c25 -> performance, c26..c35 ->
#: calibration (concept-disjoint; see PROBE_SCHEMA.md for the rationale).
PERFORMANCE_CONCEPT_MAX = 25
#: The Rust give-up gate needs >= 50 delayed performance-pool outcomes.
MIN_PERFORMANCE_ITEMS = 50
VARIANTS = ("a", "b")
POOLS = ("performance", "calibration")
CHOICE_KEYS = ("A", "B", "C")

TOPICS = (
    "ethics",
    "quantitative_methods",
    "economics",
    "financial_statement_analysis",
    "corporate_issuers",
    "equity_investments",
    "fixed_income",
    "derivatives",
    "alternative_investments",
    "portfolio_management",
)

#: cluster first component -> required topic (typo guard; bank policy).
CLUSTER_PREFIX_TOPIC = {
    "ethics": "ethics",
    "quant": "quantitative_methods",
    "qm": "quantitative_methods",
    "econ": "economics",
    "fsa": "financial_statement_analysis",
    "corp": "corporate_issuers",
    "equity": "equity_investments",
    "fi": "fixed_income",
    "deriv": "derivatives",
    "alt": "alternative_investments",
    "pm": "portfolio_management",
}

#: Tags the deck builder writes; probes.rs keys off the first three kinds.
PROBE_HELD_OUT_TAG = "probe::held_out"
POOL_TAG_PREFIX = "probe::pool::"
CONCEPT_TAG_PREFIX = "probe::concept::"
VARIANT_TAG_PREFIX = "probe::variant::"
TOPIC_TAG_PREFIX = "cfa::topic::"
CLUSTER_TAG_PREFIX = "cluster::"

#: Outcome/delay rule (mirrors rslib/src/readiness/probes.rs exactly).
MIN_PROBE_DELAY_DAYS = 7.0
MS_PER_DAY = 86_400_000

#: Calibration contract (PROBE_SCHEMA.md).
CALIBRATION_CONFIG_KEY = "speedrun:readinessCalibration"
MIN_CALIBRATION_OUTCOMES = 10
#: Documented prediction proxy: most recent <= 20 graded non-probe reviews
#: of the probe's cluster before its first answer, add-one smoothed;
#: never-studied clusters predict the 3-choice chance rate exactly.
PREDICTION_WINDOW_REVIEWS = 20
NEVER_STUDIED_PREDICTION = 1.0 / 3.0
#: Bridge-proof retention window (trailing days of study activity).
RETENTION_WINDOW_DAYS = 30

#: Variant divergence: token Jaccard of the two stems (lowercased,
#: stopword-stripped) must be strictly below this.
VARIANT_JACCARD_MAX = 0.7
STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be been at by with "
    "from as that this it its into over under per which what whose when "
    "each most best closest likely because should must than then".split()
)

NGRAM_N = 8
MIN_STEM_WORDS = 15

SELF_TEST_SEED = 20260704
SELF_TEST_DATE = "2026-07-04"

_CONCEPT_RE = re.compile(r"^c(\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Record + bank validation
# ---------------------------------------------------------------------------


def pool_for_concept(concept_id: str) -> str | None:
    """The deterministic partition rule (PROBE_SCHEMA.md)."""
    match = _CONCEPT_RE.match(concept_id or "")
    if not match:
        return None
    number = int(match.group(1))
    if not 1 <= number <= CONCEPT_COUNT:
        return None
    return "performance" if number <= PERFORMANCE_CONCEPT_MAX else "calibration"


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_probe(record: Any) -> list[str]:
    """Field-level validation of one record; empty list = valid."""
    if not isinstance(record, dict):
        return ["record: expected a JSON object"]
    errors: list[str] = []

    if record.get("schema") != SCHEMA_LITERAL:
        errors.append(
            f'schema: expected the literal "{SCHEMA_LITERAL}", '
            f"got {record.get('schema')!r}"
        )

    concept_id = record.get("concept_id")
    expected_pool = (
        pool_for_concept(concept_id) if isinstance(concept_id, str) else None
    )
    if expected_pool is None:
        errors.append(f"concept_id: expected c01..c{CONCEPT_COUNT}, got {concept_id!r}")

    if record.get("variant") not in VARIANTS:
        errors.append(f"variant: expected 'a' or 'b', got {record.get('variant')!r}")

    pool = record.get("pool")
    if pool not in POOLS:
        errors.append(f"pool: expected performance or calibration, got {pool!r}")
    elif expected_pool is not None and pool != expected_pool:
        errors.append(
            f"pool: concept {concept_id} belongs to the {expected_pool} pool by "
            f"the deterministic partition rule (c01..c{PERFORMANCE_CONCEPT_MAX:02d}"
            f" -> performance), got {pool!r}"
        )

    topic = record.get("topic")
    if topic not in TOPICS:
        errors.append(f"topic: expected one of the 10 blueprint areas, got {topic!r}")

    cluster = record.get("cluster")
    if not _nonempty_str(cluster):
        errors.append("cluster: expected a non-empty string")
    else:
        if any(ch.isspace() for ch in cluster):
            errors.append(f"cluster: must not contain whitespace, got {cluster!r}")
        if cluster.startswith(CLUSTER_TAG_PREFIX):
            errors.append(
                f"cluster: give the suffix only - the builder prepends "
                f"{CLUSTER_TAG_PREFIX!r}"
            )
        elif "::" not in cluster:
            errors.append(
                f"cluster: expected >= 2 ::-separated components, got {cluster!r}"
            )
        else:
            prefix = cluster.split("::", 1)[0]
            required = CLUSTER_PREFIX_TOPIC.get(prefix)
            if required is None:
                errors.append(f"cluster: unknown topic prefix {prefix!r}")
            elif topic in TOPICS and topic != required:
                errors.append(
                    f"cluster: prefix {prefix!r} implies topic {required!r}, "
                    f"record says {topic!r}"
                )

    if not _nonempty_str(record.get("title")):
        errors.append("title: expected a non-empty string")

    stem = record.get("stem")
    if not _nonempty_str(stem):
        errors.append("stem: expected a non-empty string")
    elif len(stem.split()) < MIN_STEM_WORDS:
        errors.append(
            f"stem: application scenarios need >= {MIN_STEM_WORDS} words, "
            f"got {len(stem.split())} (definition one-liners are not probes)"
        )

    choices = record.get("choices")
    if not isinstance(choices, dict) or set(choices) != set(CHOICE_KEYS):
        errors.append("choices: expected exactly the keys A/B/C")
    elif not all(_nonempty_str(choices[key]) for key in CHOICE_KEYS):
        errors.append("choices: every choice must be a non-empty string")
    elif len({choices[key].strip() for key in CHOICE_KEYS}) != 3:
        errors.append("choices: the three choices must be pairwise distinct")

    correct = record.get("correct")
    if correct not in CHOICE_KEYS:
        errors.append(f"correct: expected one of A/B/C, got {correct!r}")

    rationale = record.get("rationale")
    if not _nonempty_str(rationale):
        errors.append("rationale: expected a non-empty string")
    elif correct in CHOICE_KEYS:
        for wrong in sorted(set(CHOICE_KEYS) - {correct}):
            if not re.search(rf"\b{wrong}\b", rationale):
                errors.append(
                    f"rationale: must explain why distractor {wrong} is wrong "
                    f"(no mention of {wrong} found)"
                )

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        errors.append('provenance: expected {"author": "hand", "date": "YYYY-MM-DD"}')
    else:
        if provenance.get("author") != "hand":
            errors.append(
                "provenance.author: probes are hand-authored; expected 'hand', "
                f"got {provenance.get('author')!r}"
            )
        date = provenance.get("date")
        if not (isinstance(date, str) and _DATE_RE.match(date)):
            errors.append(f"provenance.date: expected YYYY-MM-DD, got {date!r}")
        extra = set(provenance) - {"author", "date"}
        if extra:
            errors.append(f"provenance: unexpected keys {sorted(extra)}")

    known = {
        "schema",
        "concept_id",
        "variant",
        "pool",
        "topic",
        "cluster",
        "title",
        "stem",
        "choices",
        "correct",
        "rationale",
        "provenance",
    }
    unknown = set(record) - known
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")

    return errors


def stem_tokens(stem: str) -> set[str]:
    """Lowercased alphanumeric tokens minus the fixed stopword list."""
    return {token for token in tokenize(stem) if token not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def variant_divergence(records: list[dict]) -> dict[str, float]:
    """Per-concept stem Jaccard between variants a and b."""
    by_concept: dict[str, dict[str, str]] = {}
    for record in records:
        by_concept.setdefault(record["concept_id"], {})[record["variant"]] = record[
            "stem"
        ]
    out: dict[str, float] = {}
    for concept_id, variants in sorted(by_concept.items()):
        if set(variants) == {"a", "b"}:
            out[concept_id] = round(
                jaccard(stem_tokens(variants["a"]), stem_tokens(variants["b"])), 4
            )
    return out


def validate_bank(records: list[dict]) -> list[str]:
    """Bank-level invariants on top of per-record validation."""
    errors: list[str] = []
    for index, record in enumerate(records):
        label = (
            record.get("title") if isinstance(record, dict) else None
        ) or f"record {index + 1}"
        errors.extend(f"{label}: {error}" for error in validate_probe(record))
    if errors:
        return errors  # bank-level checks assume well-formed records

    if len(records) != 2 * CONCEPT_COUNT:
        errors.append(f"bank: expected {2 * CONCEPT_COUNT} records, got {len(records)}")

    expected_ids = {f"c{number:02d}" for number in range(1, CONCEPT_COUNT + 1)}
    seen: dict[str, list[str]] = {}
    for record in records:
        seen.setdefault(record["concept_id"], []).append(record["variant"])
    missing = expected_ids - set(seen)
    if missing:
        errors.append(f"bank: missing concepts {sorted(missing)}")
    for concept_id, variants in sorted(seen.items()):
        if sorted(variants) != ["a", "b"]:
            errors.append(
                f"bank: concept {concept_id} needs exactly variants a+b, "
                f"got {sorted(variants)}"
            )

    for concept_id in sorted(seen):
        pair = [r for r in records if r["concept_id"] == concept_id]
        for fld in ("pool", "topic", "cluster"):
            if len({r[fld] for r in pair}) != 1:
                errors.append(f"bank: concept {concept_id} variants disagree on {fld}")

    performance = sum(1 for r in records if r["pool"] == "performance")
    if performance < MIN_PERFORMANCE_ITEMS:
        errors.append(
            f"bank: {performance} performance items < {MIN_PERFORMANCE_ITEMS} "
            "(the Rust give-up gate could never pass)"
        )

    topics = {r["topic"] for r in records}
    if topics != set(TOPICS):
        errors.append(
            f"bank: topics not fully covered; missing {sorted(set(TOPICS) - topics)}"
        )

    titles = [r["title"] for r in records]
    duplicates = sorted({t for t in titles if titles.count(t) > 1})
    if duplicates:
        errors.append(f"bank: duplicate titles {duplicates}")

    for concept_id, similarity in variant_divergence(records).items():
        if similarity >= VARIANT_JACCARD_MAX:
            errors.append(
                f"bank: concept {concept_id} variants are too similar "
                f"(stem Jaccard {similarity} >= {VARIANT_JACCARD_MAX}); "
                "variant b must be a genuine rewording"
            )

    return errors


def tags_for_probe(record: dict) -> list[str]:
    """The note tags exactly as PROBE_SCHEMA.md 'Tagging' derives them."""
    return [
        PROBE_HELD_OUT_TAG,
        f"{POOL_TAG_PREFIX}{record['pool']}",
        f"{TOPIC_TAG_PREFIX}{record['topic']}",
        f"{CLUSTER_TAG_PREFIX}{record['cluster']}",
        f"{CONCEPT_TAG_PREFIX}{record['concept_id']}",
        f"{VARIANT_TAG_PREFIX}{record['variant']}",
    ]


def load_bank(path: str | Path) -> tuple[list[dict], list[str]]:
    """Records plus hard-fail messages ('file:line: problem')."""
    records: list[dict] = []
    failures: list[str] = []
    path = Path(path)
    if not path.exists():
        return [], [f"{path}: no such file"]
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                failures.append(f"{path}:{lineno}: invalid JSON: {exc}")
    return records, failures


# ---------------------------------------------------------------------------
# Leakage scan (challenge 7e) - both directions
# ---------------------------------------------------------------------------


def probe_scan_text(record: dict) -> str:
    """The learner-facing problem text the wall scans: stem + choices."""
    choices = record.get("choices") or {}
    return " ".join(
        [record.get("stem") or ""] + [choices.get(key) or "" for key in CHOICE_KEYS]
    )


def _generator_source_texts(speedrun_dir: Path) -> dict[str, str]:
    """Every text a probe must not overlap with: corpus passages, item
    stems/prompts/cloze text, and the aig prompt templates."""
    sources: dict[str, str] = {}
    for corpus_path in sorted(glob.glob(str(speedrun_dir / "corpus" / "*.md"))):
        sources[f"corpus/{Path(corpus_path).name}"] = Path(corpus_path).read_text(
            encoding="utf-8"
        )
    for items_path in sorted(glob.glob(str(speedrun_dir / "items" / "*.jsonl"))):
        pieces: list[str] = []
        with open(items_path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for fld in ("stem", "prompt", "cloze_text"):
                    value = item.get(fld)
                    if isinstance(value, str):
                        pieces.append(value)
        sources[f"items/{Path(items_path).name}"] = "\n".join(pieces)
    try:
        from aig import prompts as aig_prompts

        template_text = "\n".join(
            value
            for name, value in vars(aig_prompts).items()
            if name.isupper() and isinstance(value, str)
        )
        sources["aig/prompts.py"] = template_text
    except Exception as exc:  # pragma: no cover - import breakage is loud
        sources["aig/prompts.py"] = ""
        sources["_prompts_import_error"] = str(exc)
    return sources


def leakage_scan(
    records: list[dict],
    speedrun_dir: Path | None = None,
    sources: dict[str, str] | None = None,
    reference_pdf: str | Path | None = None,
) -> dict[str, Any]:
    """8-gram wall, both directions. `sources` overrides file discovery
    (used by the tests to inject fixtures)."""
    speedrun_dir = speedrun_dir or HERE
    if sources is None:
        sources = _generator_source_texts(speedrun_dir)
    if reference_pdf is None:
        reference_pdf = speedrun_dir / "reference" / "cfa_l1_official_sample_2025.pdf"

    wall_sources = {
        name: text for name, text in sources.items() if not name.startswith("_")
    }
    wall = LeakageWall(
        reference_pdf=reference_pdf, corpus_texts=wall_sources, n=NGRAM_N
    )
    forward_hits: list[dict[str, str]] = []
    for record in records:
        ok, reason = wall.check(probe_scan_text(record))
        if not ok:
            forward_hits.append(
                {
                    "probe": f"{record.get('concept_id')}{record.get('variant')}",
                    "title": record.get("title", ""),
                    "reason": reason,
                }
            )

    # Reverse direction: does any generator/corpus text quote a probe?
    # (The 8-gram intersection is symmetric; reporting it per source names
    # WHICH document would be quoting the bank.)
    probe_ngrams: dict[str, set[tuple[str, ...]]] = {}
    for record in records:
        key = f"{record.get('concept_id')}{record.get('variant')}"
        tokens = tokenize(probe_scan_text(record))
        probe_ngrams[key] = {
            tuple(tokens[i : i + NGRAM_N]) for i in range(len(tokens) - NGRAM_N + 1)
        }
    reverse_hits: list[dict[str, str]] = []
    for source_name, text in sorted(sources.items()):
        if source_name.startswith("_"):
            continue
        tokens = tokenize(text)
        source_grams = {
            tuple(tokens[i : i + NGRAM_N]) for i in range(len(tokens) - NGRAM_N + 1)
        }
        for probe_key, grams in sorted(probe_ngrams.items()):
            overlap = source_grams & grams
            if overlap:
                reverse_hits.append(
                    {
                        "source": source_name,
                        "probe": probe_key,
                        "ngram": " ".join(next(iter(overlap))),
                    }
                )

    return {
        "passed": not forward_hits and not reverse_hits,
        "ngram_n": NGRAM_N,
        "probes_scanned": len(records),
        "sources_scanned": sorted(name for name in sources if not name.startswith("_")),
        "reference_pdf_available": wall.reference_available,
        "forward_hits": forward_hits,
        "reverse_hits": reverse_hits,
    }


# ---------------------------------------------------------------------------
# Outcome extraction: the delay rule (mirrors readiness/probes.rs)
# ---------------------------------------------------------------------------


@dataclass
class ProbeObs:
    """One probe card as observed in (or synthesized for) a collection."""

    key: str  # e.g. "c01a"
    pool: str
    topic: str
    cluster: str
    #: graded answers only (ease > 0), ordered ascending: (ms, ease)
    answers: list[tuple[int, int]] = field(default_factory=list)


def first_graded_answer(obs: ProbeObs) -> tuple[int, bool] | None:
    """(answered_at_ms, correct); Again(1)=wrong, Hard/Good/Easy=correct."""
    for ms, ease in obs.answers:
        if ease > 0:
            return ms, ease >= 2
    return None


def compute_outcomes(
    probes: list[ProbeObs], study_times_ms: dict[str, list[int]]
) -> dict[str, Any]:
    """Per-probe outcome rows + per-pool summaries, using the exact rule:
    first graded answer; delayed iff >= 7 days after the last graded
    NON-probe review of the probe's cluster strictly before the answer;
    never-studied clusters count as delayed (no lag claimed).
    """
    rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {
        pool: {
            "items": 0,
            "correct": 0,
            "delayed": 0,
            "undelayed": 0,
            "unanswered": 0,
            "never_studied": 0,
            "lag_days": [],  # measured lags of delayed outcomes only
            "undelayed_lag_days": [],
        }
        for pool in POOLS
    }
    for obs in sorted(probes, key=lambda p: p.key):
        pool = summary[obs.pool]
        pool["items"] += 1
        answer = first_graded_answer(obs)
        row: dict[str, Any] = {
            "probe": obs.key,
            "pool": obs.pool,
            "cluster": obs.cluster,
        }
        if answer is None:
            pool["unanswered"] += 1
            row["status"] = "unanswered"
            rows.append(row)
            continue
        answered_at, correct = answer
        times = study_times_ms.get(obs.cluster) or []
        index = bisect.bisect_left(times, answered_at)
        last_study = times[index - 1] if index > 0 else None
        if last_study is None:
            pool["delayed"] += 1
            pool["never_studied"] += 1
            if correct:
                pool["correct"] += 1
            row.update(status="delayed", correct=correct, never_studied=True)
        else:
            lag_days = (answered_at - last_study) / MS_PER_DAY
            row["lag_days"] = round(lag_days, 2)
            if lag_days >= MIN_PROBE_DELAY_DAYS:
                pool["delayed"] += 1
                pool["lag_days"].append(round(lag_days, 2))
                if correct:
                    pool["correct"] += 1
                row.update(status="delayed", correct=correct, never_studied=False)
            else:
                pool["undelayed"] += 1
                pool["undelayed_lag_days"].append(round(lag_days, 2))
                row.update(status="undelayed", correct=correct)
        rows.append(row)

    for pool in summary.values():
        lags = pool.pop("lag_days")
        pool["lag_distribution"] = _lag_stats(lags)
        pool["undelayed_lags"] = sorted(pool.pop("undelayed_lag_days"))
    return {"rows": rows, "pools": summary}


def _lag_stats(lags: list[float]) -> dict[str, Any]:
    """Honest lag reporting: only lags actually measured (never-studied
    probes contribute nothing here)."""
    if not lags:
        return {"n": 0}
    ordered = sorted(lags)
    mid = len(ordered) // 2
    median = (
        ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    )
    return {
        "n": len(ordered),
        "min": round(ordered[0], 2),
        "median": round(median, 2),
        "mean": round(sum(ordered) / len(ordered), 2),
        "max": round(ordered[-1], 2),
    }


def readiness_inputs(outcomes: dict[str, Any]) -> dict[str, int]:
    """The (x, n) pair the Rust Beta-Binomial estimate consumes."""
    performance = outcomes["pools"]["performance"]
    return {"x_correct": performance["correct"], "n_delayed": performance["delayed"]}


# ---------------------------------------------------------------------------
# Bridge proof (challenge 7d): retention vs delayed-probe accuracy
# ---------------------------------------------------------------------------


def bridge_proof(
    outcomes: dict[str, Any],
    study_reviews: dict[str, list[tuple[int, int]]],
    probe_clusters: set[str],
    window_days: int = RETENTION_WINDOW_DAYS,
) -> dict[str, Any]:
    """Memory-vs-performance gap: retention accuracy on the source
    clusters' NON-probe cards (fraction of graded reviews with ease >= 2 in
    the trailing `window_days` ending at the newest such review) minus
    delayed performance-pool probe accuracy. Abstains from a gap when
    either side has no data."""
    relevant = [
        (ms, ease)
        for cluster in sorted(probe_clusters)
        for (ms, ease) in study_reviews.get(cluster, [])
        if ease > 0
    ]
    retention: dict[str, Any] = {
        "window_days": window_days,
        "n_reviews": 0,
        "accuracy": None,
    }
    if relevant:
        newest = max(ms for ms, _ in relevant)
        cutoff = newest - window_days * MS_PER_DAY
        windowed = [(ms, ease) for ms, ease in relevant if ms >= cutoff]
        retention["n_reviews"] = len(windowed)
        retention["accuracy"] = round(
            sum(1 for _, ease in windowed if ease >= 2) / len(windowed), 4
        )

    performance = outcomes["pools"]["performance"]
    calibration = outcomes["pools"]["calibration"]
    probe_acc = (
        round(performance["correct"] / performance["delayed"], 4)
        if performance["delayed"]
        else None
    )
    all_delayed = performance["delayed"] + calibration["delayed"]
    all_correct = performance["correct"] + calibration["correct"]
    gap = (
        round(retention["accuracy"] - probe_acc, 4)
        if retention["accuracy"] is not None and probe_acc is not None
        else None
    )
    return {
        "retention": retention,
        "delayed_probe_accuracy": {
            "performance_pool": {
                "accuracy": probe_acc,
                "n": performance["delayed"],
            },
            "both_pools": {
                "accuracy": round(all_correct / all_delayed, 4)
                if all_delayed
                else None,
                "n": all_delayed,
            },
        },
        "memory_minus_performance_gap": gap,
        "note": (
            "gap = trailing-window retention accuracy on the probes' source "
            "clusters (non-probe cards) minus delayed performance-pool probe "
            "accuracy; positive = recognition memory outruns delayed "
            "application, the transfer gap the probes exist to measure"
        ),
    }


# ---------------------------------------------------------------------------
# Calibration: proxy predictions, Brier/log-loss, temperature scaling
# ---------------------------------------------------------------------------


def predict_p_correct(
    cluster: str,
    answered_at_ms: int,
    study_reviews: dict[str, list[tuple[int, int]]],
    window_reviews: int = PREDICTION_WINDOW_REVIEWS,
) -> float:
    """The documented proxy: accuracy of the most recent <= 20 graded
    non-probe reviews of the probe's cluster strictly before the answer,
    add-one (Laplace) smoothed; 1/3 for never-studied clusters."""
    history = [
        ease
        for ms, ease in study_reviews.get(cluster, [])
        if ease > 0 and ms < answered_at_ms
    ]
    if not history:
        return NEVER_STUDIED_PREDICTION
    recent = history[-window_reviews:]
    correct = sum(1 for ease in recent if ease >= 2)
    return (correct + 1) / (len(recent) + 2)


def brier_score(pairs: list[tuple[float, bool]]) -> float:
    return sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, bool]]) -> float:
    total = 0.0
    for p, y in pairs:
        p = min(max(p, 1e-6), 1.0 - 1e-6)
        total += -math.log(p if y else 1.0 - p)
    return total / len(pairs)


def apply_temperature(p: float, temperature: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    logit = math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-logit / temperature))


def fit_temperature(
    pairs: list[tuple[float, bool]],
    low: float = 0.05,
    high: float = 20.0,
    iterations: int = 200,
) -> float:
    """Single-scalar temperature minimizing log-loss, golden-section search
    on ln T (deterministic; unimodal in practice for one parameter)."""

    def loss(temperature: float) -> float:
        return log_loss([(apply_temperature(p, temperature), y) for p, y in pairs])

    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = math.log(low), math.log(high)
    c = b - ratio * (b - a)
    d = a + ratio * (b - a)
    loss_c, loss_d = loss(math.exp(c)), loss(math.exp(d))
    for _ in range(iterations):
        if b - a < 1e-9:
            break
        if loss_c < loss_d:
            b, d, loss_d = d, c, loss_c
            c = b - ratio * (b - a)
            loss_c = loss(math.exp(c))
        else:
            a, c, loss_c = c, d, loss_d
            d = a + ratio * (b - a)
            loss_d = loss(math.exp(d))
    best = math.exp((a + b) / 2.0)
    # never return a fit worse than the identity
    return best if loss(best) <= loss(1.0) else 1.0


def build_calibration_record(
    pairs: list[tuple[float, bool]], fitted_at: str
) -> dict[str, Any]:
    """The exact snake_case shape rslib's CalibrationRecord deserializes.
    Brier/log-loss are the post-temperature (calibrated) scores; the raw
    before-scores travel in the report, not the record."""
    temperature = fit_temperature(pairs)
    calibrated = [(apply_temperature(p, temperature), y) for p, y in pairs]
    return {
        "fitted_at": fitted_at,
        "brier": round(brier_score(calibrated), 6),
        "log_loss": round(log_loss(calibrated), 6),
        "n": len(pairs),
        "temperature": round(temperature, 6),
    }


def calibrate(
    probes: list[ProbeObs],
    outcomes: dict[str, Any],
    study_reviews: dict[str, list[tuple[int, int]]],
    fitted_at: str,
) -> dict[str, Any]:
    """Calibration on the calibration pool ONLY, delayed outcomes only.
    Abstains (refuses to fit) below MIN_CALIBRATION_OUTCOMES."""
    status_by_key = {
        row["probe"]: row for row in outcomes["rows"] if row["pool"] == "calibration"
    }
    pairs: list[tuple[float, bool]] = []
    per_probe: list[dict[str, Any]] = []
    for obs in sorted(probes, key=lambda p: p.key):
        if obs.pool != "calibration":
            continue
        row = status_by_key.get(obs.key)
        if not row or row["status"] != "delayed":
            continue
        answer = first_graded_answer(obs)
        assert answer is not None  # delayed implies answered
        predicted = predict_p_correct(obs.cluster, answer[0], study_reviews)
        pairs.append((predicted, row["correct"]))
        per_probe.append(
            {
                "probe": obs.key,
                "cluster": obs.cluster,
                "predicted": round(predicted, 4),
                "correct": row["correct"],
                "never_studied": row.get("never_studied", False),
            }
        )

    result: dict[str, Any] = {
        "pool": "calibration",
        "outcomes_used": len(pairs),
        "min_outcomes": MIN_CALIBRATION_OUTCOMES,
        "proxy": (
            f"accuracy of the most recent <= {PREDICTION_WINDOW_REVIEWS} graded "
            "non-probe reviews of the probe's cluster before its first answer, "
            "add-one smoothed; never-studied clusters predict 1/3 (3-choice "
            "chance rate)"
        ),
        "per_probe": per_probe,
    }
    if len(pairs) < MIN_CALIBRATION_OUTCOMES:
        result["abstained"] = True
        result["message"] = (
            f"REFUSING to fit calibration: only {len(pairs)} delayed "
            f"calibration-pool outcomes; need >= {MIN_CALIBRATION_OUTCOMES}. "
            "No record is written - abstention is the honest output."
        )
        return result

    record = build_calibration_record(pairs, fitted_at)
    calibrated = [(apply_temperature(p, record["temperature"]), y) for p, y in pairs]
    result.update(
        abstained=False,
        raw={
            "brier": round(brier_score(pairs), 6),
            "log_loss": round(log_loss(pairs), 6),
        },
        calibrated={
            "brier": round(brier_score(calibrated), 6),
            "log_loss": round(log_loss(calibrated), 6),
        },
        temperature=record["temperature"],
        record=record,
        note=(
            "record carries the post-temperature scores; with one fitted "
            "scalar on a small n they are mildly optimistic in-sample "
            "(disclosed; see PROBE_SCHEMA.md)"
        ),
    )
    return result


# ---------------------------------------------------------------------------
# Reading a real collection (sqlite3, read-only; mirrors aig/confusability)
# ---------------------------------------------------------------------------


def read_collection(path: str | Path) -> dict[str, Any]:
    """Probe cards + per-cluster non-probe study reviews from an Anki
    collection DB, opened read-only. Probe identity comes from note tags,
    exactly like probes.rs (search on probe::held_out)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        note_tags: dict[int, list[str]] = {}
        for nid, tags in con.execute("select id, tags from notes"):
            note_tags[nid] = [t.lower() for t in str(tags).split()]
        card_note = dict(con.execute("select id, nid from cards"))
        revlog: dict[int, list[tuple[int, int]]] = {}
        for rid, cid, ease in con.execute(
            "select id, cid, ease from revlog order by id"
        ):
            revlog.setdefault(cid, []).append((rid, ease))

        def tag_value(tags: list[str], prefix: str) -> str | None:
            for tag in tags:
                if tag.startswith(prefix) and len(tag) > len(prefix):
                    return tag[len(prefix) :]
            return None

        probes: list[ProbeObs] = []
        probe_note_ids: set[int] = set()
        for cid, nid in card_note.items():
            tags = note_tags.get(nid, [])
            if PROBE_HELD_OUT_TAG not in tags:
                continue
            probe_note_ids.add(nid)
            pool = tag_value(tags, POOL_TAG_PREFIX)
            if pool not in POOLS:
                continue  # held-out but unpooled: ignore rather than guess
            concept = tag_value(tags, CONCEPT_TAG_PREFIX) or f"card{cid}"
            variant = tag_value(tags, VARIANT_TAG_PREFIX) or ""
            probes.append(
                ProbeObs(
                    key=f"{concept}{variant}",
                    pool=pool,
                    topic=tag_value(tags, TOPIC_TAG_PREFIX) or "",
                    cluster=tag_value(tags, CLUSTER_TAG_PREFIX) or "",
                    answers=[
                        (ms, ease) for ms, ease in revlog.get(cid, []) if ease > 0
                    ],
                )
            )

        study_reviews: dict[str, list[tuple[int, int]]] = {}
        for cid, nid in card_note.items():
            if nid in probe_note_ids:
                continue  # probe answers are never study touches
            tags = note_tags.get(nid, [])
            cluster = tag_value(tags, CLUSTER_TAG_PREFIX)
            if cluster is None:
                continue
            for ms, ease in revlog.get(cid, []):
                if ease > 0:
                    study_reviews.setdefault(cluster, []).append((ms, ease))
        for reviews in study_reviews.values():
            reviews.sort()

        return {"probes": probes, "study_reviews": study_reviews}
    finally:
        con.close()


def analyze_collection(path: str | Path, fitted_at: str) -> dict[str, Any]:
    data = read_collection(path)
    probes: list[ProbeObs] = data["probes"]
    study_reviews: dict[str, list[tuple[int, int]]] = data["study_reviews"]
    study_times = {
        cluster: [ms for ms, _ in reviews] for cluster, reviews in study_reviews.items()
    }
    outcomes = compute_outcomes(probes, study_times)
    probe_clusters = {obs.cluster for obs in probes if obs.cluster}
    return {
        "path": str(path),
        "probe_cards": len(probes),
        "outcomes": outcomes,
        "readiness_inputs": readiness_inputs(outcomes),
        "bridge": bridge_proof(outcomes, study_reviews, probe_clusters),
        "calibration": calibrate(probes, outcomes, study_reviews, fitted_at),
    }


# ---------------------------------------------------------------------------
# --apply: write the config record via pylib (lazy import), then verify
# ---------------------------------------------------------------------------


def apply_calibration(collection_path: str, record: dict[str, Any]) -> dict[str, Any]:
    """Write speedrun:readinessCalibration via pylib and read it back."""
    from anki.collection import Collection  # deferred: pylib optional

    col = Collection(collection_path)
    try:
        col.set_config(CALIBRATION_CONFIG_KEY, record)
        readback = col.get_config(CALIBRATION_CONFIG_KEY)
    finally:
        col.close()
    if readback != record:
        raise RuntimeError(
            f"config verify failed: wrote {record!r}, read back {readback!r}"
        )
    return {"key": CALIBRATION_CONFIG_KEY, "written": record, "verified": True}


# ---------------------------------------------------------------------------
# --self-test: fully synthetic, deterministic, no collection, no pylib
# ---------------------------------------------------------------------------


def _known_answer_checks() -> list[str]:
    """Hand-computed fixtures; raises AssertionError on any regression."""
    passed: list[str] = []

    pairs = [(0.8, True), (0.4, False)]
    assert abs(brier_score(pairs) - 0.10) < 1e-12
    expected = -(math.log(0.8) + math.log(0.6)) / 2.0
    assert abs(log_loss(pairs) - expected) < 1e-12
    passed.append("brier/log-loss match hand-computed values")

    assert apply_temperature(0.5, 3.0) == 0.5  # T never moves 0.5
    overconfident = [
        (0.9, True),
        (0.9, False),
        (0.9, True),
        (0.9, False),
        (0.1, True),
        (0.1, False),
    ]
    temperature = fit_temperature(overconfident)
    assert temperature > 5.0, temperature  # 50% accuracy -> push toward 0.5
    assert log_loss(
        [(apply_temperature(p, temperature), y) for p, y in overconfident]
    ) <= log_loss(overconfident)
    passed.append("temperature softens overconfident predictions")

    identity_pairs = [(0.8, True), (0.8, True), (0.8, True), (0.8, False), (0.2, False)]
    fitted = fit_temperature(identity_pairs)
    assert (
        log_loss([(apply_temperature(p, fitted), y) for p, y in identity_pairs])
        <= log_loss(identity_pairs) + 1e-12
    )
    passed.append("temperature fit never worsens log-loss")

    # delay-rule micro-fixture: lag 9 delayed / lag 2 undelayed / exactly
    # 7.0 delayed / never-studied delayed / unanswered / first answer wins
    day = MS_PER_DAY
    study = {"fi::duration": [10 * day]}
    micro = [
        ProbeObs("p1", "performance", "t", "fi::duration", [(19 * day, 3)]),
        ProbeObs("p2", "performance", "t", "fi::duration", [(12 * day, 1)]),
        ProbeObs("p3", "performance", "t", "fi::duration", [(17 * day, 3)]),
        ProbeObs("p4", "performance", "t", "qm::tvm", [(3 * day, 1)]),
        ProbeObs("p5", "performance", "t", "fi::duration", []),
        ProbeObs("p6", "performance", "t", "qm::tvm", [(2 * day, 1), (30 * day, 3)]),
    ]
    outcome = compute_outcomes(micro, study)
    pool = outcome["pools"]["performance"]
    assert pool["delayed"] == 4  # p1, p3 (exactly 7d), p4, p6
    assert pool["undelayed"] == 1  # p2
    assert pool["unanswered"] == 1  # p5
    assert pool["never_studied"] == 2  # p4, p6
    assert pool["correct"] == 2  # p1, p3 (p6's FIRST answer was wrong)
    assert outcome["rows"][0]["lag_days"] == 9.0
    passed.append("delay rule: 7-day boundary, never-studied, first-answer-only")

    # calibration abstention below the floor
    thin = calibrate(
        micro[:1],
        compute_outcomes(micro[:1], study),
        {"fi::duration": [(10 * day, 3)]},
        SELF_TEST_DATE,
    )
    assert thin["abstained"] is True and "REFUSING" in thin["message"]
    passed.append("calibration abstains below 10 outcomes")

    return passed


def synthesize_collection(
    records: list[dict], seed: int = SELF_TEST_SEED
) -> tuple[list[ProbeObs], dict[str, list[tuple[int, int]]]]:
    """A seeded fake revlog over the REAL bank: every studied cluster gets
    30 days of study reviews with a per-cluster skill; performance probes
    are all answered delayed; the calibration pool exercises the
    unanswered/undelayed branches too; never-studied clusters answer near
    the 1/3 chance rate."""
    rng = random.Random(seed)
    base = 1_750_000_000_000  # fixed epoch: determinism, no wall clock
    day = MS_PER_DAY

    clusters = sorted({record["cluster"] for record in records})
    never_studied = set(clusters[:5])  # deterministic slice
    skill: dict[str, float] = {}
    study_reviews: dict[str, list[tuple[int, int]]] = {}
    for cluster in clusters:
        skill[cluster] = 0.60 + 0.35 * rng.random()
        if cluster in never_studied:
            continue
        reviews: list[tuple[int, int]] = []
        for review_index in range(30):
            at = base + (review_index % 30) * day + rng.randrange(0, day // 2)
            correct = rng.random() < skill[cluster]
            reviews.append((at, 3 if correct else 1))
        study_reviews[cluster] = sorted(reviews)

    last_study = {
        cluster: max(ms for ms, _ in reviews)
        for cluster, reviews in study_reviews.items()
    }
    probes: list[ProbeObs] = []
    calibration_index = 0
    for record in sorted(records, key=lambda r: (r["concept_id"], r["variant"])):
        cluster = record["cluster"]
        key = f"{record['concept_id']}{record['variant']}"
        obs = ProbeObs(key, record["pool"], record["topic"], cluster)
        studied = cluster not in never_studied
        # delayed answer time: >= 7d after the cluster's last study touch
        if studied:
            answer_at = last_study[cluster] + int((8 + (hash(key) % 5)) * day)
        else:
            answer_at = base + 45 * day
        if record["pool"] == "calibration":
            calibration_index += 1
            if calibration_index in (3, 11):
                probes.append(obs)  # unanswered
                continue
            if calibration_index in (6, 14) and studied:
                answer_at = last_study[cluster] + 2 * day  # undelayed
        # delayed performance sits below study accuracy (the transfer gap
        # the bridge proof reports); calibration-pool outcomes are drawn AT
        # the study-accuracy rate the proxy predicts, so the temperature
        # fit demonstrates a non-degenerate scalar instead of saturating
        gap = 0.15 if record["pool"] == "performance" else 0.0
        p_correct = (skill[cluster] - gap) if studied else 0.34
        correct = rng.random() < p_correct
        obs.answers.append((answer_at, 3 if correct else 1))
        if rng.random() < 0.25:  # later practice must never count
            obs.answers.append((answer_at + 3 * day, 1 if correct else 3))
        probes.append(obs)
    return probes, study_reviews


def run_self_test(records: list[dict], seed: int = SELF_TEST_SEED) -> dict[str, Any]:
    """Deterministic end-to-end run over synthetic data; raises on failure."""
    checks = _known_answer_checks()

    probes, study_reviews = synthesize_collection(records, seed)
    study_times = {
        cluster: [ms for ms, _ in reviews] for cluster, reviews in study_reviews.items()
    }
    outcomes = compute_outcomes(probes, study_times)
    performance = outcomes["pools"]["performance"]
    calibration = outcomes["pools"]["calibration"]

    assert performance["items"] == MIN_PERFORMANCE_ITEMS
    assert performance["delayed"] == MIN_PERFORMANCE_ITEMS, (
        "all performance probes are delayed by construction, demonstrating "
        "the >=50 gate is satisfiable"
    )
    assert calibration["unanswered"] == 2
    assert calibration["undelayed"] >= 1
    assert (
        calibration["delayed"] + calibration["undelayed"] + calibration["unanswered"]
        == calibration["items"]
    )
    checks.append("synthetic outcome partition adds up")

    probe_clusters = {obs.cluster for obs in probes}
    bridge = bridge_proof(outcomes, study_reviews, probe_clusters)
    assert bridge["retention"]["accuracy"] is not None
    assert bridge["memory_minus_performance_gap"] is not None
    checks.append("bridge proof produced a measured gap")

    calibration_result = calibrate(probes, outcomes, study_reviews, SELF_TEST_DATE)
    assert calibration_result["abstained"] is False
    assert calibration_result["outcomes_used"] >= MIN_CALIBRATION_OUTCOMES
    record = calibration_result["record"]
    assert list(record) == ["fitted_at", "brier", "log_loss", "n", "temperature"]
    assert (
        calibration_result["calibrated"]["log_loss"]
        <= calibration_result["raw"]["log_loss"] + 1e-9
    )
    checks.append("calibration fit on synthetic outcomes, record shape exact")

    return {
        "seed": seed,
        "checks_passed": checks,
        "outcomes": {"pools": outcomes["pools"]},
        "readiness_inputs": readiness_inputs(outcomes),
        "bridge": bridge,
        "calibration": {
            key: value
            for key, value in calibration_result.items()
            if key != "per_probe"
        },
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Probe harness report",
        "",
        f"Generated: {report['meta']['generated_at']} — modes: "
        f"{', '.join(report['meta']['modes'])}",
        "",
        "## Bank validation",
        "",
    ]
    validation = report["validation"]
    if validation["passed"]:
        divergence = validation["variant_divergence"]
        lines += [
            f"PASS — {validation['record_count']} records, "
            f"{validation['concepts']} concepts x 2 variants, "
            f"{validation['performance_items']} performance / "
            f"{validation['calibration_items']} calibration (concept-disjoint), "
            f"all {len(validation['topics_covered'])} topics covered.",
            "",
            f"Variant divergence: max stem Jaccard "
            f"{divergence['max_jaccard']} (concept {divergence['worst_concept']}) "
            f"< {divergence['threshold']} threshold.",
        ]
    else:
        lines.append(f"FAIL — {len(validation['errors'])} error(s):")
        lines += [f"- {error}" for error in validation["errors"]]
    lines.append("")

    leakage = report.get("leakage")
    if leakage:
        lines += ["## Leakage scan (8-gram wall, both directions)", ""]
        status = "CLEAN" if leakage["passed"] else "HITS FOUND"
        reference = (
            "reference PDF included"
            if leakage["reference_pdf_available"]
            else "reference PDF not present (corpus/items/prompts only)"
        )
        lines.append(
            f"{status} — {leakage['probes_scanned']} probes vs "
            f"{len(leakage['sources_scanned'])} sources ({reference})."
        )
        for hit in leakage["forward_hits"]:
            lines.append(f"- FORWARD {hit['probe']}: {hit['reason']}")
        for hit in leakage["reverse_hits"]:
            lines.append(
                f"- REVERSE {hit['source']} quotes {hit['probe']}: {hit['ngram']!r}"
            )
        lines.append("")

    self_test = report.get("self_test")
    if self_test:
        lines += ["## Self-test (synthetic, seeded)", ""]
        inputs = self_test["readiness_inputs"]
        gap = self_test["bridge"]["memory_minus_performance_gap"]
        calibration = self_test["calibration"]
        lines += [
            f"Seed {self_test['seed']}; {len(self_test['checks_passed'])} "
            "internal checks passed.",
            f"- readiness inputs: x={inputs['x_correct']} of "
            f"n={inputs['n_delayed']} delayed performance outcomes",
            f"- bridge gap (memory − delayed performance): {gap}",
            f"- calibration: n={calibration['outcomes_used']}, raw log-loss "
            f"{calibration['raw']['log_loss']} → calibrated "
            f"{calibration['calibrated']['log_loss']} at T="
            f"{calibration['temperature']}",
            "",
        ]

    collection = report.get("collection")
    if collection:
        lines += ["## Collection analysis", ""]
        inputs = collection["readiness_inputs"]
        pools = collection["outcomes"]["pools"]
        lines += [
            f"Path: `{collection['path']}` — {collection['probe_cards']} "
            "probe cards found.",
            f"- readiness inputs: x={inputs['x_correct']} of "
            f"n={inputs['n_delayed']} delayed performance outcomes",
        ]
        for pool_name in POOLS:
            pool = pools[pool_name]
            lines.append(
                f"- {pool_name}: {pool['items']} items — {pool['delayed']} "
                f"delayed ({pool['never_studied']} never-studied), "
                f"{pool['undelayed']} undelayed (excluded), "
                f"{pool['unanswered']} unanswered; lag "
                f"{pool['lag_distribution']}"
            )
        bridge = collection["bridge"]
        lines.append(
            f"- bridge: retention {bridge['retention']['accuracy']} "
            f"(n={bridge['retention']['n_reviews']}) vs delayed probes "
            f"{bridge['delayed_probe_accuracy']['performance_pool']['accuracy']} "
            f"(n={bridge['delayed_probe_accuracy']['performance_pool']['n']}) "
            f"→ gap {bridge['memory_minus_performance_gap']}"
        )
        calibration = collection["calibration"]
        if calibration.get("abstained"):
            lines.append(f"- calibration: {calibration['message']}")
        else:
            lines.append(
                f"- calibration: n={calibration['outcomes_used']}, raw "
                f"Brier/log-loss {calibration['raw']['brier']}/"
                f"{calibration['raw']['log_loss']} → calibrated "
                f"{calibration['calibrated']['brier']}/"
                f"{calibration['calibrated']['log_loss']} at T="
                f"{calibration['temperature']}"
            )
        apply_result = report.get("apply")
        if apply_result:
            lines.append(
                f"- applied: wrote `{apply_result['key']}` = "
                f"`{json.dumps(apply_result['written'])}` (verified by "
                "read-back)"
            )
        lines.append("")

    lines += [
        "## Honesty notes",
        "",
        "- Outcomes use the FIRST graded answer per probe card; delayed "
        f"means >= {int(MIN_PROBE_DELAY_DAYS)} days after the cluster's last "
        "non-probe study touch; never-studied clusters count as delayed and "
        "carry no fabricated lag.",
        "- Only the performance pool feeds Readiness; calibration is fit on "
        "the disjoint calibration pool (no circularity).",
        "- Calibration abstains below "
        f"{MIN_CALIBRATION_OUTCOMES} delayed outcomes; the record's scores "
        "are in-sample post-temperature (disclosed).",
        "",
    ]
    return "\n".join(lines)


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "probe_harness_report.json"
    md_path = report_dir / "probe_harness_report.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--items",
        default=str(HERE / "probes" / "probe_bank.jsonl"),
        help="probe bank JSONL (default: %(default)s)",
    )
    parser.add_argument(
        "--leakage-scan",
        action="store_true",
        help="run the 8-gram leakage wall (always on; flag kept for clarity)",
    )
    parser.add_argument(
        "--collection",
        help="Anki collection (.anki2) to read outcomes from, read-only",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --collection: write speedrun:readinessCalibration via pylib",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the synthetic end-to-end self-test (default when no "
        "--collection is given)",
    )
    parser.add_argument(
        "--report-dir",
        default=str(HERE / "eval"),
        help="where to write probe_harness_report.{json,md} (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    if args.apply and not args.collection:
        parser.error("--apply requires --collection")

    run_self = args.self_test or not args.collection
    failures: list[str] = []
    modes = (
        ["validation", "leakage-scan"]
        + (["self-test"] if run_self else [])
        + (["collection"] if args.collection else [])
        + (["apply"] if args.apply else [])
    )
    report: dict[str, Any] = {
        "meta": {
            "tool": "probe_harness",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "bank": args.items,
            "modes": modes,
            "delay_rule": (
                f"first graded answer; delayed iff >= {MIN_PROBE_DELAY_DAYS} days "
                "after the last graded non-probe review of the probe's cluster; "
                "never-studied clusters count as delayed"
            ),
        }
    }

    # ---- validation (always) ----
    records, load_failures = load_bank(args.items)
    errors = load_failures + validate_bank(records)
    divergence = variant_divergence(records) if not errors else {}
    worst_concept = max(divergence, key=divergence.get) if divergence else None
    report["validation"] = {
        "passed": not errors,
        "record_count": len(records),
        "concepts": len({r.get("concept_id") for r in records}),
        "performance_items": sum(1 for r in records if r.get("pool") == "performance"),
        "calibration_items": sum(1 for r in records if r.get("pool") == "calibration"),
        "topics_covered": sorted({r.get("topic") for r in records if r.get("topic")}),
        "errors": errors,
        "variant_divergence": {
            "threshold": VARIANT_JACCARD_MAX,
            "max_jaccard": max(divergence.values()) if divergence else None,
            "worst_concept": worst_concept,
            "per_concept": divergence,
        },
    }
    if errors:
        failures.append(f"validation: {len(errors)} error(s)")
        print(f"VALIDATION FAILED ({len(errors)} errors):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    else:
        print(
            f"validation: OK ({len(records)} records, "
            f"{report['validation']['performance_items']} performance / "
            f"{report['validation']['calibration_items']} calibration; max "
            f"variant Jaccard {report['validation']['variant_divergence']['max_jaccard']})"
        )

    # ---- leakage scan (always; requires parseable records) ----
    scannable = [r for r in records if isinstance(r, dict) and r.get("stem")]
    leak = leakage_scan(scannable, HERE)
    report["leakage"] = leak
    if leak["passed"]:
        print(
            f"leakage scan: CLEAN ({leak['probes_scanned']} probes x "
            f"{len(leak['sources_scanned'])} sources, n={leak['ngram_n']}; "
            f"reference PDF {'included' if leak['reference_pdf_available'] else 'absent'})"
        )
    else:
        failures.append(
            f"leakage: {len(leak['forward_hits'])} forward / "
            f"{len(leak['reverse_hits'])} reverse hit(s)"
        )
        for hit in leak["forward_hits"]:
            print(f"LEAKAGE {hit['probe']}: {hit['reason']}", file=sys.stderr)
        for hit in leak["reverse_hits"]:
            print(
                f"LEAKAGE (reverse) {hit['source']} quotes {hit['probe']}: "
                f"{hit['ngram']!r}",
                file=sys.stderr,
            )

    # ---- self-test ----
    if run_self and not errors:
        try:
            report["self_test"] = run_self_test(records)
            inputs = report["self_test"]["readiness_inputs"]
            print(
                f"self-test: OK (seed {SELF_TEST_SEED}; "
                f"{len(report['self_test']['checks_passed'])} checks; "
                f"synthetic x={inputs['x_correct']}/n={inputs['n_delayed']}; "
                f"calibration T="
                f"{report['self_test']['calibration']['temperature']})"
            )
        except AssertionError as exc:
            failures.append(f"self-test: {exc}")
            report["self_test"] = {"failed": str(exc)}
            print(f"SELF-TEST FAILED: {exc}", file=sys.stderr)
    elif run_self:
        print("self-test: skipped (bank invalid)", file=sys.stderr)

    # ---- real collection ----
    if args.collection and not errors:
        fitted_at = datetime.date.today().isoformat()
        collection_report = analyze_collection(args.collection, fitted_at)
        report["collection"] = collection_report
        inputs = collection_report["readiness_inputs"]
        print(
            f"collection: {collection_report['probe_cards']} probe cards; "
            f"readiness inputs x={inputs['x_correct']} n={inputs['n_delayed']}; "
            f"bridge gap {collection_report['bridge']['memory_minus_performance_gap']}"
        )
        calibration = collection_report["calibration"]
        if calibration.get("abstained"):
            print(calibration["message"])
            if args.apply:
                failures.append("apply refused: calibration abstained")
        elif args.apply:
            report["apply"] = apply_calibration(args.collection, calibration["record"])
            print(
                f"applied {CALIBRATION_CONFIG_KEY} = "
                f"{json.dumps(report['apply']['written'])} (verified)"
            )
    elif args.collection:
        print("collection analysis: skipped (bank invalid)", file=sys.stderr)

    report["failures"] = failures
    report["exit_code"] = 1 if failures else 0
    json_path, md_path = write_reports(report, Path(args.report_dir))
    print(f"reports: {json_path} + {md_path.name}")
    if failures:
        print("FAILURES: " + "; ".join(failures), file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
