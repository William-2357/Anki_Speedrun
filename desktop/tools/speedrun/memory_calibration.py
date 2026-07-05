# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Held-out calibration of the FSRS memory model (Anki Speedrun, Phase 3).

MODEL_DESCRIPTIONS.md section "Memory" claims per-card FSRS retrievability
is the recall estimator but states its calibration check is missing. This
tool fills that gap: it proves (or disproves) on HELD-BACK reviews that the
engine's predicted recall probabilities match observed recall, reporting
Brier score (with a seeded-bootstrap 95% CI), log-loss, a 10-bin
reliability table/chart, ECE, and two context baselines.

Design: the ENGINE does the memory math (no formula drift). We build a
truncated sqlite copy of the collection (all revlog rows at/after the
cutoff deleted; FSRS state stripped from cards.data), then ask pylib's
`Collection.compute_memory_state(card_id)` — which recomputes stability/
difficulty/decay from the surviving revlog via rslib
(rslib/src/scheduler/fsrs/memory_state.rs `compute_memory_state`, line
~360: it rebuilds the FSRS item from revlog and never reads stored card
state) — for each held-out card. Predicted recall for the card's FIRST
post-cutoff review is then the engine-defined forgetting curve at the
elapsed time since its last pre-cutoff graded review.

Retrievability formula (verified against the vendored engine source):

    R(t) = (1 + factor * t / S) ** (-decay),  factor = 0.9 ** (-1/decay) - 1

* fsrs crate 5.2.0, src/inference.rs:60-63 (`current_retrievability`), the
  same power curve as src/model.rs:52-56 (`power_forgetting_curve`).
* Elapsed time in FRACTIONAL DAYS = seconds / 86_400: exactly what the
  crate's `current_retrievability_seconds` does (src/inference.rs:512-519)
  and what rslib's own stats/browser retrievability display uses
  (rslib/src/stats/graphs/retrievability.rs:33-34). Note the engine TRAINS
  on rollover-aware integer day counts (rslib/src/scheduler/fsrs/params.rs
  `days_elapsed`), so using revlog-to-revlog fractional days here is the
  same approximation Anki itself makes when it shows "retrievability" —
  disclosed in the report.
* decay is per-collection FSRS-6 parameter w[20]; the engine reports it per
  card via ComputeMemoryStateResponse.decay (proto/anki/scheduler.proto).
  FSRS-6 default decay = 0.1542 (src/inference.rs:25), pinned below.

Leakage rules (all enforced, all reported):

* Cutoff is chronological over graded reviews (ease > 0, excluding manual/
  rescheduled entries and cramming, mirroring rslib/src/revlog/mod.rs:119-131
  `has_rating_and_affects_scheduling`). Default: last ~25% held out,
  stepping the cutoff earlier (0.05 at a time, floor 0.50) only if needed
  to reach >= 50 held-out observations.
* Holdout = FIRST post-cutoff graded review per card only; later reviews
  depend on held-out history and would leak. Cards first seen post-cutoff
  are skipped (nothing to predict from).
* FSRS params: user params trained on the FULL history would leak, so the
  trained tier re-trains on the truncated copy only
  (`compute_fsrs_params`, params applied as deck-config fsrsParams6). If
  training data is insufficient the engine falls back internally
  (fsrs-5.2.0/src/training.rs:279-296: < 8 items -> defaults, pretrain-only
  for tiny sets); we ALSO always report a pure FSRS-6-defaults tier, so
  the default row is a calibration test of the shipped default model.
* A leakage guard re-opens the truncated copy read-only and fails the run
  if any post-cutoff revlog row or any FSRS state key survives in
  cards.data (keys s/d/dr/decay/lrt: rslib/src/storage/card/data.rs:20-62).

Modes (probe_harness conventions):

* no flags          -> --self-test
* --self-test       -> build a small seeded synthetic collection with a
                       KNOWN generative process (true per-card stability,
                       outcomes Bernoulli(R_true)), run the whole pipeline
                       end-to-end, assert internal checks. Needs the built
                       repo (pylib) like any collection run; module IMPORT
                       stays stdlib-only so unit tests run anywhere.
* --collection PATH -> real run. PATH is copied; the original is never
                       written to. Optional --cutoff-quantile overrides the
                       adaptive split.

Always writes eval/memory_calibration_report.{json,md} and
eval/memory_calibration_chart.svg (hand-rolled reliability diagram:
predicted-recall bins vs observed recall, diagonal reference, per-bin
counts, histogram strip; no numpy/matplotlib anywhere in the repo).
Exit code is non-zero on any failure or refused run.

Honesty rules: every reported number is computed in-run; low-n runs carry
an explicit warning banner (n < 30); log-loss clamps predictions to
[1e-6, 1-1e-6] (disclosed); Python floats are f64 while the engine is f32
(differences ~1e-6, immaterial at these n); same-day learning steps are
handled by the engine's own FSRS-6 short-term memory path because memory
states come from the engine, not a reimplementation.

stdlib only at import time; pylib (anki) is imported lazily inside the
functions that need a collection, mirroring probe_harness.py.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import random
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Engine-pinned constants (provenance in the docstring; tests pin these)
# ---------------------------------------------------------------------------

#: fsrs-5.2.0/src/inference.rs:25
FSRS6_DEFAULT_DECAY = 0.1542
#: fsrs-5.2.0/src/inference.rs:27-49 (DEFAULT_PARAMETERS, 21 entries)
FSRS6_DEFAULT_PARAMETERS = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    FSRS6_DEFAULT_DECAY,
)

MS_PER_DAY = 86_400_000
#: log-loss clamp, same epsilon probe_harness uses (disclosed in reports)
LOG_LOSS_EPSILON = 1e-6
N_BINS = 10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260704
SELF_TEST_SEED = 20260704
#: split rule: hold out the last ~25% of graded reviews...
DEFAULT_CUTOFF_QUANTILE = 0.75
#: ...stepping earlier only if needed to reach this many observations
MIN_HOLDOUT_TARGET = 50
QUANTILE_STEP = 0.05
QUANTILE_FLOOR = 0.50
LOW_N_THRESHOLD = 30

#: rslib/src/revlog/mod.rs RevlogReviewKind
REVLOG_LEARNING = 0
REVLOG_REVIEW = 1
REVLOG_RELEARNING = 2
REVLOG_FILTERED = 3
REVLOG_MANUAL = 4
REVLOG_RESCHEDULED = 5

#: FSRS state keys inside cards.data JSON (rslib/src/storage/card/data.rs:20-62)
FSRS_CARD_DATA_KEYS = ("s", "d", "dr", "decay", "lrt")

TIER_DEFAULTS = "fsrs6-defaults"
TIER_TRAINED = "trained-on-past"
TIER_COLORS = {TIER_TRAINED: "#1f6fb4", TIER_DEFAULTS: "#c44e52"}


class CalibrationError(RuntimeError):
    """A refused or failed calibration run (leakage, no data, ...)."""


# ---------------------------------------------------------------------------
# The engine's forgetting curve (formula provenance in module docstring)
# ---------------------------------------------------------------------------


def retrievability(elapsed_days: float, stability: float, decay: float) -> float:
    """R(t): fsrs-5.2.0/src/inference.rs:60-63, f64 instead of f32."""
    if stability <= 0:
        raise ValueError(f"stability must be positive, got {stability}")
    if decay <= 0:
        raise ValueError(f"decay must be positive, got {decay}")
    factor = 0.9 ** (-1.0 / decay) - 1.0
    return (max(elapsed_days, 0.0) / stability * factor + 1.0) ** -decay


# ---------------------------------------------------------------------------
# Revlog rows, eligibility, split, holdout selection (pure; unit-tested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevlogRow:
    """One revlog entry; id is the review's epoch-ms timestamp."""

    id: int
    cid: int
    ease: int
    kind: int
    factor: int


@dataclass(frozen=True)
class HoldoutObs:
    """One held-out observation: a card's FIRST post-cutoff graded review."""

    card_id: int
    review_ms: int
    #: the card's last pre-cutoff eligible review (what set the memory state)
    prev_ms: int
    elapsed_days: float
    recalled: bool  # ease > 1 (Again=1 is a lapse; Hard/Good/Easy recalled)
    ease: int


def is_eligible(row: RevlogRow) -> bool:
    """Mirror of rslib has_rating_and_affects_scheduling
    (rslib/src/revlog/mod.rs:119-131): graded (ease > 0) and not cramming
    (Filtered kind with ease_factor 0). Manual/Rescheduled entries carry
    ease 0, so the ease test excludes them too — the same rows FSRS
    training and memory-state extraction keep."""
    return row.ease > 0 and not (row.kind == REVLOG_FILTERED and row.factor == 0)


def choose_cutoff(eligible_ids: list[int], quantile: float) -> int:
    """cutoff_ms = id of the first held-out review, i.e. rows with
    id >= cutoff_ms are post-cutoff. Index floor(quantile * n) over the
    ascending eligible review ids."""
    if not eligible_ids:
        raise CalibrationError("no eligible graded reviews to split")
    if not 0.0 < quantile < 1.0:
        raise CalibrationError(f"cutoff quantile must be in (0, 1), got {quantile}")
    ordered = sorted(eligible_ids)
    index = min(int(quantile * len(ordered)), len(ordered) - 1)
    return ordered[index]


def select_holdout(
    rows: list[RevlogRow], cutoff_ms: int
) -> tuple[list[HoldoutObs], dict[str, int]]:
    """First post-cutoff ELIGIBLE review per card, elapsed time measured
    revlog-to-revlog from the card's last pre-cutoff eligible review.
    Later post-cutoff reviews are never used (they depend on held-out
    history); cards first seen post-cutoff are skipped (no pre-cutoff
    memory state exists to test)."""
    last_pre: dict[int, int] = {}
    first_post: dict[int, RevlogRow] = {}
    post_rows = 0
    for row in sorted(rows, key=lambda r: r.id):
        if not is_eligible(row):
            continue
        if row.id < cutoff_ms:
            last_pre[row.cid] = row.id
        else:
            post_rows += 1
            first_post.setdefault(row.cid, row)
    observations: list[HoldoutObs] = []
    skipped_first_seen_post_cutoff = 0
    for cid, row in sorted(first_post.items()):
        prev = last_pre.get(cid)
        if prev is None:
            skipped_first_seen_post_cutoff += 1
            continue
        observations.append(
            HoldoutObs(
                card_id=cid,
                review_ms=row.id,
                prev_ms=prev,
                elapsed_days=(row.id - prev) / MS_PER_DAY,
                recalled=row.ease > 1,
                ease=row.ease,
            )
        )
    stats = {
        "post_cutoff_eligible_rows": post_rows,
        "cards_with_post_cutoff_reviews": len(first_post),
        "skipped_first_seen_post_cutoff": skipped_first_seen_post_cutoff,
        "observations": len(observations),
    }
    return observations, stats


def adaptive_cutoff(
    eligible_ids: list[int],
    rows: list[RevlogRow],
    requested_quantile: float | None,
) -> tuple[int, float, list[dict[str, Any]]]:
    """The written-down split rule: quantile 0.75 (last ~25% of graded
    reviews held out); if that yields < MIN_HOLDOUT_TARGET observations,
    step the quantile down by 0.05 (floor 0.50) and take the first that
    reaches the target, else fall back to the default quantile and let the
    low-n warning fire. An explicit --cutoff-quantile skips the search."""
    tried: list[dict[str, Any]] = []
    if requested_quantile is not None:
        cutoff = choose_cutoff(eligible_ids, requested_quantile)
        observations, _ = select_holdout(rows, cutoff)
        tried.append(
            {"quantile": requested_quantile, "observations": len(observations)}
        )
        return cutoff, requested_quantile, tried
    quantile = DEFAULT_CUTOFF_QUANTILE
    while quantile >= QUANTILE_FLOOR - 1e-9:
        cutoff = choose_cutoff(eligible_ids, quantile)
        observations, _ = select_holdout(rows, cutoff)
        tried.append(
            {"quantile": round(quantile, 2), "observations": len(observations)}
        )
        if len(observations) >= MIN_HOLDOUT_TARGET:
            return cutoff, round(quantile, 2), tried
        quantile -= QUANTILE_STEP
    cutoff = choose_cutoff(eligible_ids, DEFAULT_CUTOFF_QUANTILE)
    return cutoff, DEFAULT_CUTOFF_QUANTILE, tried


# ---------------------------------------------------------------------------
# Metrics (pure; unit-tested)
# ---------------------------------------------------------------------------


def brier_score(pairs: list[tuple[float, bool]]) -> float:
    if not pairs:
        raise ValueError("brier_score of an empty sample")
    return sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)


def log_loss(
    pairs: list[tuple[float, bool]], epsilon: float = LOG_LOSS_EPSILON
) -> float:
    if not pairs:
        raise ValueError("log_loss of an empty sample")
    total = 0.0
    for p, y in pairs:
        p = min(max(p, epsilon), 1.0 - epsilon)
        total += -math.log(p if y else 1.0 - p)
    return total / len(pairs)


def bin_index(p: float, n_bins: int = N_BINS) -> int:
    """Equal-width bins over [0, 1]; p == 1.0 lands in the last bin."""
    return min(int(p * n_bins), n_bins - 1)


def calibration_bins(
    pairs: list[tuple[float, bool]], n_bins: int = N_BINS
) -> list[dict[str, Any]]:
    """Per-bin n / mean predicted / observed recall, all bins reported
    (empty bins carry n=0 and nulls so the table shape is stable)."""
    grouped: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        grouped[bin_index(p, n_bins)].append((p, y))
    bins: list[dict[str, Any]] = []
    for index, members in enumerate(grouped):
        lo, hi = index / n_bins, (index + 1) / n_bins
        entry: dict[str, Any] = {
            "bin": f"[{lo:.1f}, {hi:.1f}{']' if index == n_bins - 1 else ')'}",
            "n": len(members),
            "mean_predicted": None,
            "observed": None,
        }
        if members:
            entry["mean_predicted"] = round(
                sum(p for p, _ in members) / len(members), 6
            )
            entry["observed"] = round(sum(1 for _, y in members if y) / len(members), 6)
        bins.append(entry)
    return bins


def expected_calibration_error(bins: list[dict[str, Any]]) -> float:
    """ECE = sum_b (n_b / N) * |observed_b - mean_predicted_b|."""
    total = sum(entry["n"] for entry in bins)
    if total == 0:
        raise ValueError("ECE of an empty sample")
    gap = 0.0
    for entry in bins:
        if entry["n"]:
            gap += entry["n"] / total * abs(entry["observed"] - entry["mean_predicted"])
    return gap


def bootstrap_brier_ci(
    pairs: list[tuple[float, bool]],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Percentile 95% CI on the Brier score, seeded stdlib bootstrap over
    held-out observations (deterministic for a given seed and sample)."""
    if not pairs:
        raise ValueError("bootstrap of an empty sample")
    rng = random.Random(seed)
    n = len(pairs)
    scores = sorted(
        brier_score([pairs[rng.randrange(n)] for _ in range(n)])
        for _ in range(resamples)
    )
    low = scores[max(0, min(resamples - 1, round(0.025 * (resamples - 1))))]
    high = scores[max(0, min(resamples - 1, round(0.975 * (resamples - 1))))]
    return low, high


def metrics_block(
    pairs: list[tuple[float, bool]],
    train_recall_rate: float,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """All headline metrics for one tier plus the two context baselines."""
    n = len(pairs)
    bins = calibration_bins(pairs)
    ci_low, ci_high = bootstrap_brier_ci(pairs, bootstrap_resamples, bootstrap_seed)
    constant = [(train_recall_rate, y) for _, y in pairs]
    chance = [(0.5, y) for _, y in pairs]
    observed = sum(1 for _, y in pairs if y) / n
    caveats: list[str] = []
    if observed in (0.0, 1.0):
        outcome = "lapses" if observed == 1.0 else "recalls"
        caveats.append(
            f"degenerate holdout: zero {outcome} among the held-out "
            "reviews, so the evidence is one-sided (miscalibration toward "
            f"{'over' if observed == 1.0 else 'under'}-prediction cannot "
            "show up)"
        )
    if sum(1 for entry in bins if entry["n"]) == 1:
        caveats.append(
            "all predictions fall into a single bin; the reliability "
            "curve is a single point, not a curve"
        )
    return {
        "n": n,
        "brier": round(brier_score(pairs), 6),
        "brier_ci95": [round(ci_low, 6), round(ci_high, 6)],
        "log_loss": round(log_loss(pairs), 6),
        "log_loss_epsilon": LOG_LOSS_EPSILON,
        "ece": round(expected_calibration_error(bins), 6),
        "observed_recall": round(observed, 6),
        "mean_predicted": round(sum(p for p, _ in pairs) / n, 6),
        "caveats": caveats,
        "baselines": {
            "constant_train_rate": {
                "p": round(train_recall_rate, 6),
                "brier": round(brier_score(constant), 6),
                "log_loss": round(log_loss(constant), 6),
                "note": "predicts the TRAIN-side global recall rate for every "
                "held-out review",
            },
            "chance_0.5": {
                "brier": round(brier_score(chance), 6),
                "log_loss": round(log_loss(chance), 6),
            },
        },
        "bins": bins,
        "bootstrap": {"resamples": bootstrap_resamples, "seed": bootstrap_seed},
    }


# ---------------------------------------------------------------------------
# Collection plumbing: sqlite copy, truncation, leakage guard (stdlib)
# ---------------------------------------------------------------------------


def read_revlog_rows(path: Path | str) -> list[RevlogRow]:
    """All revlog rows from a collection, opened READ-ONLY."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return [
            RevlogRow(id=rid, cid=cid, ease=ease, kind=kind, factor=factor)
            for rid, cid, ease, kind, factor in con.execute(
                "select id, cid, ease, type, factor from revlog order by id"
            )
        ]
    finally:
        con.close()


def strip_fsrs_card_state(obj: dict[str, Any]) -> bool:
    """Drop the FSRS keys from one cards.data JSON object, in place.
    Returns True if anything was removed."""
    removed = False
    for key in FSRS_CARD_DATA_KEYS:
        if key in obj:
            del obj[key]
            removed = True
    return removed


def build_truncated_copy(
    source: Path | str, dest: Path, cutoff_ms: int
) -> dict[str, int]:
    """Copy the collection, DELETE every revlog row with id >= cutoff_ms
    (all kinds — post-cutoff manual entries would leak too), and strip
    FSRS memory-state keys from cards.data. The engine then recomputes
    memory states from the surviving pre-cutoff revlog only."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in (dest, Path(f"{dest}-wal"), Path(f"{dest}-shm")):
        if stale.exists():
            stale.unlink()
    shutil.copyfile(source, dest)
    con = sqlite3.connect(dest)
    try:
        deleted = con.execute("delete from revlog where id >= ?", (cutoff_ms,)).rowcount
        cleared = 0
        for cid, data in con.execute(
            "select id, data from cards where data != ''"
        ).fetchall():
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and strip_fsrs_card_state(obj):
                con.execute(
                    "update cards set data = ? where id = ?",
                    (json.dumps(obj, separators=(",", ":")), cid),
                )
                cleared += 1
        con.commit()
    finally:
        con.close()
    return {"revlog_rows_deleted": deleted, "cards_fsrs_state_cleared": cleared}


def leakage_check(db_path: Path | str, cutoff_ms: int) -> dict[str, Any]:
    """Independent re-open (read-only) of the truncated copy: zero
    post-cutoff revlog rows and zero surviving FSRS state keys, or the run
    fails."""
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        post = con.execute(
            "select count(*) from revlog where id >= ?", (cutoff_ms,)
        ).fetchone()[0]
        dirty = 0
        for (data,) in con.execute("select data from cards where data != ''"):
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and any(key in obj for key in FSRS_CARD_DATA_KEYS):
                dirty += 1
    finally:
        con.close()
    return {
        "passed": post == 0 and dirty == 0,
        "post_cutoff_revlog_rows": post,
        "cards_with_fsrs_state": dirty,
        "cutoff_ms": cutoff_ms,
    }


# ---------------------------------------------------------------------------
# Engine bridge (pylib imported lazily; needs the built repo)
# ---------------------------------------------------------------------------


def open_collection(path: Path | str) -> Any:
    from anki.collection import Collection  # deferred: pylib optional

    return Collection(str(path))


def train_params_on_truncated(col: Any) -> dict[str, Any]:
    """Retrain FSRS params on the truncated copy ONLY (leakage rule: user
    params were fit on history that includes the holdout). Mirrors what
    deck options sends: whole-collection search, relearning-step count from
    the deck config. The engine handles small samples internally
    (fsrs-5.2.0/src/training.rs:279-296) and may return its own defaults or
    keep `current_params` ([] here -> defaults) when training does not
    beat them."""
    configs = col.decks.all_config()
    relearn_steps = 1
    if configs:
        relearn_steps = len((configs[0].get("lapse") or {}).get("delays") or [])
    try:
        resp = col._backend.compute_fsrs_params(
            search="",
            current_params=[],
            ignore_revlogs_before_ms=0,
            num_of_relearning_steps=relearn_steps,
            health_check=False,
        )
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    params = [float(p) for p in resp.params]
    if not params:
        # rslib returns current_params ([] here) either when nothing is
        # trainable or when the optimized params did not beat the current
        # ones on training log-loss (rslib/src/scheduler/fsrs/params.rs)
        if int(resp.fsrs_items) == 0:
            note = (
                "no trainable FSRS items in the truncated revlog; the "
                "defaults tier is the only calibration row"
            )
            status = "insufficient"
        else:
            note = (
                f"engine trained on {int(resp.fsrs_items)} items but kept "
                "the current (default) parameters — optimized params did "
                "not beat them on training log-loss; a trained tier would "
                "duplicate the defaults tier"
            )
            status = "engine_kept_defaults"
        return {"status": status, "fsrs_items": int(resp.fsrs_items), "note": note}
    equals_defaults = len(params) == len(FSRS6_DEFAULT_PARAMETERS) and all(
        abs(a - b) < 1e-6 for a, b in zip(params, FSRS6_DEFAULT_PARAMETERS)
    )
    pretrain_only = (
        len(params) == len(FSRS6_DEFAULT_PARAMETERS)
        and not equals_defaults
        and all(
            abs(a - b) < 1e-6 for a, b in zip(params[4:], FSRS6_DEFAULT_PARAMETERS[4:])
        )
    )
    return {
        "status": "ok",
        "params": params,
        "fsrs_items": int(resp.fsrs_items),
        "num_of_relearning_steps": relearn_steps,
        "equals_defaults": equals_defaults,
        "pretrain_only_shape": pretrain_only,
    }


def apply_params(col: Any, params: list[float]) -> None:
    """Write fsrsParams6 into every deck config and enable FSRS, so
    compute_memory_state uses the trained-on-past parameters."""
    for conf in col.decks.all_config():
        conf["fsrsParams6"] = [float(p) for p in params]
        col.decks.update_config(conf)
    col.set_config("fsrs", True)


def compute_states(col: Any, card_ids: list[int]) -> dict[int, dict[str, float] | None]:
    """Engine-recomputed memory state per card (from the truncated revlog)."""
    states: dict[int, dict[str, float] | None] = {}
    for cid in card_ids:
        computed = col.compute_memory_state(cid)
        if computed.stability is None or computed.decay is None:
            states[cid] = None
        else:
            states[cid] = {
                "stability": float(computed.stability),
                "difficulty": float(computed.difficulty),
                "decay": float(computed.decay),
                "desired_retention": float(computed.desired_retention),
            }
    return states


# ---------------------------------------------------------------------------
# Tier evaluation
# ---------------------------------------------------------------------------


def evaluate_tier(
    observations: list[HoldoutObs],
    states: dict[int, dict[str, float] | None],
) -> tuple[list[tuple[float, bool]], list[dict[str, Any]], int]:
    """(predicted, observed) pairs via the engine-verified curve, plus
    per-observation rows for the JSON report and the skip count for cards
    the engine produced no memory state for."""
    pairs: list[tuple[float, bool]] = []
    per_obs: list[dict[str, Any]] = []
    skipped_no_state = 0
    for obs in observations:
        state = states.get(obs.card_id)
        if state is None:
            skipped_no_state += 1
            continue
        predicted = retrievability(obs.elapsed_days, state["stability"], state["decay"])
        pairs.append((predicted, obs.recalled))
        per_obs.append(
            {
                "card_id": obs.card_id,
                "elapsed_days": round(obs.elapsed_days, 4),
                "stability": round(state["stability"], 4),
                "difficulty": round(state["difficulty"], 3),
                "decay": round(state["decay"], 4),
                "predicted": round(predicted, 6),
                "recalled": obs.recalled,
                "ease": obs.ease,
            }
        )
    return pairs, per_obs, skipped_no_state


# ---------------------------------------------------------------------------
# Reliability chart (hand-rolled SVG, stdlib string building)
# ---------------------------------------------------------------------------

SVG_WIDTH = 760
SVG_HEIGHT = 600
PLOT_X, PLOT_Y, PLOT_W, PLOT_H = 100, 70, 480, 360
HIST_Y, HIST_H = PLOT_Y + PLOT_H + 60, 80


def _svg_x(p: float) -> float:
    return PLOT_X + p * PLOT_W


def _svg_y(p: float) -> float:
    return PLOT_Y + (1.0 - p) * PLOT_H


def reliability_svg(
    tiers: list[dict[str, Any]],
    title: str,
    subtitle: str,
    warning: str | None = None,
) -> str:
    """Reliability diagram: x = predicted-recall bin (mean predicted), y =
    observed recall, dashed diagonal = perfect calibration, per-bin counts
    labelled, histogram strip of prediction counts per bin underneath.
    `tiers`: [{"tier", "color", "bins", "n"}, ...] (first tier drawn on
    top and used for point count labels)."""
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" '
        f'height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" '
        'font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" fill="#ffffff"/>',
        f'<text x="{PLOT_X}" y="28" font-size="17" font-weight="bold" '
        f'fill="#222222">{title}</text>',
        f'<text x="{PLOT_X}" y="48" font-size="12" fill="#555555">{subtitle}</text>',
    ]
    if warning:
        parts.append(
            f'<text class="low-n-warning" x="{PLOT_X}" y="{SVG_HEIGHT - 12}" '
            f'font-size="12" font-weight="bold" fill="#b03a2e">{warning}</text>'
        )

    # frame, gridlines, ticks
    for tick in range(0, 11, 2):
        p = tick / 10.0
        x, y = _svg_x(p), _svg_y(p)
        parts += [
            f'<line class="grid" x1="{x:.1f}" y1="{PLOT_Y}" x2="{x:.1f}" '
            f'y2="{PLOT_Y + PLOT_H}" stroke="#e6e6e6" stroke-width="1"/>',
            f'<line class="grid" x1="{PLOT_X}" y1="{y:.1f}" '
            f'x2="{PLOT_X + PLOT_W}" y2="{y:.1f}" stroke="#e6e6e6" '
            'stroke-width="1"/>',
            f'<text x="{x:.1f}" y="{PLOT_Y + PLOT_H + 18}" font-size="11" '
            f'fill="#444444" text-anchor="middle">{p:.1f}</text>',
            f'<text x="{PLOT_X - 8}" y="{y + 4:.1f}" font-size="11" '
            f'fill="#444444" text-anchor="end">{p:.1f}</text>',
        ]
    parts += [
        f'<rect class="frame" x="{PLOT_X}" y="{PLOT_Y}" width="{PLOT_W}" '
        f'height="{PLOT_H}" fill="none" stroke="#888888" stroke-width="1"/>',
        f'<line class="diagonal" x1="{_svg_x(0):.1f}" y1="{_svg_y(0):.1f}" '
        f'x2="{_svg_x(1):.1f}" y2="{_svg_y(1):.1f}" stroke="#999999" '
        'stroke-width="1.5" stroke-dasharray="6,5"/>',
        f'<text x="{PLOT_X + PLOT_W / 2:.0f}" y="{PLOT_Y + PLOT_H + 40}" '
        'font-size="12" fill="#333333" text-anchor="middle">predicted recall '
        "(engine retrievability, binned)</text>",
        f'<text x="26" y="{PLOT_Y + PLOT_H / 2:.0f}" font-size="12" '
        f'fill="#333333" text-anchor="middle" '
        f'transform="rotate(-90 26 {PLOT_Y + PLOT_H / 2:.0f})">observed recall'
        "</text>",
    ]

    # calibration points/lines per tier (draw secondary tiers first)
    for order, tier in enumerate(reversed(tiers)):
        primary = order == len(tiers) - 1
        color = tier["color"]
        filled = [b for b in tier["bins"] if b["n"]]
        points = [(_svg_x(b["mean_predicted"]), _svg_y(b["observed"])) for b in filled]
        if len(points) > 1:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            parts.append(
                f'<polyline class="calib-line" fill="none" stroke="{color}" '
                f'stroke-width="1.5" points="{path}"/>'
            )
        for (x, y), entry in zip(points, filled):
            parts.append(
                f'<circle class="bin-point" cx="{x:.1f}" cy="{y:.1f}" r="4" '
                f'fill="{color}" stroke="#ffffff" stroke-width="1"/>'
            )
            if primary:
                parts.append(
                    f'<text class="bin-count" x="{x:.1f}" y="{y - 9:.1f}" '
                    f'font-size="10" fill="{color}" text-anchor="middle">'
                    f"n={entry['n']}</text>"
                )

    # legend
    legend_y = PLOT_Y + 14
    for tier in tiers:
        parts += [
            f'<rect x="{PLOT_X + 12}" y="{legend_y - 9}" width="12" height="12" '
            f'fill="{tier["color"]}"/>',
            f'<text class="legend" x="{PLOT_X + 30}" y="{legend_y + 1}" '
            f'font-size="12" fill="#222222">{tier["tier"]} (n={tier["n"]})</text>',
        ]
        legend_y += 18
    parts.append(
        f'<text class="legend" x="{PLOT_X + 12}" y="{legend_y + 1}" '
        'font-size="11" fill="#999999">dashed diagonal = perfectly calibrated'
        "</text>"
    )

    # histogram strip: predictions per bin, counts labelled
    max_count = max((b["n"] for tier in tiers for b in tier["bins"]), default=0)
    parts.append(
        f'<text x="{PLOT_X}" y="{HIST_Y - 8}" font-size="12" fill="#333333">'
        "predictions per bin</text>"
    )
    if max_count:
        n_tiers = len(tiers)
        bin_w = PLOT_W / N_BINS
        bar_w = (bin_w - 8) / n_tiers
        for tier_index, tier in enumerate(tiers):
            color = tier["color"]
            for index, entry in enumerate(tier["bins"]):
                height = HIST_H * entry["n"] / max_count
                x = PLOT_X + index * bin_w + 4 + tier_index * bar_w
                y = HIST_Y + HIST_H - height
                parts.append(
                    f'<rect class="hist-bar" x="{x:.1f}" y="{y:.1f}" '
                    f'width="{bar_w:.1f}" height="{height:.1f}" fill="{color}" '
                    'fill-opacity="0.75"/>'
                )
                if entry["n"]:
                    parts.append(
                        f'<text class="hist-count" x="{x + bar_w / 2:.1f}" '
                        f'y="{y - 3:.1f}" font-size="9" fill="{color}" '
                        f'text-anchor="middle">{entry["n"]}</text>'
                    )
        parts.append(
            f'<line x1="{PLOT_X}" y1="{HIST_Y + HIST_H}" '
            f'x2="{PLOT_X + PLOT_W}" y2="{HIST_Y + HIST_H}" stroke="#888888" '
            'stroke-width="1"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# The full pipeline against one collection file
# ---------------------------------------------------------------------------


def run_calibration(
    collection_path: Path | str,
    work_dir: Path,
    cutoff_quantile: float | None = None,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Truncate -> guard -> engine memory states (defaults tier, then a
    trained-on-past tier when the engine can fit one) -> held-out metrics.
    The source collection is only ever opened read-only."""
    collection_path = Path(collection_path)
    rows = read_revlog_rows(collection_path)
    eligible = [row for row in rows if is_eligible(row)]
    if len(eligible) < 10:
        raise CalibrationError(
            f"only {len(eligible)} eligible graded reviews; refusing to "
            "calibrate on fewer than 10"
        )
    eligible_ids = [row.id for row in eligible]
    cutoff_ms, used_quantile, quantiles_tried = adaptive_cutoff(
        eligible_ids, rows, cutoff_quantile
    )
    observations, selection = select_holdout(rows, cutoff_ms)
    if not observations:
        raise CalibrationError(
            "holdout selection produced zero observations; nothing to score"
        )

    train_rows = [row for row in eligible if row.id < cutoff_ms]
    train_recall_rate = sum(1 for row in train_rows if row.ease > 1) / len(train_rows)

    work_dir.mkdir(parents=True, exist_ok=True)
    truncated = work_dir / f"truncated_{collection_path.stem}.anki2"
    truncation = build_truncated_copy(collection_path, truncated, cutoff_ms)
    guard = leakage_check(truncated, cutoff_ms)
    if not guard["passed"]:
        raise CalibrationError(f"leakage guard failed: {guard}")

    # revlog survives card deletion in Anki; the engine cannot compute a
    # memory state for a card that no longer exists, so orphaned holdout
    # rows are dropped and counted honestly
    con = sqlite3.connect(f"file:{truncated}?mode=ro", uri=True)
    try:
        existing_cards = {row[0] for row in con.execute("select id from cards")}
    finally:
        con.close()
    orphaned = sum(1 for obs in observations if obs.card_id not in existing_cards)
    observations = [obs for obs in observations if obs.card_id in existing_cards]
    selection["skipped_card_deleted"] = orphaned
    selection["observations"] = len(observations)
    if not observations:
        raise CalibrationError(
            "every held-out observation belongs to a deleted card; nothing "
            "the engine can score"
        )

    card_ids = [obs.card_id for obs in observations]
    col = open_collection(truncated)
    try:
        default_states = compute_states(col, card_ids)
        training = train_params_on_truncated(col)
        trained_states = None
        if training["status"] == "ok" and not training["equals_defaults"]:
            apply_params(col, training["params"])
            trained_states = compute_states(col, card_ids)
    finally:
        col.close()

    tiers: list[dict[str, Any]] = []
    if trained_states is not None:
        pairs, per_obs, skipped = evaluate_tier(observations, trained_states)
        if pairs:
            tiers.append(
                {
                    "tier": TIER_TRAINED,
                    "params_source": (
                        f"compute_fsrs_params on the truncated copy only "
                        f"({training['fsrs_items']} FSRS items)"
                    ),
                    "params": [round(p, 6) for p in training["params"]],
                    "pretrain_only_shape": training["pretrain_only_shape"],
                    "skipped_no_memory_state": skipped,
                    "metrics": metrics_block(
                        pairs, train_recall_rate, bootstrap_resamples, bootstrap_seed
                    ),
                    "per_observation": per_obs,
                }
            )
    pairs, per_obs, skipped = evaluate_tier(observations, default_states)
    if pairs:
        tiers.append(
            {
                "tier": TIER_DEFAULTS,
                "params_source": "FSRS-6 default parameters (no user fit; "
                "calibration of the shipped default model)",
                "params": None,
                "skipped_no_memory_state": skipped,
                "metrics": metrics_block(
                    pairs, train_recall_rate, bootstrap_resamples, bootstrap_seed
                ),
                "per_observation": per_obs,
            }
        )
    if not tiers:
        raise CalibrationError("engine produced no memory state for any held-out card")

    max_n = max(tier["metrics"]["n"] for tier in tiers)
    return {
        "path": str(collection_path),
        "revlog_rows": len(rows),
        "eligible_graded_reviews": len(eligible),
        "split": {
            "rule": (
                "chronological cutoff at the graded-review timestamp quantile "
                f"{DEFAULT_CUTOFF_QUANTILE} (last ~25% held out), stepped "
                f"earlier by {QUANTILE_STEP} (floor {QUANTILE_FLOOR}) only if "
                f"needed to reach >= {MIN_HOLDOUT_TARGET} held-out "
                "first-post-cutoff-per-card observations; observed recall = "
                "ease > 1; eligibility mirrors rslib "
                "has_rating_and_affects_scheduling (graded, non-manual, "
                "non-cramming)"
            ),
            "requested_quantile": cutoff_quantile,
            "used_quantile": used_quantile,
            "quantiles_tried": quantiles_tried,
            "cutoff_ms": cutoff_ms,
            "cutoff_utc": datetime.datetime.fromtimestamp(
                cutoff_ms / 1000, tz=datetime.timezone.utc
            ).isoformat(timespec="seconds"),
            "train_reviews": len(train_rows),
            "train_recall_rate": round(train_recall_rate, 6),
            **selection,
        },
        "truncation": truncation,
        "leakage_guard": guard,
        "training": {key: value for key, value in training.items() if key != "params"},
        "tiers": tiers,
        "low_n_warning": max_n < LOW_N_THRESHOLD,
        "truncated_copy": str(truncated),
    }


# ---------------------------------------------------------------------------
# --self-test: seeded synthetic collection, known generative process
# ---------------------------------------------------------------------------


def _known_answer_checks() -> list[str]:
    """Hand-computed fixtures; AssertionError on any regression."""
    passed: list[str] = []

    assert retrievability(0.0, 5.0, FSRS6_DEFAULT_DECAY) == 1.0
    for decay in (0.1, FSRS6_DEFAULT_DECAY, 0.5, 1.0):
        for stability in (0.5, 5.0, 100.0):
            assert abs(retrievability(stability, stability, decay) - 0.9) < 1e-12
    assert retrievability(10.0, 5.0, 0.2) < retrievability(1.0, 5.0, 0.2)
    passed.append("curve: R(0)=1, R(S)=0.9 for any decay, monotone decreasing")

    pairs = [(0.8, True), (0.4, False)]
    assert abs(brier_score(pairs) - 0.10) < 1e-12
    expected = -(math.log(0.8) + math.log(0.6)) / 2.0
    assert abs(log_loss(pairs) - expected) < 1e-12
    passed.append("brier/log-loss match hand-computed values")

    bins = calibration_bins([(0.05, False), (0.95, True), (0.92, False)])
    assert bins[0]["n"] == 1 and bins[9]["n"] == 2
    assert abs(bins[9]["observed"] - 0.5) < 1e-12
    ece = expected_calibration_error(bins)
    assert abs(ece - (1 / 3 * 0.05 + 2 / 3 * abs(0.5 - 0.935))) < 1e-9
    passed.append("binning and ECE match a hand-computed fixture")

    sample = [(0.7, True), (0.6, False), (0.9, True), (0.2, False)]
    assert bootstrap_brier_ci(sample, 200, 7) == bootstrap_brier_ci(sample, 200, 7)
    passed.append("bootstrap is deterministic under a fixed seed")

    day = MS_PER_DAY
    micro = [
        RevlogRow(1 * day, 1, 3, REVLOG_LEARNING, 0),
        RevlogRow(2 * day, 1, 3, REVLOG_REVIEW, 2500),
        RevlogRow(5 * day, 1, 1, REVLOG_REVIEW, 2500),  # holdout: lapse
        RevlogRow(6 * day, 1, 3, REVLOG_REVIEW, 2500),  # later: ignored
        RevlogRow(1 * day + 1, 2, 3, REVLOG_LEARNING, 0),
        RevlogRow(5 * day + 1, 2, 0, REVLOG_MANUAL, 0),  # manual: ignored
        RevlogRow(6 * day + 1, 2, 4, REVLOG_REVIEW, 2500),  # holdout: recalled
        RevlogRow(5 * day + 2, 3, 3, REVLOG_LEARNING, 0),  # first seen post-cutoff
        RevlogRow(4 * day, 4, 2, REVLOG_FILTERED, 0),  # cramming: ignored
    ]
    observations, stats = select_holdout(micro, 5 * day)
    assert [o.card_id for o in observations] == [1, 2]
    assert observations[0].recalled is False and observations[1].recalled is True
    assert abs(observations[0].elapsed_days - 3.0) < 1e-12
    assert abs(observations[1].elapsed_days - 5.0) < 1e-12  # from 1*day+1
    assert stats["skipped_first_seen_post_cutoff"] == 1
    passed.append(
        "holdout: first-post-cutoff-per-card, manual/cram ignored, "
        "post-cutoff-only cards skipped"
    )
    return passed


def build_synthetic_collection(
    path: Path, seed: int = SELF_TEST_SEED, cards: int = 60
) -> dict[str, Any]:
    """A pylib collection whose revlog follows a KNOWN process: each card
    gets a fixed true stability; review gaps are seeded; outcomes are
    Bernoulli(R_true(gap)) with the engine's own curve shape. The pipeline
    must recover finite, better-than-chance calibration from it."""
    # deferred pylib imports; anki.collection must come first (importing
    # anki.decks before the package is initialized trips a circular import)
    from anki.collection import Collection  # noqa: F401
    from anki.decks import DeckId

    rng = random.Random(seed)
    for stale in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if stale.exists():
            stale.unlink()
    col = open_collection(path)
    try:
        notetype = col.models.by_name("Basic")
        card_ids: list[int] = []
        for index in range(cards):
            note = col.new_note(notetype)
            note["Front"] = f"synthetic prompt {index}"
            note["Back"] = f"synthetic answer {index}"
            col.add_note(note, DeckId(1))
            card_ids.extend(int(cid) for cid in col.card_ids_of_note(note.id))
    finally:
        col.close()

    now_ms = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000)
    base_ms = now_ms - 240 * MS_PER_DAY
    used_ids: set[int] = set()
    rows: list[tuple[int, int, int, int, int]] = []
    for cid in card_ids:
        true_stability = math.exp(rng.uniform(math.log(0.8), math.log(45.0)))
        at = base_ms + rng.randrange(0, 10 * MS_PER_DAY)
        while at in used_ids:
            at += 1
        used_ids.add(at)
        rows.append((at, cid, 3, 1, REVLOG_LEARNING))
        for _ in range(rng.randrange(4, 9)):
            gap_days = math.exp(rng.uniform(math.log(0.2), math.log(35.0)))
            at += max(1, int(gap_days * MS_PER_DAY))
            while at in used_ids:
                at += 1
            used_ids.add(at)
            recalled = rng.random() < retrievability(
                gap_days, true_stability, FSRS6_DEFAULT_DECAY
            )
            rows.append((at, cid, 3 if recalled else 1, 1, REVLOG_REVIEW))
    rows.sort()

    con = sqlite3.connect(path)
    try:
        con.executemany(
            "insert into revlog (id, cid, usn, ease, ivl, lastIvl, factor, "
            "time, type) values (?, ?, -1, ?, ?, 1, 2500, 3000, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()
    return {"cards": len(card_ids), "revlog_rows": len(rows), "seed": seed}


def run_self_test(work_dir: Path, seed: int = SELF_TEST_SEED) -> dict[str, Any]:
    """Deterministic end-to-end run over the synthetic collection; raises
    AssertionError on failure. Requires pylib (the built repo)."""
    checks = _known_answer_checks()

    work_dir.mkdir(parents=True, exist_ok=True)
    synth_path = work_dir / "self_test_synthetic.anki2"
    synth = build_synthetic_collection(synth_path, seed)
    result = run_calibration(synth_path, work_dir / "self_test")

    assert result["leakage_guard"]["passed"], result["leakage_guard"]
    checks.append("leakage guard: zero post-cutoff rows in the truncated copy")

    # tamper with a copy: the guard must catch planted post-cutoff rows
    tampered = work_dir / "self_test" / "tampered.anki2"
    shutil.copyfile(result["truncated_copy"], tampered)
    con = sqlite3.connect(tampered)
    con.execute(
        "insert into revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, "
        "type) values (?, 1, -1, 3, 1, 1, 2500, 3000, 1)",
        (result["split"]["cutoff_ms"] + 12345,),
    )
    con.commit()
    con.close()
    assert not leakage_check(tampered, result["split"]["cutoff_ms"])["passed"]
    checks.append("leakage guard: rejects a deliberately contaminated copy")

    n = max(tier["metrics"]["n"] for tier in result["tiers"])
    assert n >= LOW_N_THRESHOLD, f"synthetic holdout too small: {n}"
    for tier in result["tiers"]:
        metrics = tier["metrics"]
        for key in ("brier", "log_loss", "ece"):
            assert math.isfinite(metrics[key]), (tier["tier"], key)
        assert 0.0 <= metrics["brier"] <= 1.0
        for row in tier["per_observation"]:
            assert 0.0 <= row["predicted"] <= 1.0
        chance = metrics["baselines"]["chance_0.5"]["brier"]
        assert metrics["brier"] < chance, (
            f"{tier['tier']}: Brier {metrics['brier']} did not beat chance "
            f"{chance} on synthetic data with a real signal"
        )
    checks.append(
        "synthetic pipeline: finite metrics, predictions in [0,1], "
        "Brier beats chance on every tier"
    )

    svg = reliability_svg(
        [
            {
                "tier": tier["tier"],
                "color": TIER_COLORS[tier["tier"]],
                "bins": tier["metrics"]["bins"],
                "n": tier["metrics"]["n"],
            }
            for tier in result["tiers"]
        ],
        "self-test reliability",
        "synthetic data",
    )
    assert "<svg" in svg and 'class="diagonal"' in svg and 'class="hist-bar"' in svg
    checks.append("chart: SVG renders with diagonal + histogram structure")

    return {
        "seed": seed,
        "synthetic": synth,
        "checks_passed": checks,
        "split": result["split"],
        "training": result["training"],
        "low_n_warning": result["low_n_warning"],
        "tiers": [
            {"tier": tier["tier"], "metrics": tier["metrics"]}
            for tier in result["tiers"]
        ],
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value}"


def _tier_markdown(tier: dict[str, Any]) -> list[str]:
    metrics = tier["metrics"]
    baselines = metrics["baselines"]
    lines = [
        f"### Tier: {tier['tier']}",
        "",
        f"Params: {tier['params_source']}",
        f"- n = {metrics['n']} held-out observations "
        f"({tier['skipped_no_memory_state']} skipped: engine returned no "
        "memory state)",
        f"- Brier {metrics['brier']} (95% CI {metrics['brier_ci95'][0]}–"
        f"{metrics['brier_ci95'][1]}, {metrics['bootstrap']['resamples']} "
        f"seeded bootstrap resamples, seed {metrics['bootstrap']['seed']})",
        f"- log-loss {metrics['log_loss']} (predictions clamped to "
        f"[{metrics['log_loss_epsilon']}, 1-{metrics['log_loss_epsilon']}])",
        f"- ECE {metrics['ece']} (10 equal-width bins)",
        f"- observed recall {metrics['observed_recall']} vs mean predicted "
        f"{metrics['mean_predicted']}",
        f"- baseline constant p={baselines['constant_train_rate']['p']} "
        f"(train recall rate): Brier {baselines['constant_train_rate']['brier']}"
        f", log-loss {baselines['constant_train_rate']['log_loss']}",
        f"- baseline chance p=0.5: Brier {baselines['chance_0.5']['brier']}, "
        f"log-loss {baselines['chance_0.5']['log_loss']}",
    ]
    lines += [f"- CAVEAT: {caveat}" for caveat in metrics.get("caveats", [])]
    lines += [
        "",
        "| bin | n | mean predicted | observed |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {b['bin']} | {b['n']} | {_fmt(b['mean_predicted'])} | "
        f"{_fmt(b['observed'])} |"
        for b in metrics["bins"]
    ]
    lines.append("")
    return lines


def _markdown_report(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        "# Memory calibration report",
        "",
        f"Generated: {meta['generated_at']} — modes: {', '.join(meta['modes'])}",
        "",
        "## Formula provenance",
        "",
        "`R(t) = (1 + factor * t / S) ** (-decay)`, "
        "`factor = 0.9 ** (-1/decay) - 1`, t in fractional days "
        "(seconds / 86400).",
        "",
        "- fsrs crate 5.2.0 `src/inference.rs:60-63` (`current_retrievability`)"
        " and `src/inference.rs:512-519` (`current_retrievability_seconds`, "
        "the seconds/86400 day arithmetic); same curve as `src/model.rs:52-56`.",
        "- rslib uses exactly this path for its own retrievability displays: "
        "`rslib/src/stats/graphs/retrievability.rs:33-34`.",
        "- stability/difficulty/decay come from the ENGINE "
        "(`Collection.compute_memory_state`, rslib "
        "`scheduler/fsrs/memory_state.rs:360`), recomputed from the truncated "
        "revlog — this tool never reimplements the memory-state update.",
        f"- FSRS-6 default decay {FSRS6_DEFAULT_DECAY} "
        "(`src/inference.rs:25`), pinned by the unit tests.",
        "",
    ]

    collection = report.get("collection")
    if collection and "failed" in collection:
        lines += [
            "## Collection run",
            "",
            f"REFUSED — `{collection['path']}`: {collection['failed']}",
            "",
        ]
        collection = None
    if collection:
        split = collection["split"]
        lines += [
            "## Collection run",
            "",
            f"Path: `{collection['path']}` — {collection['revlog_rows']} revlog "
            f"rows, {collection['eligible_graded_reviews']} eligible graded "
            "reviews.",
            "",
            "### Split rule",
            "",
            split["rule"] + ".",
            "",
            f"- cutoff: {split['cutoff_utc']} (ms {split['cutoff_ms']}), "
            f"quantile used {split['used_quantile']}"
            + (
                f" (requested {split['requested_quantile']})"
                if split["requested_quantile"] is not None
                else f" (adaptive; tried {split['quantiles_tried']})"
            ),
            f"- train side: {split['train_reviews']} reviews, recall rate "
            f"{split['train_recall_rate']}",
            f"- post-cutoff: {split['post_cutoff_eligible_rows']} eligible rows "
            f"on {split['cards_with_post_cutoff_reviews']} cards; "
            f"{split['skipped_first_seen_post_cutoff']} cards skipped (first "
            f"seen post-cutoff), {split.get('skipped_card_deleted', 0)} skipped "
            f"(card deleted, orphan revlog) -> {split['observations']} held-out "
            "observations (first post-cutoff review per card)",
            f"- leakage guard: PASSED — {collection['leakage_guard']['post_cutoff_revlog_rows']} "
            f"post-cutoff revlog rows, "
            f"{collection['leakage_guard']['cards_with_fsrs_state']} cards with "
            "surviving FSRS state in the truncated copy "
            f"({collection['truncation']['revlog_rows_deleted']} rows deleted, "
            f"{collection['truncation']['cards_fsrs_state_cleared']} cards "
            "stripped)",
            f"- params training on truncated copy: {collection['training']}",
            "",
        ]
        if collection["low_n_warning"]:
            lines += [
                f"**LOW-N WARNING: fewer than {LOW_N_THRESHOLD} held-out "
                "observations. The Brier CI below is wide and this run is "
                "not strong evidence either way — that is the honest "
                "result.**",
                "",
            ]
        for tier in collection["tiers"]:
            lines += _tier_markdown(tier)
        if report.get("chart"):
            lines += [
                f"Chart: `{Path(report['chart']).name}` (reliability diagram "
                "+ prediction histogram, same directory as this report).",
                "",
            ]

    self_test = report.get("self_test")
    if self_test and "failed" in self_test:
        lines += [
            "## Self-test (synthetic, seeded)",
            "",
            f"FAILED — {self_test['failed']}",
            "",
        ]
        self_test = None
    if self_test:
        lines += [
            "## Self-test (synthetic, seeded)",
            "",
            f"Seed {self_test['seed']}; {self_test['synthetic']['cards']} cards, "
            f"{self_test['synthetic']['revlog_rows']} synthetic reviews; "
            f"{len(self_test['checks_passed'])} internal checks passed.",
            "",
        ]
        lines += [f"- {check}" for check in self_test["checks_passed"]]
        lines.append("")
        for tier in self_test["tiers"]:
            metrics = tier["metrics"]
            lines.append(
                f"- {tier['tier']}: n={metrics['n']}, Brier {metrics['brier']} "
                f"(chance {metrics['baselines']['chance_0.5']['brier']}), "
                f"log-loss {metrics['log_loss']}, ECE {metrics['ece']}"
            )
        lines.append("")

    lines += [
        "## Honesty notes",
        "",
        "- Memory states and parameters come from the engine on a truncated "
        "copy; predictions never see post-cutoff data. The independent "
        "leakage guard re-checks the copy and fails the run on any hit.",
        "- Only the FIRST post-cutoff review per card is scored; later "
        "reviews depend on held-out history.",
        "- Elapsed time is revlog-to-revlog fractional days "
        "(seconds/86400), the engine's own display-path arithmetic; FSRS "
        "TRAINS on rollover-aware integer days, so same-day boundaries can "
        "differ slightly from the scheduler's internal day counting "
        "(disclosed approximation, matching Anki's shipped retrievability "
        "display).",
        "- The trained tier refits params on pre-cutoff data only; user "
        "params from the source collection are never used (they saw the "
        "holdout). When training is impossible the defaults tier is the "
        "only row — a calibration test of the shipped default model.",
        f"- log-loss clamps predictions at epsilon {LOG_LOSS_EPSILON}; "
        "Python f64 vs engine f32 differs by ~1e-6.",
        "- Same-day learning steps are handled by the engine's FSRS-6 "
        "short-term memory path (memory states are engine-computed); no "
        "short-term reimplementation exists in this tool.",
        "",
    ]
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any], report_dir: Path
) -> tuple[Path, Path, Path | None]:
    report_dir.mkdir(parents=True, exist_ok=True)
    chart_path: Path | None = None
    # the committed chart prefers real-collection data; self-test data is
    # only charted when that is all this invocation produced
    chart_source = report.get("collection")
    if not chart_source or "tiers" not in chart_source:
        chart_source = report.get("self_test")
    if chart_source and "tiers" in chart_source:
        tiers = [
            {
                "tier": tier["tier"],
                "color": TIER_COLORS[tier["tier"]],
                "bins": tier["metrics"]["bins"],
                "n": tier["metrics"]["n"],
            }
            for tier in chart_source["tiers"]
            if "bins" in tier.get("metrics", {})
        ]
        if tiers:
            label = chart_source.get("path", "self-test synthetic collection")
            headline = chart_source["tiers"][0]["metrics"]
            subtitle = (
                f"{Path(str(label)).name} — n={headline['n']}, "
                f"Brier {headline['brier']} "
                f"(CI {headline['brier_ci95'][0]}–{headline['brier_ci95'][1]}), "
                f"ECE {headline['ece']}"
            )
            warning = None
            if chart_source.get("low_n_warning"):
                warning = (
                    f"LOW N: fewer than {LOW_N_THRESHOLD} held-out "
                    "observations — wide CI, weak evidence"
                )
            elif any(tier["metrics"].get("caveats") for tier in chart_source["tiers"]):
                warning = (
                    "CAVEATS apply (one-sided/degenerate holdout) — see "
                    "memory_calibration_report.md"
                )
            chart_path = report_dir / "memory_calibration_chart.svg"
            chart_path.write_text(
                reliability_svg(
                    tiers,
                    "FSRS memory calibration — held-out reviews",
                    subtitle,
                    warning,
                ),
                encoding="utf-8",
            )
            report["chart"] = str(chart_path)
    json_path = report_dir / "memory_calibration_report.json"
    md_path = report_dir / "memory_calibration_report.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    return json_path, md_path, chart_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--collection",
        help="Anki collection (.anki2). Read-only: the run works on a "
        "truncated copy under --work-dir.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the synthetic end-to-end self-test (default when no "
        "--collection is given)",
    )
    parser.add_argument(
        "--cutoff-quantile",
        type=float,
        default=None,
        help="explicit holdout cutoff quantile in (0,1); default: adaptive "
        f"{DEFAULT_CUTOFF_QUANTILE} with >= {MIN_HOLDOUT_TARGET}-observation "
        "fallback search",
    )
    parser.add_argument(
        "--bootstrap-resamples",
        type=int,
        default=BOOTSTRAP_RESAMPLES,
        help="bootstrap resamples for the Brier CI (default: %(default)s)",
    )
    parser.add_argument(
        "--work-dir",
        default=str(HERE.parents[1] / "out" / "speedrun_eval" / "memcal"),
        help="scratch directory for truncated copies (default: %(default)s)",
    )
    parser.add_argument(
        "--report-dir",
        default=str(HERE / "eval"),
        help="where to write memory_calibration_report.{json,md} + chart "
        "(default: %(default)s)",
    )
    args = parser.parse_args(argv)

    run_self = args.self_test or not args.collection
    work_dir = Path(args.work_dir)
    failures: list[str] = []
    modes = (["self-test"] if run_self else []) + (
        ["collection"] if args.collection else []
    )
    report: dict[str, Any] = {
        "meta": {
            "tool": "memory_calibration",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "modes": modes,
            "formula": {
                "expression": "R(t) = (1 + (0.9**(-1/decay) - 1) * t/S) ** -decay",
                "elapsed_time": "fractional days = (review_ms - prev_ms)/86_400_000",
                "provenance": [
                    "fsrs-5.2.0 src/inference.rs:60-63 current_retrievability",
                    "fsrs-5.2.0 src/inference.rs:512-519 "
                    "current_retrievability_seconds (seconds/86400)",
                    "fsrs-5.2.0 src/model.rs:52-56 power_forgetting_curve",
                    "rslib/src/stats/graphs/retrievability.rs:33-34 "
                    "(engine display path uses the same arithmetic)",
                    "rslib/src/scheduler/fsrs/memory_state.rs:360 "
                    "compute_memory_state (state recomputed from revlog)",
                ],
                "fsrs6_default_decay": FSRS6_DEFAULT_DECAY,
            },
        }
    }

    if run_self:
        try:
            report["self_test"] = run_self_test(work_dir)
            tier_bits = ", ".join(
                f"{tier['tier']}: Brier {tier['metrics']['brier']} "
                f"(n={tier['metrics']['n']})"
                for tier in report["self_test"]["tiers"]
            )
            print(
                f"self-test: OK (seed {SELF_TEST_SEED}; "
                f"{len(report['self_test']['checks_passed'])} checks; "
                f"{tier_bits})"
            )
        except (AssertionError, CalibrationError) as exc:
            failures.append(f"self-test: {exc}")
            report["self_test"] = {"failed": str(exc)}
            print(f"SELF-TEST FAILED: {exc}", file=sys.stderr)
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] != "anki":
                raise
            message = (
                "pylib not importable: collection work needs the built repo "
                "(PYTHONPATH=out/pylib out/pyenv/bin/python ...); module "
                "import and unit tests stay stdlib-only"
            )
            failures.append(f"self-test: {message}")
            report["self_test"] = {"failed": message}
            print(f"SELF-TEST FAILED: {message}", file=sys.stderr)

    if args.collection:
        try:
            collection_report = run_calibration(
                args.collection,
                work_dir,
                cutoff_quantile=args.cutoff_quantile,
                bootstrap_resamples=args.bootstrap_resamples,
            )
            report["collection"] = collection_report
            for tier in collection_report["tiers"]:
                metrics = tier["metrics"]
                print(
                    f"collection [{tier['tier']}]: n={metrics['n']}, "
                    f"Brier {metrics['brier']} "
                    f"(CI {metrics['brier_ci95'][0]}–{metrics['brier_ci95'][1]}), "
                    f"log-loss {metrics['log_loss']}, ECE {metrics['ece']}, "
                    f"constant-baseline Brier "
                    f"{metrics['baselines']['constant_train_rate']['brier']}"
                )
            if collection_report["low_n_warning"]:
                print(
                    f"WARNING: held-out n < {LOW_N_THRESHOLD}; wide CI, weak "
                    "evidence (reported as such)"
                )
        except CalibrationError as exc:
            failures.append(f"collection: {exc}")
            report["collection"] = {"failed": str(exc), "path": args.collection}
            print(f"COLLECTION RUN REFUSED: {exc}", file=sys.stderr)
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] != "anki":
                raise
            message = (
                "pylib not importable: collection work needs the built repo "
                "(PYTHONPATH=out/pylib out/pyenv/bin/python ...)"
            )
            failures.append(f"collection: {message}")
            report["collection"] = {"failed": message, "path": args.collection}
            print(f"COLLECTION RUN REFUSED: {message}", file=sys.stderr)

    report["failures"] = failures
    report["exit_code"] = 1 if failures else 0
    json_path, md_path, chart_path = write_reports(report, Path(args.report_dir))
    chart_note = f" + {chart_path.name}" if chart_path else ""
    print(f"reports: {json_path} + {md_path.name}{chart_note}")
    if failures:
        print("FAILURES: " + "; ".join(failures), file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
