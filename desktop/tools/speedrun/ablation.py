# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 3 M4 - the rigorous ablation (Anki Speedrun), as a SIMULATION.

================================  DISCLOSURE  ================================
Everything this harness reports is a SIMULATION of a single synthetic learner
model - NOT human data. Results are descriptive, not inferential: there is no
hypothesis test, no cohort, and the learner model itself is n=1 (one set of
hand-chosen equations). The write-up must (and does, below) disclose this
loudly. What the simulation IS good for: checking that each scheduling policy
moves the metrics in the direction its evidence predicts, on equal budget and
identical content, with every arm run through the exact same learner model.
==============================================================================

One command, from ``desktop/``::

    python3 tools/speedrun/ablation.py

Writes ``tools/speedrun/eval/ablation_report.json`` (deterministic given the
CLI parameters) and ``tools/speedrun/eval/ablation_report.md``. Stdlib only -
no pylib, no cargo, no network.

Real-collection observational companion (``--collection PATH``)
----------------------------------------------------------------

``python3 tools/speedrun/ablation.py --collection PATH`` reads a real
collection READ-ONLY (sqlite3 URI ``mode=ro``, never a live profile) and
writes ``eval/ablation_real_report.{json,md}`` instead of simulating. It is
NOT an ablation and never pretends to be one: with a single real learner
there is no counterfactual arm and no random assignment, so the report is
observational only - (1) which Speedrun deck-config toggles are currently on
(contrastScheduling / fadeEnabled / readinessAllocation, schema11 JSON names,
decoded from the deck_config protobuf blobs) and the simulated arm that state
corresponds to; (2) the memory-side outcome family (overall and per
cfa::topic::* retention / again-rate on graded study reviews, study days,
cards touched); (3) delayed held-out probe outcomes under probe_harness's
exact [R7] rule (reused by import, never reimplemented); (4) the
speedrun:readinessCalibration record status; and (5) the same-day
contrast-adjacency share when the feature was on. Every section carries its
n and abstains, with the reason, where the data cannot support a number. The
default no-flag invocation still runs the simulation exactly as before.

The simulated learner model (all constants below, all equations here)
----------------------------------------------------------------------

**Memory (exponential forgetting).** Each item ``i`` has a hidden strength
``S_i`` (days) and a last-study day. Retention after a lag of ``dt`` days::

    r_i(t) = exp(-dt / S_i)          (r = 0 for never-studied items)

A study presentation is graded recall: success probability
``p = r * (1 - STUDY_CONFUSION_WEIGHT * c_i)`` where ``c_i`` is the item's
confusion rate (below; interference bites less when cued by your own card
front than on an application probe). On first exposure the item is encoded
at ``S = S_INIT``; on a later success the spacing effect grows strength by
``S *= 1 + GROWTH * (1 - r)`` (harder retrieval, bigger gain); on failure
``S = max(S_INIT, FAIL_SHRINK * S)``. An item comes due
``max(1, round(DUE_MULT * S))`` days after its last study (a review at
``r ~ exp(-DUE_MULT)``).

**Queue mechanics shared by every arm** (model side, not policy side): each
day the gathered queue is all overdue reviews (oldest due first, ties by a
per-replication arrival order) with up to ``NEW_PER_DAY`` unseen items
merged in proportionally (Anki's daily new limit + MixWithReviews). If the
gathered queue is shorter than the budget, remaining slots study ahead:
not-yet-due items nearest due first, then further unseen items (the cap
yields rather than break the equal-budget invariant - a real learner with
time budgeted would keep studying). Every arm presents exactly ``budget``
cards on every one of ``days`` days; policies only permute or gate this
queue, never change its size.

**Confusable-cluster interference ([R8]).** The true cluster key is
``(topic, family)`` - clusters never bridge topics, mirroring the engine's
``clusters_do_not_bridge_topics`` invariant. Each true cluster ``k`` has a
discrimination level ``d_k`` in [0, 1] (starts 0). A clustered item is
answered wrong by *confusion* (picking its look-alike sibling) at rate::

    c_i = C_MAX * (1 - d_k)     (full weight on probes; STUDY_CONFUSION_WEIGHT on study)

Reviewing two same-true-cluster cards back-to-back (consecutive
presentations within one day) trains discrimination::

    d_k += ADJ_BONUS * (1 - d_k)

with two documented dampers: (1) at most one training event per cluster per
day - further massed same-day pairs add nothing (massed-practice
diminishing returns; long single-category blocks are the d=0.76 loss the
contrast pass explicitly avoids with small interleaved runs), and (2)
discrimination is memory too and decays overnight, ``d_k *= DISC_RETAIN``
per day - so a policy must keep *re-creating* adjacency to hold ``d_k``
high, and the metrics reward sustained contrast, not a lucky burst. A
consecutive pair that merely shares a cluster *name* across two topics gets
NO bonus - the interference reduction only truly exists within topic
(St. Hilaire & Carpenter 2023, general transfer g ~ 0.04; Pan & Rickard
PEESE intercept ~ 0). The ``cross_topic_leakage`` arm deliberately spends
its adjacency slots on such pairs: it keys clusters by family name alone and
interleaves topics inside each run, believing cross-topic contrast works.

**Ladder content (worked -> faded -> solve).** Six synthetic formula
concepts each ship three rung items. A worked example always encodes
(reading, success probability 1). A faded/solve attempt made while its
prerequisite rung's *current recall* is below ``GATE_R`` is *premature* -
the worked-example effect: floundering on a problem without the concept
teaches nothing. A premature attempt succeeds only at ``p * PREMATURE_MULT``
and never updates memory (first exposure encodes weakly at
``S = PREMATURE_ENCODE_S`` so the item exists, comes due, and keeps burning
budget slots daily until its prerequisite clears). The fade arms gate on
exactly this criterion at queue-build time - a locked rung is simply
withheld, mirroring the Rust fade pass's bury-style build-time gating - so
they skip the burn and study the rung once it can actually teach.

**Delayed held-out probes ([R7]).** Probe units: one application probe per
true cluster, per ladder concept (its solve rung), and per topic's
unclustered pool. A probe draw picks (seeded) one member item whose
study->probe lag is >= PROBE_DELAY_DAYS (never-studied counts as infinitely
delayed) and answers a 3-option MCQ::

    p_correct = r * (1 - c) + (1 - r) * 1/3

where ``r`` is that member's recall at probe time and ``c`` the unit's full
confusion rate. Probes never update memory (held-out hygiene). Waves run
every ``PROBE_WAVE_INTERVAL`` days; a unit with no delay-eligible member is
skipped (the [R7] rule). Confusion errors (retrieval succeeded,
discrimination failed) are tracked separately from retrieval failures.

**Readiness gauges.** The strict [R1] gate emits only when graded reviews
>= 300 AND blueprint-weighted topic coverage >= 70% AND delayed held-out
probe outcomes >= 50 AND the posterior half-width <= 0.20. Its estimate is
the Jeffreys posterior over probe outcomes, ``p_hat = (x+0.5)/(n+1)``,
``sigma = sqrt(p_hat(1-p_hat)/(n+1))``, half-width ``1.645*sigma``, mapped
to ``P(pass) = 1 - Phi((MPS_CENTER - p_hat)/sigma)`` (normal approximation
of the posterior at the fixed MPS band center 0.715), with the [R25]
honesty constraints applied: sigma floored so the band half-width never
drops below ``MIN_HALF_WIDTH`` and P(pass) clamped into
``[1 - CONFIDENCE_CAP, CONFIDENCE_CAP]`` (the mock<->exam r~0.7 ceiling).
Probe evidence uses a ``READINESS_WINDOW_DAYS`` recency window - a
simulation-only choice, disclosed: the real probe bank is answered once,
near the exam; a lifetime average would let day-14 evidence pollute a
day-90 claim. The retired lenient gate (>= 15 reviews, >= 1% coverage)
emits the old display's logistic point ``1/(1+exp(-k*(proxy - 0.65)))``
where ``proxy`` is the blueprint-weighted mean recall - an FSRS-recall
proxy with no held-out outcomes behind it, free to run to near-certainty
with no cap. The abstention analysis quantifies the honesty cost of
over-claiming: the fraction of days the lenient gauge emitted while the
strict gauge abstained, and the Brier score of exactly those emissions
against the simulated exam outcome.

**Exam ground truth.** The exam happens PROBE_DELAY_DAYS after the horizon.
Expected blueprint-weighted MCQ score is computed from the hidden state
(no sampling); outcome = score >= MPS_CENTER. Readiness calibration = Brier
of the arm's final strict-gauge P(pass) against that outcome, across
replications.

**Equal budget.** Every arm presents exactly ``budget`` cards on each of
``days`` days - asserted, and reported per arm. The item bank, probe units
and learner model are identical across arms; each replication shares one
arrival-order shuffle across all arms (common random numbers).

**Pre-registered primary comparison (stated ahead of the run):** full_on vs
vanilla on delayed-Performance. Every other number in the report is
exploratory.

Content: the item bank mirrors ``cfa_sample_cards.py`` (the real 72-card
topic/cluster structure), scaled by ITEM_VARIANTS paraphrase variants per
card so the daily budget binds, plus 4 synthetic cross-topic homonym
clusters (the same family name appearing in a second topic - e.g. duration
under both fixed income and equity - exactly what the leakage arm
mis-credits) and 6 worked/faded/solve ladder concepts. Blueprint topic
weights are the CFA 2026 midpoints
(ts/routes/dashboard/cfa_weights_2026.json), total 102.5.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import random
import sqlite3
import statistics
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import probe_harness  # noqa: E402  (read-only import: [R7] outcome extraction)
from cfa_sample_cards import CARDS  # noqa: E402  (data import, see docstring)

SCHEMA = "speedrun-ablation-v1"
HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# defaults & blueprint
# ---------------------------------------------------------------------------

DEFAULT_SEED = 20260704
DEFAULT_DAYS = 90
DEFAULT_BUDGET = 40
DEFAULT_REPLICATIONS = 20

# CFA 2026 blueprint midpoints - mirrors ts/routes/dashboard/cfa_weights_2026.json
BLUEPRINT_MIDPOINTS: dict[str, float] = {
    "ethics": 17.5,
    "quantitative_methods": 7.5,
    "economics": 7.5,
    "financial_statement_analysis": 12.5,
    "corporate_issuers": 7.5,
    "equity_investments": 12.5,
    "fixed_income": 12.5,
    "derivatives": 6.5,
    "alternative_investments": 8.5,
    "portfolio_management": 10.0,
}
TOTAL_WEIGHT = sum(BLUEPRINT_MIDPOINTS.values())  # 102.5

# ---------------------------------------------------------------------------
# learner-model constants (equations in the module docstring)
# ---------------------------------------------------------------------------

S_INIT = 5.0  # strength (days) after first exposure
GROWTH = 3.0  # spacing-effect gain scale on success
FAIL_SHRINK = 0.7  # strength multiplier on failure (floored at S_INIT)
DUE_MULT = 0.3  # due after DUE_MULT * S days (reviews at r ~ 0.74)
NEW_PER_DAY = 12  # daily new-card cap (yields to fill; see docstring)
C_MAX = 0.35  # confusion ceiling for untrained clusters (probes)
STUDY_CONFUSION_WEIGHT = 0.3  # interference weight during study
ADJ_BONUS = 0.25  # discrimination gain per adjacent same-cluster pair
DISC_RETAIN = 0.97  # overnight retention of discrimination (decays like memory)
GUESS_MCQ = 1.0 / 3.0  # 3-option MCQ guess rate (CFA L1)
GATE_R = 0.6  # rung prerequisite recall threshold
PREMATURE_MULT = 0.25  # success-probability penalty for premature attempts
PREMATURE_ENCODE_S = 1.0  # weak first-exposure encode of a premature attempt
ITEM_VARIANTS = 5  # paraphrase variants per real card (budget must bind)

PROBE_DELAY_DAYS = 7  # [R7] minimum study->probe lag
PROBE_WAVE_INTERVAL = 14  # probe waves every 2 weeks
FINAL_PROBE_DRAWS = 3  # draws per unit in the final scoring wave
READINESS_WINDOW_DAYS = 45  # recency window on probe evidence (disclosed)

CONTRAST_CHUNK = 4  # mirror contrast.rs CONTRAST_CHUNK
PERFORMANCE_TARGET = 0.8  # mirror readiness PERFORMANCE_TARGET

# exam / readiness constants (mirror rslib/src/readiness/mod.rs)
MPS_LOW, MPS_HIGH = 0.68, 0.75
MPS_CENTER = round((MPS_LOW + MPS_HIGH) / 2.0, 6)  # 0.715
STRICT_MIN_REVIEWS = 300
STRICT_MIN_COVERAGE = 0.70
STRICT_MIN_PROBES = 50
STRICT_MAX_HALF_WIDTH = 0.20
MIN_HALF_WIDTH = 0.10  # [R25] the band never collapses below this
CONFIDENCE_CAP = 0.85  # [R25] mock<->exam ceiling (1 + r~0.7) / 2
Z_90 = 1.645

# the retired lenient gate (the abstention ablation's other arm)
LENIENT_MIN_REVIEWS = 15
LENIENT_MIN_COVERAGE = 0.01
LENIENT_LOGISTIC_K = 8.0
LENIENT_MPS = 0.65  # the old display's hardcoded MPS center

# synthetic cross-topic homonym clusters: (topic, family shared with another
# topic's real cluster, member count). These give the leakage arm real
# same-name-different-topic pairs to waste adjacency slots on.
SHADOW_CLUSTERS: list[tuple[str, str, int]] = [
    ("equity_investments", "duration", 2),  # equity duration vs fi duration
    ("derivatives", "futures_curves", 2),  # vs alt futures_curves
    ("portfolio_management", "return_measures", 2),  # vs quant return_measures
    ("alternative_investments", "spreads", 2),  # vs fi spreads
]

# synthetic ladder concepts (worked/faded/solve triples), formula-heavy topics
LADDER_CONCEPTS: list[tuple[str, str]] = [
    ("quantitative_methods", "tvm_annuity"),
    ("quantitative_methods", "hypothesis_power"),
    ("fixed_income", "bond_price_ytm"),
    ("fixed_income", "convexity_adjustment"),
    ("financial_statement_analysis", "dupont_decomposition"),
    ("derivatives", "forward_pricing_carry"),
]
RUNGS = ("worked", "faded", "solve")

# ---------------------------------------------------------------------------
# item bank
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    index: int
    topic: str
    family: str | None  # cluster family name (last tag segment), e.g. "duration"
    cluster: tuple[str, str] | None  # true, topic-scoped cluster key [R8]
    rung: str | None  # worked|faded|solve for ladder items
    concept: str | None  # ladder concept id


@dataclass(frozen=True)
class ProbeUnit:
    uid: str
    kind: str  # "cluster" | "ladder" | "topic_pool"
    topic: str
    cluster: tuple[str, str] | None
    item_indices: tuple[int, ...]


def build_item_bank(
    variants: int = ITEM_VARIANTS,
) -> tuple[list[Item], list[ProbeUnit], dict[str, dict[str, int]]]:
    """The shared simulated content: real deck structure x variants, shadow
    homonym clusters, ladder triples. Returns (items, probe_units,
    concept->rung->item index map)."""
    items: list[Item] = []

    def add(
        topic: str, family: str | None, rung: str | None, concept: str | None
    ) -> None:
        cluster = (topic, family) if family else None
        items.append(
            Item(
                index=len(items),
                topic=topic,
                family=family,
                cluster=cluster,
                rung=rung,
                concept=concept,
            )
        )

    # real deck structure (cfa_sample_cards), scaled by paraphrase variants;
    # variants share the true cluster key, so cluster fan-out shape survives
    for _front, _back, topic, cluster_tag in CARDS:
        family = cluster_tag.split("::")[-1] if cluster_tag else None
        for _v in range(variants):
            add(topic, family, None, None)

    # synthetic cross-topic homonym clusters (see module docstring)
    for topic, family, members in SHADOW_CLUSTERS:
        for _m in range(members):
            for _v in range(variants):
                add(topic, family, None, None)

    # ladder triples (not scaled; six concepts are plenty for the fade arm)
    concept_rungs: dict[str, dict[str, int]] = {}
    for topic, concept in LADDER_CONCEPTS:
        concept_rungs[concept] = {}
        for rung in RUNGS:
            concept_rungs[concept][rung] = len(items)
            add(topic, None, rung, concept)

    # probe units: one per true cluster, per ladder concept (solve rung),
    # per topic's unclustered pool - in stable construction order
    units: list[ProbeUnit] = []
    seen_clusters: list[tuple[str, str]] = []
    cluster_members: dict[tuple[str, str], list[int]] = {}
    for item in items:
        if item.cluster:
            if item.cluster not in cluster_members:
                seen_clusters.append(item.cluster)
                cluster_members[item.cluster] = []
            cluster_members[item.cluster].append(item.index)
    for cluster in seen_clusters:
        topic, family = cluster
        units.append(
            ProbeUnit(
                uid=f"cluster:{topic}/{family}",
                kind="cluster",
                topic=topic,
                cluster=cluster,
                item_indices=tuple(cluster_members[cluster]),
            )
        )
    for topic, concept in LADDER_CONCEPTS:
        units.append(
            ProbeUnit(
                uid=f"ladder:{concept}",
                kind="ladder",
                topic=topic,
                cluster=None,
                item_indices=(concept_rungs[concept]["solve"],),
            )
        )
    for topic in BLUEPRINT_MIDPOINTS:
        pool = tuple(
            item.index
            for item in items
            if item.topic == topic and item.cluster is None and item.concept is None
        )
        if pool:
            units.append(
                ProbeUnit(
                    uid=f"topic:{topic}",
                    kind="topic_pool",
                    topic=topic,
                    cluster=None,
                    item_indices=pool,
                )
            )
    return items, units, concept_rungs


# ---------------------------------------------------------------------------
# arms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmSpec:
    name: str
    contrast: str  # "off" | "within_topic" | "cross_topic"
    fade: bool
    allocation: bool
    role: str  # "named" | "internal"
    description: str


def default_arms() -> list[ArmSpec]:
    return [
        ArmSpec(
            "vanilla",
            "off",
            False,
            False,
            "named",
            "Plain due/FIFO order; no speedrun features.",
        ),
        ArmSpec(
            "contrast_on",
            "within_topic",
            False,
            False,
            "named",
            "Same selection as vanilla; same-cluster cards (within topic "
            "only) chunked adjacently, runs of <= 4 (contrastScheduling).",
        ),
        ArmSpec(
            "fade_on",
            "off",
            True,
            False,
            "named",
            "Worked -> faded -> solve gating: later rungs locked until the "
            "prerequisite rung's recall clears the gate (fadeEnabled).",
        ),
        ArmSpec(
            "full_on",
            "within_topic",
            True,
            True,
            "named",
            "Contrast + fade + readiness allocation (topics ordered by "
            "blueprint-weight x recall gap, mirroring allocation.rs).",
        ),
        ArmSpec(
            "cross_topic_leakage",
            "cross_topic",
            False,
            False,
            "named",
            "[R8] ablation arm: adjacency credit applied across topic "
            "boundaries too (family-name cluster key, topics interleaved "
            "inside runs); the model grants no cross-topic benefit, so "
            "those adjacency slots are wasted.",
        ),
        ArmSpec(
            "allocation_on",
            "off",
            False,
            True,
            "internal",
            "Allocation only (for the per-SPOV table).",
        ),
        ArmSpec(
            "full_minus_contrast",
            "off",
            True,
            True,
            "internal",
            "full_on without contrast (per-SPOV table).",
        ),
        ArmSpec(
            "full_minus_fade",
            "within_topic",
            False,
            True,
            "internal",
            "full_on without fade (per-SPOV table).",
        ),
        ArmSpec(
            "full_minus_allocation",
            "within_topic",
            True,
            False,
            "internal",
            "full_on without allocation (per-SPOV table).",
        ),
    ]


# ---------------------------------------------------------------------------
# learner state + model helpers
# ---------------------------------------------------------------------------


class Learner:
    """Hidden true state of the simulated learner (see module docstring)."""

    def __init__(self, n_items: int) -> None:
        self.strengths = [0.0] * n_items
        self.last_study = [0] * n_items
        self.seen = [False] * n_items
        self.discrimination: dict[tuple[str, str], float] = {}
        self._trained_today: set[tuple[str, str]] = set()

    def recall(self, index: int, day: int) -> float:
        if not self.seen[index]:
            return 0.0
        dt = day - self.last_study[index]
        if dt <= 0:
            return 1.0
        return math.exp(-dt / self.strengths[index])

    def confusion(self, item: Item) -> float:
        """Full (probe-time) confusion rate for the item."""
        if item.cluster is None:
            return 0.0
        return C_MAX * (1.0 - self.discrimination.get(item.cluster, 0.0))

    def train_discrimination(self, cluster: tuple[str, str]) -> None:
        """At most one training event per cluster per day (docstring damper 1)."""
        if cluster in self._trained_today:
            return
        self._trained_today.add(cluster)
        d = self.discrimination.get(cluster, 0.0)
        self.discrimination[cluster] = d + ADJ_BONUS * (1.0 - d)

    def end_day(self) -> None:
        """Overnight discrimination decay (docstring damper 2)."""
        self._trained_today.clear()
        for cluster in self.discrimination:
            self.discrimination[cluster] *= DISC_RETAIN


def adjacency_kind(prev: Item | None, cur: Item) -> str | None:
    """Classify a consecutive presentation pair.

    "true"  : same topic-scoped cluster -> discrimination is trained ([R8]).
    "wasted": same cluster *name* in different topics -> no true benefit
              (what the cross_topic_leakage arm spends slots on).
    """
    if prev is None or prev.cluster is None or cur.cluster is None:
        return None
    if prev.cluster == cur.cluster:
        return "true"
    if prev.family == cur.family:
        return "wasted"
    return None


def interleave_topics(members: list[Item]) -> list[Item]:
    """Round-robin a family's members across their topics (what the
    cross_topic_leakage policy believes maximizes contrast). For a
    single-topic family this is the identity."""
    by_topic: dict[str, list[Item]] = {}
    order: list[str] = []
    for item in members:
        if item.topic not in by_topic:
            by_topic[item.topic] = []
            order.append(item.topic)
        by_topic[item.topic].append(item)
    if len(order) == 1:
        return members
    queues = [by_topic[t] for t in order]
    result: list[Item] = []
    position = 0
    while any(queues):
        queue = queues[position % len(queues)]
        if queue:
            result.append(queue.pop(0))
        position += 1
    return result


def apply_contrast(selected: list[Item], mode: str) -> list[Item]:
    """Permute one day's cards so same-cluster cards form adjacent runs of
    <= CONTRAST_CHUNK, runs round-robined across clusters, background cards
    keeping their relative order - mirroring contrast.rs ``apply_contrast``
    (the C10 sibling guard is irrelevant here: an item appears at most once
    per day). ``mode`` picks the cluster key: "within_topic" = the true
    (topic, family) key; "cross_topic" = family name only, which merges
    same-name clusters across topics and interleaves their topics inside
    each run."""

    def key_of(item: Item) -> object | None:
        if item.family is None:
            return None
        return item.cluster if mode == "within_topic" else item.family

    keys = [key_of(item) for item in selected]
    counts = Counter(k for k in keys if k is not None)
    active = {k for k, c in counts.items() if c >= 2}
    if not active:
        return selected

    cluster_order: list[object] = []
    per_cluster: dict[object, list[Item]] = {}
    is_slot: list[bool] = []
    for item, k in zip(selected, keys):
        if k in active:
            if k not in per_cluster:
                per_cluster[k] = []
                cluster_order.append(k)
            per_cluster[k].append(item)
            is_slot.append(True)
        else:
            is_slot.append(False)

    chunk_queues: list[list[list[Item]]] = []
    for k in cluster_order:
        members = per_cluster[k]
        if mode == "cross_topic":
            members = interleave_topics(members)
        chunk_queues.append(
            [
                members[i : i + CONTRAST_CHUNK]
                for i in range(0, len(members), CONTRAST_CHUNK)
            ]
        )
    stream: list[list[Item]] = []
    while True:
        exhausted = True
        for queue in chunk_queues:
            if queue:
                stream.append(queue.pop(0))
                exhausted = False
        if exhausted:
            break

    rebuilt: list[Item] = []
    skip = 0
    stream_pos = 0
    for item, slot in zip(selected, is_slot):
        if not slot:
            rebuilt.append(item)
            continue
        if skip:
            skip -= 1
            continue
        chunk = stream[stream_pos]
        stream_pos += 1
        skip = len(chunk) - 1
        rebuilt.extend(chunk)
    assert len(rebuilt) == len(selected)
    return rebuilt


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _mcq_p(r: float, c: float) -> float:
    return r * (1.0 - c) + (1.0 - r) * GUESS_MCQ


def _child_seed(*parts: object) -> int:
    """Cross-process-deterministic child seed (never Python's hash())."""
    data = ":".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


# ---------------------------------------------------------------------------
# one arm x one replication
# ---------------------------------------------------------------------------


@dataclass
class DayGauge:
    """Morning-of-day readiness gauge snapshot (state after day-1)."""

    day: int
    graded_reviews: int
    coverage: float
    delayed_probes: int
    half_width: float
    gates_pass: dict[str, bool] = field(default_factory=dict)
    strict_emitted: bool = False
    strict_p: float | None = None
    lenient_emitted: bool = False
    lenient_p: float | None = None


@dataclass
class RepResult:
    arm: str
    replication: int
    presentations: int
    memory_plain: float
    memory_weighted: float
    delayed_performance: float
    confusion_error_rate: float
    probe_count: int
    readiness_pred: float
    exam_score: float
    exam_outcome: int
    brier_sq: float
    mean_discrimination: float
    adjacency_true: int
    adjacency_wasted: int
    lenient_days: int
    strict_days: int
    overclaim_days: int
    overclaim_brier: float | None
    strict_first_emit: int | None
    daily: list[DayGauge]


class ArmSimulation:
    def __init__(
        self,
        arm: ArmSpec,
        bank: list[Item],
        units: list[ProbeUnit],
        concept_rungs: dict[str, dict[str, int]],
        seed: int,
        replication: int,
        days: int,
        budget: int,
    ) -> None:
        self.arm = arm
        self.bank = bank
        self.units = units
        self.concept_rungs = concept_rungs
        self.days = days
        self.budget = budget
        self.replication = replication
        # arrival order shared across arms within a replication (common
        # random numbers): the deck arrives in one arbitrary order per rep
        arrival = list(range(len(bank)))
        random.Random(_child_seed(seed, "arrival", replication)).shuffle(arrival)
        self.arrival_pos = [0] * len(bank)
        for pos, index in enumerate(arrival):
            self.arrival_pos[index] = pos
        self.rng = random.Random(_child_seed(seed, "outcomes", arm.name, replication))
        self.learner = Learner(len(bank))
        self.covered_topics: set[str] = set()
        self.presentations = 0
        self.adjacency_true = 0
        self.adjacency_wasted = 0
        # delayed held-out probe outcomes as (day, correct)
        self.probe_outcomes: list[tuple[int, bool]] = []
        # per-topic item lists for the weighted gauges
        self.topic_items: dict[str, list[int]] = {t: [] for t in BLUEPRINT_MIDPOINTS}
        for item in bank:
            self.topic_items[item.topic].append(item.index)
        self.true_clusters = sorted({item.cluster for item in bank if item.cluster})

    # -- model-side ladder rules (arm-independent) --------------------------

    def _prerequisite(self, item: Item) -> int | None:
        if item.concept is None or item.rung == "worked":
            return None
        rungs = self.concept_rungs[item.concept]
        return rungs["worked"] if item.rung == "faded" else rungs["faded"]

    def _premature(self, item: Item, recalls: list[float]) -> bool:
        prereq = self._prerequisite(item)
        if prereq is None:
            return False
        return not self.learner.seen[prereq] or recalls[prereq] < GATE_R

    # -- policy-side queue build --------------------------------------------

    def _topic_priorities(self, recalls: list[float]) -> dict[str, float]:
        """Mirror allocation.rs: (weight/total) x max(0, target - topic mean
        recall over *studied* items); unstudied topics carry the full gap."""
        priorities: dict[str, float] = {}
        for topic, indices in self.topic_items.items():
            studied = [recalls[i] for i in indices if self.learner.seen[i]]
            mean = sum(studied) / len(studied) if studied else 0.0
            priorities[topic] = (BLUEPRINT_MIDPOINTS[topic] / TOTAL_WEIGHT) * max(
                0.0, PERFORMANCE_TARGET - mean
            )
        return priorities

    def _day_queue(self, day: int, recalls: list[float]) -> list[Item]:
        learner = self.learner
        overdue: list[tuple[int, int, Item]] = []  # (due, arrival, item)
        ahead: list[tuple[int, int, Item]] = []  # not yet due
        unseen: list[tuple[int, Item]] = []  # (arrival, item)
        for item in self.bank:
            i = item.index
            if self.arm.fade and self._premature(item, recalls):
                continue  # rung locked until the prerequisite clears GATE_R
            if learner.seen[i]:
                due = learner.last_study[i] + max(
                    1, round(DUE_MULT * learner.strengths[i])
                )
                target = overdue if due <= day else ahead
                target.append((due, self.arrival_pos[i], item))
            else:
                unseen.append((self.arrival_pos[i], item))
        overdue.sort(key=lambda t: (t[0], t[1]))
        ahead.sort(key=lambda t: (t[0], t[1]))
        unseen.sort(key=lambda t: t[0])
        reviews = [item for _due, _arr, item in overdue]
        new_head = [item for _arr, item in unseen[:NEW_PER_DAY]]
        new_rest = [item for _arr, item in unseen[NEW_PER_DAY:]]

        if self.arm.allocation:
            # a pure within-bucket permutation, like the engine pass: due
            # reviews stay due, the new-card allotment stays the same size -
            # allocation only chooses which topics' cards lead each bucket,
            # which under a bounded budget decides what gets studied today
            priorities = self._topic_priorities(recalls)
            reviews.sort(key=lambda item: -priorities[item.topic])
            new_head.sort(key=lambda item: -priorities[item.topic])

        # proportional merge of the new allotment into the reviews
        # (MixWithReviews), so backlog days still introduce some new cards;
        # Bresenham-style error accumulation keeps the ratio even
        merged: list[Item] = []
        m, k = len(reviews), len(new_head)
        ri = ni = 0
        err = 0
        while ri < m or ni < k:
            if ni < k and (ri >= m or err >= m):
                merged.append(new_head[ni])
                ni += 1
                err -= m
            else:
                merged.append(reviews[ri])
                ri += 1
                err += k

        # study-ahead fill keeps the budget exact (equal-budget invariant)
        queue = merged + [item for _due, _arr, item in ahead] + new_rest
        selected = queue[: self.budget]
        if self.arm.contrast != "off":
            selected = apply_contrast(selected, self.arm.contrast)
        return selected

    # -- presentation -------------------------------------------------------

    def _present_day(
        self, day: int, selected: list[Item], recalls: list[float]
    ) -> None:
        learner = self.learner
        prev: Item | None = None
        for item in selected:
            i = item.index
            r = recalls[i]
            premature = item.rung is not None and self._premature(item, recalls)
            if item.rung == "worked":
                correct = True  # reading a worked example always encodes
            else:
                p = r * (1.0 - STUDY_CONFUSION_WEIGHT * learner.confusion(item))
                if premature:
                    p *= PREMATURE_MULT
                correct = self.rng.random() < p
            if not learner.seen[i]:
                # a premature first attempt encodes only weakly (the
                # worked-example effect: floundering teaches little)
                learner.seen[i] = True
                learner.strengths[i] = PREMATURE_ENCODE_S if premature else S_INIT
                learner.last_study[i] = day
                self.covered_topics.add(item.topic)
            elif premature and not correct:
                # a failed premature attempt teaches nothing: no strength
                # or last-study update - the slot is simply burnt
                pass
            elif correct:
                gain = GROWTH * (1.0 - r)
                if premature:
                    gain *= 0.5
                learner.strengths[i] *= 1.0 + gain
                learner.last_study[i] = day
            else:
                learner.strengths[i] = max(S_INIT, FAIL_SHRINK * learner.strengths[i])
                learner.last_study[i] = day
            self.presentations += 1

            kind = adjacency_kind(prev, item)
            if kind == "true":
                learner.train_discrimination(item.cluster)  # type: ignore[arg-type]
                self.adjacency_true += 1
            elif kind == "wasted":
                # [R8]: no cross-topic discrimination exists to train
                self.adjacency_wasted += 1
            prev = item

    # -- probes ---------------------------------------------------------------

    def _eligible_probe_members(self, unit: ProbeUnit, day: int) -> list[int]:
        """Members whose study->probe lag honours [R7] (never-studied counts
        as infinitely delayed)."""
        eligible = []
        for i in unit.item_indices:
            if (
                not self.learner.seen[i]
                or day - self.learner.last_study[i] >= PROBE_DELAY_DAYS
            ):
                eligible.append(i)
        return eligible

    def _draw_probe(self, unit: ProbeUnit, member: int, day: int) -> tuple[bool, bool]:
        """One MCQ draw against a member item. Returns (correct,
        confusion_error). Never updates memory (held-out hygiene)."""
        r = self.learner.recall(member, day)
        c = self.learner.confusion(self.bank[member]) if unit.cluster else 0.0
        if self.rng.random() < r:
            if self.rng.random() < c:
                return False, True  # retrieved, then confused with a sibling
            return True, False
        return self.rng.random() < GUESS_MCQ, False

    def _probe_wave(self, day: int) -> None:
        """One held-out draw per unit with a delay-eligible member; outcomes
        feed the strict readiness gauge."""
        for unit in self.units:
            eligible = self._eligible_probe_members(unit, day)
            if not eligible:
                continue  # would violate the >= 7-day delay rule
            member = eligible[self.rng.randrange(len(eligible))]
            correct, _confused = self._draw_probe(unit, member, day)
            self.probe_outcomes.append((day, correct))

    # -- gauges ---------------------------------------------------------------

    def _weighted_metrics(self, recalls: list[float]) -> tuple[float, float]:
        """(blueprint-weighted mean recall aka proxy, plain mean recall)."""
        weighted = 0.0
        for topic, indices in self.topic_items.items():
            mean = sum(recalls[i] for i in indices) / len(indices)
            weighted += (BLUEPRINT_MIDPOINTS[topic] / TOTAL_WEIGHT) * mean
        plain = sum(recalls) / len(recalls)
        return weighted, plain

    def _coverage(self) -> float:
        return sum(BLUEPRINT_MIDPOINTS[t] for t in self.covered_topics) / TOTAL_WEIGHT

    def _posterior(self, day: int) -> tuple[float, float, float, int]:
        """Jeffreys posterior over recent probe outcomes ->
        (p_hat, sigma, half_width, n)."""
        recent = [
            ok for d, ok in self.probe_outcomes if day - d <= READINESS_WINDOW_DAYS
        ]
        n = len(recent)
        x = sum(recent)
        p_hat = (x + 0.5) / (n + 1)
        sigma = max(math.sqrt(p_hat * (1.0 - p_hat) / (n + 1)), 1e-9)
        return p_hat, sigma, Z_90 * sigma, n

    def _strict_p_pass(self, day: int) -> float:
        """P(pass) under the posterior, with the [R25] honesty constraints:
        the effective band never narrows below MIN_HALF_WIDTH and the call
        never leaves [1 - CONFIDENCE_CAP, CONFIDENCE_CAP] (mock<->exam
        correlation ceiling)."""
        p_hat, sigma, _hw, _n = self._posterior(day)
        sigma = max(sigma, MIN_HALF_WIDTH / Z_90)
        p_pass = 1.0 - _phi((MPS_CENTER - p_hat) / sigma)
        return min(max(p_pass, 1.0 - CONFIDENCE_CAP), CONFIDENCE_CAP)

    def _gauge(self, day: int, proxy: float) -> DayGauge:
        graded = self.presentations
        coverage = self._coverage()
        _p_hat, _sigma, half_width, n_recent = self._posterior(day)
        gates = {
            "graded_reviews": graded >= STRICT_MIN_REVIEWS,
            "coverage": coverage >= STRICT_MIN_COVERAGE,
            "delayed_probes": n_recent >= STRICT_MIN_PROBES,
            "half_width": half_width <= STRICT_MAX_HALF_WIDTH,
        }
        gauge = DayGauge(
            day=day,
            graded_reviews=graded,
            coverage=coverage,
            delayed_probes=n_recent,
            half_width=half_width,
            gates_pass=gates,
        )
        if all(gates.values()):
            gauge.strict_emitted = True
            gauge.strict_p = self._strict_p_pass(day)
        if graded >= LENIENT_MIN_REVIEWS and coverage >= LENIENT_MIN_COVERAGE:
            gauge.lenient_emitted = True
            gauge.lenient_p = 1.0 / (
                1.0 + math.exp(-LENIENT_LOGISTIC_K * (proxy - LENIENT_MPS))
            )
        return gauge

    # -- scoring ----------------------------------------------------------------

    def _final_probes(self, day: int) -> tuple[float, float, int]:
        """Final held-out scoring wave: (accuracy, confusion_error_rate, n).
        At `day` (>= horizon + PROBE_DELAY_DAYS) every member is delay-
        eligible."""
        correct = 0
        confused = 0
        total = 0
        for unit in self.units:
            members = list(unit.item_indices)
            for _draw in range(FINAL_PROBE_DRAWS):
                member = members[self.rng.randrange(len(members))]
                ok, conf = self._draw_probe(unit, member, day)
                correct += int(ok)
                confused += int(conf)
                total += 1
        return correct / total, confused / total, total

    def _exam_score(self, day: int) -> float:
        """Expected blueprint-weighted exam score at the exam day (ground
        truth: the hidden state is known, so no sampling is needed)."""
        score = 0.0
        for topic, indices in self.topic_items.items():
            acc = sum(
                _mcq_p(
                    self.learner.recall(i, day), self.learner.confusion(self.bank[i])
                )
                for i in indices
            ) / len(indices)
            score += (BLUEPRINT_MIDPOINTS[topic] / TOTAL_WEIGHT) * acc
        return score

    # -- main loop ----------------------------------------------------------------

    def run(self) -> RepResult:
        daily: list[DayGauge] = []
        for day in range(self.days):
            recalls = [self.learner.recall(i, day) for i in range(len(self.bank))]
            proxy, _plain = self._weighted_metrics(recalls)
            daily.append(self._gauge(day, proxy))  # morning gauge
            selected = self._day_queue(day, recalls)
            self._present_day(day, selected, recalls)
            if day > 0 and day % PROBE_WAVE_INTERVAL == 0:
                self._probe_wave(day)
            self.learner.end_day()

        # end-of-horizon state (morning after the last study day)
        end_recalls = [self.learner.recall(i, self.days) for i in range(len(self.bank))]
        memory_weighted, memory_plain = self._weighted_metrics(end_recalls)
        # the arm's own readiness prediction, made before the exam
        readiness_pred = self._strict_p_pass(self.days) if self.probe_outcomes else 0.5

        exam_day = (self.days - 1) + PROBE_DELAY_DAYS  # lag >= 7 for everything
        delayed_perf, confusion_rate, probe_count = self._final_probes(exam_day)
        exam_score = self._exam_score(exam_day)
        exam_outcome = int(exam_score >= MPS_CENTER)

        lenient_days = sum(1 for g in daily if g.lenient_emitted)
        strict_days = sum(1 for g in daily if g.strict_emitted)
        overclaims = [g for g in daily if g.lenient_emitted and not g.strict_emitted]
        overclaim_brier = (
            sum((g.lenient_p - exam_outcome) ** 2 for g in overclaims) / len(overclaims)
            if overclaims
            else None
        )
        strict_first = next((g.day for g in daily if g.strict_emitted), None)
        d = self.learner.discrimination
        mean_disc = sum(d.get(c, 0.0) for c in self.true_clusters) / len(
            self.true_clusters
        )

        return RepResult(
            arm=self.arm.name,
            replication=self.replication,
            presentations=self.presentations,
            memory_plain=memory_plain,
            memory_weighted=memory_weighted,
            delayed_performance=delayed_perf,
            confusion_error_rate=confusion_rate,
            probe_count=probe_count,
            readiness_pred=readiness_pred,
            exam_score=exam_score,
            exam_outcome=exam_outcome,
            brier_sq=(readiness_pred - exam_outcome) ** 2,
            mean_discrimination=mean_disc,
            adjacency_true=self.adjacency_true,
            adjacency_wasted=self.adjacency_wasted,
            lenient_days=lenient_days,
            strict_days=strict_days,
            overclaim_days=len(overclaims),
            overclaim_brier=overclaim_brier,
            strict_first_emit=strict_first,
            daily=daily,
        )


def simulate_arm(
    arm: ArmSpec,
    bank: list[Item],
    units: list[ProbeUnit],
    concept_rungs: dict[str, dict[str, int]],
    seed: int,
    replication: int,
    days: int,
    budget: int,
) -> RepResult:
    return ArmSimulation(
        arm, bank, units, concept_rungs, seed, replication, days, budget
    ).run()


# ---------------------------------------------------------------------------
# aggregation + report
# ---------------------------------------------------------------------------


def _r(x: float) -> float:
    return round(float(x), 6)


def _mean_sd(values: list[float]) -> dict[str, float]:
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"mean": _r(mean), "sd": _r(sd)}


def _paired_delta(
    a: list[RepResult], b: list[RepResult], attr: str
) -> dict[str, object]:
    diffs = [getattr(x, attr) - getattr(y, attr) for x, y in zip(a, b)]
    out: dict[str, object] = dict(_mean_sd(diffs))
    out["per_replication"] = [_r(v) for v in diffs]
    return out


def run_ablation(
    seed: int = DEFAULT_SEED,
    days: int = DEFAULT_DAYS,
    budget: int = DEFAULT_BUDGET,
    replications: int = DEFAULT_REPLICATIONS,
) -> dict:
    """Run every arm x replication and assemble the deterministic report dict."""
    bank, units, concept_rungs = build_item_bank()
    arms = default_arms()
    results: dict[str, list[RepResult]] = {}
    for arm in arms:
        results[arm.name] = [
            simulate_arm(arm, bank, units, concept_rungs, seed, rep, days, budget)
            for rep in range(replications)
        ]

    arm_blocks: dict[str, dict] = {}
    for arm in arms:
        reps = results[arm.name]
        presentations = {r.presentations for r in reps}
        assert presentations == {days * budget}, "equal-budget invariant broken"
        overclaim_briers = [
            r.overclaim_brier for r in reps if r.overclaim_brier is not None
        ]
        strict_firsts = [
            r.strict_first_emit for r in reps if r.strict_first_emit is not None
        ]
        arm_blocks[arm.name] = {
            "role": arm.role,
            "description": arm.description,
            "features": {
                # deck-config names: contrastScheduling / fadeEnabled /
                # readinessAllocation ("cross_topic" exists only in this sim)
                "contrast": arm.contrast,
                "fadeEnabled": arm.fade,
                "readinessAllocation": arm.allocation,
            },
            "presentations_per_replication": days * budget,
            "memory": _mean_sd([r.memory_plain for r in reps]),
            "memory_weighted": _mean_sd([r.memory_weighted for r in reps]),
            "delayed_performance": _mean_sd([r.delayed_performance for r in reps]),
            "confusion_error_rate": _mean_sd([r.confusion_error_rate for r in reps]),
            "readiness_brier": _mean_sd([r.brier_sq for r in reps]),
            "readiness_prediction": _mean_sd([r.readiness_pred for r in reps]),
            "exam_score": _mean_sd([r.exam_score for r in reps]),
            "exam_pass_rate": _r(sum(r.exam_outcome for r in reps) / len(reps)),
            "mean_discrimination": _mean_sd([r.mean_discrimination for r in reps]),
            "adjacency": {
                "true_pairs": _mean_sd([float(r.adjacency_true) for r in reps]),
                "wasted_pairs": _mean_sd([float(r.adjacency_wasted) for r in reps]),
            },
            "abstention": {
                "lenient_emit_days": _mean_sd([float(r.lenient_days) for r in reps]),
                "strict_emit_days": _mean_sd([float(r.strict_days) for r in reps]),
                "overclaim_days": _mean_sd([float(r.overclaim_days) for r in reps]),
                "overclaim_fraction": _mean_sd([r.overclaim_days / days for r in reps]),
                "overclaim_brier": (
                    _mean_sd(overclaim_briers) if overclaim_briers else None
                ),
                "strict_first_emit_day": (
                    _mean_sd([float(v) for v in strict_firsts])
                    if strict_firsts
                    else None
                ),
                "strict_emitting_replications": len(strict_firsts),
            },
        }

    primary = {
        "preregistered": True,
        "statement": (
            "Stated ahead of the run: the primary comparison is full_on vs "
            "vanilla on delayed-Performance (held-out probe accuracy >= 7 "
            "simulated days after last study). Every other number in this "
            "report is exploratory."
        ),
        "metric": "delayed_performance",
        "full_on": arm_blocks["full_on"]["delayed_performance"],
        "vanilla": arm_blocks["vanilla"]["delayed_performance"],
        "delta": _paired_delta(
            results["full_on"], results["vanilla"], "delayed_performance"
        ),
        "note": (
            "Descriptive, not inferential: one simulated learner model "
            "(n=1), paired by shared per-replication content order; no "
            "hypothesis test is performed or implied."
        ),
    }

    spov = []
    for feature, solo_arm, minus_arm in (
        ("contrast", "contrast_on", "full_minus_contrast"),
        ("fade", "fade_on", "full_minus_fade"),
        ("allocation", "allocation_on", "full_minus_allocation"),
    ):
        spov.append(
            {
                "feature": feature,
                "vs_vanilla": {
                    "arm": solo_arm,
                    "memory": _paired_delta(
                        results[solo_arm], results["vanilla"], "memory_plain"
                    ),
                    "delayed_performance": _paired_delta(
                        results[solo_arm], results["vanilla"], "delayed_performance"
                    ),
                },
                "within_full_on": {
                    "arm": minus_arm,
                    "memory": _paired_delta(
                        results["full_on"], results[minus_arm], "memory_plain"
                    ),
                    "delayed_performance": _paired_delta(
                        results["full_on"], results[minus_arm], "delayed_performance"
                    ),
                },
            }
        )

    n_clustered = sum(1 for i in bank if i.cluster)
    report = {
        "schema": SCHEMA,
        "generated_by": "tools/speedrun/ablation.py",
        "simulation_disclosure": {
            "is_simulation": True,
            "headline": (
                "SIMULATION ONLY - every number below comes from a synthetic "
                "learner model, not from human study data."
            ),
            "details": [
                "Learner model: single hand-specified exponential-forgetting "
                "model (n=1 learner model); see the module docstring of "
                "ablation.py for the exact equations.",
                "Descriptive, not inferential: no hypothesis tests; "
                "mean +/- SD across seeded replications of the same model.",
                "Content is derived from the real 72-card CFA sample deck's "
                "topic/cluster structure, scaled by synthetic paraphrase "
                "variants, plus synthetic homonym clusters and ladder items.",
            ],
        },
        "config": {
            "seed": seed,
            "days": days,
            "budget_per_day": budget,
            "replications": replications,
            "arms": [arm.name for arm in default_arms()],
            "blueprint_midpoints": {k: _r(v) for k, v in BLUEPRINT_MIDPOINTS.items()},
            "total_blueprint_weight": _r(TOTAL_WEIGHT),
            "mps_band": [_r(MPS_LOW), _r(MPS_HIGH)],
            "mps_center": _r(MPS_CENTER),
            "strict_gate": {
                "min_graded_reviews": STRICT_MIN_REVIEWS,
                "min_weighted_coverage": _r(STRICT_MIN_COVERAGE),
                "min_delayed_probes": STRICT_MIN_PROBES,
                "max_half_width": _r(STRICT_MAX_HALF_WIDTH),
                "min_half_width": _r(MIN_HALF_WIDTH),
                "confidence_cap": _r(CONFIDENCE_CAP),
                "probe_recency_window_days": READINESS_WINDOW_DAYS,
            },
            "lenient_gate": {
                "min_graded_reviews": LENIENT_MIN_REVIEWS,
                "min_weighted_coverage": _r(LENIENT_MIN_COVERAGE),
            },
            "model_constants": {
                "S_INIT": _r(S_INIT),
                "GROWTH": _r(GROWTH),
                "FAIL_SHRINK": _r(FAIL_SHRINK),
                "DUE_MULT": _r(DUE_MULT),
                "NEW_PER_DAY": NEW_PER_DAY,
                "C_MAX": _r(C_MAX),
                "STUDY_CONFUSION_WEIGHT": _r(STUDY_CONFUSION_WEIGHT),
                "ADJ_BONUS": _r(ADJ_BONUS),
                "DISC_RETAIN": _r(DISC_RETAIN),
                "GUESS_MCQ": _r(GUESS_MCQ),
                "GATE_R": _r(GATE_R),
                "PREMATURE_MULT": _r(PREMATURE_MULT),
                "PREMATURE_ENCODE_S": _r(PREMATURE_ENCODE_S),
                "ITEM_VARIANTS": ITEM_VARIANTS,
                "PROBE_DELAY_DAYS": PROBE_DELAY_DAYS,
                "PROBE_WAVE_INTERVAL": PROBE_WAVE_INTERVAL,
                "FINAL_PROBE_DRAWS": FINAL_PROBE_DRAWS,
                "READINESS_WINDOW_DAYS": READINESS_WINDOW_DAYS,
                "CONTRAST_CHUNK": CONTRAST_CHUNK,
                "PERFORMANCE_TARGET": _r(PERFORMANCE_TARGET),
            },
        },
        "item_bank": {
            "total_items": len(bank),
            "clustered_items": n_clustered,
            "true_clusters": len({i.cluster for i in bank if i.cluster}),
            "shadow_homonym_clusters": len(SHADOW_CLUSTERS),
            "ladder_concepts": len(LADDER_CONCEPTS),
            "probe_units": len(units),
            "source": "cfa_sample_cards.py x variants + synthetic shadows/ladders",
        },
        "primary_comparison": primary,
        "exploratory_note": (
            "Everything outside primary_comparison is exploratory and "
            "carries the same simulation caveats."
        ),
        "arms": arm_blocks,
        "spov_contributions": spov,
        "abstention_analysis": {
            "description": (
                "Not a scheduling arm: for each arm's trajectory, what the "
                "retired lenient gate (>=15 reviews, >=1% coverage) would "
                "have emitted vs the strict [R1] gate (>=300 reviews, >=70% "
                "weighted coverage, >=50 delayed probes, half-width <= "
                "0.20). overclaim_fraction = share of simulated days where "
                "lenient emitted a number while strict abstained; "
                "overclaim_brier = Brier of exactly those lenient emissions "
                "against the simulated exam outcome (the honesty cost of "
                "over-claiming)."
            ),
            "headline_arm": "full_on",
            "headline": arm_blocks["full_on"]["abstention"],
        },
        "limitations": [
            "SIMULATION, not human data: all effects are properties of the "
            "documented learner model and its constants.",
            "n=1 learner model: a single set of forgetting/interference "
            "equations; real learners vary in ways this cannot capture.",
            "Descriptive, not inferential: replications share the model, so "
            "SDs describe seed noise, not population uncertainty; no "
            "significance claims are made.",
            "The exam ground truth is the model's own expected score against "
            "a fixed MPS center (0.715); CFA never publishes the MPS.",
            "Probe evidence uses a 45-day recency window (simulation-only "
            "choice, disclosed in config); the shipped gate counts all "
            "answered probes because its bank is answered once, near the "
            "exam.",
            "Probe items are simulated paraphrases of studied material; real "
            "held-out probes (the 30x2 set) are a separate milestone (M3).",
        ],
    }
    return report


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------


def _fmt(cell: dict | None) -> str:
    if cell is None:
        return "-"
    return f"{cell['mean']:.3f} +/- {cell['sd']:.3f}"


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    cfg = report["config"]
    add(
        "# Ablation report - Anki Speedrun Phase 3 M4 (SIMULATED learner - "
        "see ablation_real_report.md for the real-collection observational "
        "companion)"
    )
    add("")
    add(
        "> **SIMULATION - READ THIS FIRST.** "
        + report["simulation_disclosure"]["headline"]
    )
    for detail in report["simulation_disclosure"]["details"]:
        add("> " + detail)
    add(">")
    add(
        f"> seed={cfg['seed']}, days={cfg['days']}, budget={cfg['budget_per_day']}/day, "
        f"replications={cfg['replications']}. All cells are mean +/- SD across "
        "replications. Descriptive, not inferential."
    )
    add("")

    primary = report["primary_comparison"]
    add("## Pre-registered primary comparison (the main number, stated ahead)")
    add("")
    add(primary["statement"])
    add("")
    delta = primary["delta"]
    add(
        f"**full_on {primary['full_on']['mean']:.3f} +/- {primary['full_on']['sd']:.3f} "
        f"vs vanilla {primary['vanilla']['mean']:.3f} +/- {primary['vanilla']['sd']:.3f} "
        f"on delayed-Performance; paired delta = {delta['mean']:+.3f} +/- {delta['sd']:.3f} "
        f"(n={cfg['replications']} replications).**"
    )
    add("")
    add(primary["note"])
    add("")

    add("## Arms (exploratory beyond the primary comparison)")
    add("")
    add(
        "| arm | role | Memory (mean recall) | delayed-Performance | "
        "Readiness Brier | confusion-error rate | exam pass rate |"
    )
    add("|---|---|---|---|---|---|---|")
    for name, block in report["arms"].items():
        add(
            f"| {name} | {block['role']} | {_fmt(block['memory'])} | "
            f"{_fmt(block['delayed_performance'])} | {_fmt(block['readiness_brier'])} | "
            f"{_fmt(block['confusion_error_rate'])} | {block['exam_pass_rate']:.2f} |"
        )
    add("")
    add(
        "Memory = plain mean recall probability over all items at the end of "
        "the horizon (blueprint-weighted variant in the JSON). "
        "delayed-Performance = held-out probe accuracy >= 7 days after last "
        "study. Readiness Brier = (final strict-gauge P(pass) - simulated "
        "outcome)^2, lower is better. Confusion-error rate = share of final "
        "probes answered with a confusable sibling."
    )
    add("")

    add("## Per-SPOV marginal contributions (exploratory)")
    add("")
    add(
        "| feature | vs vanilla: Memory | vs vanilla: delayed-Perf | "
        "within full_on: Memory | within full_on: delayed-Perf |"
    )
    add("|---|---|---|---|---|")
    for row in report["spov_contributions"]:
        vv = row["vs_vanilla"]
        wf = row["within_full_on"]
        add(
            f"| {row['feature']} | {vv['memory']['mean']:+.3f} | "
            f"{vv['delayed_performance']['mean']:+.3f} | {wf['memory']['mean']:+.3f} | "
            f"{wf['delayed_performance']['mean']:+.3f} |"
        )
    add("")
    add(
        '"vs vanilla" = (single-feature arm) - vanilla; "within full_on" = '
        "full_on - (full_on minus that feature). Paired per-replication "
        "deltas; SDs in the JSON."
    )
    add("")

    add("## Cross-topic leakage arm ([R8])")
    add("")
    leak = report["arms"]["cross_topic_leakage"]
    con = report["arms"]["contrast_on"]
    add(
        f"cross_topic_leakage spends adjacency slots on same-name pairs across "
        f"topics (wasted pairs/replication: {leak['adjacency']['wasted_pairs']['mean']:.1f} "
        f"vs contrast_on {con['adjacency']['wasted_pairs']['mean']:.1f}); the model grants "
        f"those pairs no discrimination. Result: delayed-Performance "
        f"{_fmt(leak['delayed_performance'])} vs contrast_on "
        f"{_fmt(con['delayed_performance'])}; trained discrimination "
        f"{_fmt(leak['mean_discrimination'])} vs {_fmt(con['mean_discrimination'])}."
    )
    add("")

    add("## Abstention arm: lenient vs strict gate (the honesty cost)")
    add("")
    add(report["abstention_analysis"]["description"])
    add("")
    add(
        "| arm | lenient emit days | strict emit days | overclaim fraction | "
        "overclaim Brier | strict first emit day |"
    )
    add("|---|---|---|---|---|---|")
    for name, block in report["arms"].items():
        ab = block["abstention"]
        first = ab["strict_first_emit_day"]
        first_cell = f"{first['mean']:.1f}" if first else "never"
        add(
            f"| {name} | {ab['lenient_emit_days']['mean']:.1f} | "
            f"{ab['strict_emit_days']['mean']:.1f} | "
            f"{ab['overclaim_fraction']['mean']:.3f} | "
            f"{_fmt(ab['overclaim_brier'])} | {first_cell} |"
        )
    add("")

    add("## Limitations (read before quoting any number)")
    add("")
    for limitation in report["limitations"]:
        add(f"- {limitation}")
    add("")
    add(
        "Model equations, constants and content derivation: module docstring "
        "of `tools/speedrun/ablation.py`. Engine passes mirrored: "
        "`rslib/src/scheduler/queue/builder/{contrast,fade,allocation}.rs`."
    )
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# real-collection observational mode (--collection; NOT an ablation)
# ---------------------------------------------------------------------------

REAL_SCHEMA = "speedrun-ablation-real-v1"
MS_PER_DAY = probe_harness.MS_PER_DAY

#: Speedrun toggles in the deck-config blob. Keys are the schema11 JSON
#: names (rslib/src/deckconfig/schema11.rs, ``rename_all = "camelCase"``);
#: values are the protobuf field numbers of DeckConfig.Config
#: (proto/anki/deck_config.proto), which is how modern collections store
#: each preset in the deck_config table.
FEATURE_FIELDS: dict[str, int] = {
    "contrastScheduling": 47,
    "fadeEnabled": 50,
    "readinessAllocation": 59,
}
#: Fade-ladder band + contrast tag prefix (disclosure detail, not toggles).
FADE_BAND_FIELDS: dict[str, int] = {"fadeUpR": 52, "fadeDownR": 53}
CONTRAST_TAG_PREFIX_FIELD = 48  # empty string means the "cluster::" default

DEFAULT_ROLLOVER_HOURS = 4  # Anki's default day-cutoff hour (4am local)
UNTAGGED_BUCKET = "(no cfa::topic tag)"
DELETED_BUCKET = "(card deleted)"


def _scan_message(blob: bytes) -> dict[int, list[tuple[int, int | bytes]]]:
    """Minimal protobuf wire-format scan: field number -> [(wire_type,
    value)], varints as int, length-delimited/fixed payloads as bytes.
    Enough to read scalar deck-config fields without a generated proto
    module (this harness stays stdlib-only)."""
    fields: dict[int, list[tuple[int, int | bytes]]] = {}
    pos = 0

    def varint() -> int:
        nonlocal pos
        shift = value = 0
        while True:
            byte = blob[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7

    while pos < len(blob):
        key = varint()
        number, wire = key >> 3, key & 7
        value: int | bytes
        if wire == 0:
            value = varint()
        elif wire == 1:
            value = blob[pos : pos + 8]
            pos += 8
        elif wire == 2:
            length = varint()
            value = blob[pos : pos + length]
            pos += length
        elif wire == 5:
            value = blob[pos : pos + 4]
            pos += 4
        else:  # wire types 3/4 (groups) never appear in anki protos
            raise ValueError(f"unsupported protobuf wire type {wire}")
        fields.setdefault(number, []).append((wire, value))
    return fields


def _last_scalar(
    fields: dict[int, list[tuple[int, int | bytes]]], number: int
) -> int | bytes | None:
    """Last occurrence wins, per protobuf scalar-merge semantics."""
    if number not in fields:
        return None
    return fields[number][-1][1]


def _preset_feature_state(fields: dict[int, list[tuple[int, int | bytes]]]) -> dict:
    """Toggle + disclosure-detail state of one deck-config protobuf blob.
    Absent fields are proto3 defaults (false / 0.0 / '')."""
    features = {
        name: bool(_last_scalar(fields, number))
        for name, number in FEATURE_FIELDS.items()
    }
    prefix = _last_scalar(fields, CONTRAST_TAG_PREFIX_FIELD)
    detail: dict[str, object] = {
        "contrastTagPrefix": prefix.decode() if isinstance(prefix, bytes) else ""
    }
    for name, number in FADE_BAND_FIELDS.items():
        raw = _last_scalar(fields, number)
        detail[name] = (
            _r(struct.unpack("<f", raw)[0]) if isinstance(raw, bytes) else 0.0
        )
    return {"features": features, "detail": detail}


def _schema11_feature_state(conf: dict) -> dict:
    """The same state from a legacy schema11 col.dconf JSON preset (field
    names per rslib/src/deckconfig/schema11.rs; absent means default-off)."""
    features = {name: bool(conf.get(name, False)) for name in FEATURE_FIELDS}
    detail: dict[str, object] = {
        "contrastTagPrefix": str(conf.get("contrastTagPrefix", ""))
    }
    for name in FADE_BAND_FIELDS:
        detail[name] = _r(float(conf.get(name, 0.0)))
    return {"features": features, "detail": detail}


def arm_for_features(contrast: bool, fade: bool, allocation: bool) -> str:
    """The simulated arm whose feature triple matches a real deck-config
    state. The real engine has no cross-topic contrast mode, so every real
    (contrast, fade, allocation) triple maps to exactly one arm."""
    want = ("within_topic" if contrast else "off", fade, allocation)
    for arm in default_arms():
        if arm.contrast == "cross_topic":
            continue
        if (arm.contrast, arm.fade, arm.allocation) == want:
            return arm.name
    raise AssertionError("unreachable: all 8 feature triples map to an arm")


def collection_day(ms: int, mins_west: int, rollover_hours: int) -> int:
    """Collection-local day index of a revlog timestamp: the scheduler's
    day cutoff (rollover hour, minutes-west-of-UTC offset)."""
    return (ms - mins_west * 60_000 - rollover_hours * 3_600_000) // MS_PER_DAY


@dataclass(frozen=True)
class StudyReview:
    """One graded (ease > 0) non-probe revlog row of a real collection."""

    ms: int
    day: int  # collection-local day index
    card_id: int
    deck_id: int | None  # None once the card has been deleted
    ease: int
    topic: str | None  # cfa::topic::* tag suffix, if any
    cluster: str | None  # cluster::* tag suffix, if any
    deleted: bool  # card no longer exists (topic/cluster unknowable)

    @property
    def correct(self) -> bool:
        return self.ease >= 2  # Again(1) = wrong; Hard/Good/Easy = correct


def _unicase(a: str, b: str) -> int:
    """Read-side stand-in for Anki's unicase collation: the decks table
    declares it, and sqlite refuses to even prepare statements against the
    table without the symbol. We never sort by name, so casefold suffices."""
    left, right = a.casefold(), b.casefold()
    return (left > right) - (left < right)


def read_real_collection(path: str | Path) -> dict:
    """Everything the observational report needs, over one READ-ONLY
    connection (sqlite3 URI mode=ro - the file is never mutated). Probe
    identity and tag parsing mirror probe_harness.read_collection."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    con.create_collation("unicase", _unicase)
    try:
        tables = {
            name
            for (name,) in con.execute(
                "select name from sqlite_master where type='table'"
            )
        }

        # -- config keys (modern config table, or legacy col.conf JSON) ----
        if "config" in tables:
            config = {
                key: bytes(val)
                for key, val in con.execute("select key, val from config")
            }
        else:
            (conf_json,) = con.execute("select conf from col").fetchone()
            config = {
                key: json.dumps(value).encode()
                for key, value in json.loads(conf_json or "{}").items()
            }
        mins_west = (
            int(json.loads(config["creationOffset"]))
            if "creationOffset" in config
            else 0
        )
        rollover = (
            int(json.loads(config["rollover"]))
            if "rollover" in config
            else DEFAULT_ROLLOVER_HOURS
        )

        # -- deck-config presets + deck attachment --------------------------
        presets: list[dict] = []
        deck_preset: dict[int, int | None] = {}
        deck_names: dict[int, str] = {}
        if "deck_config" in tables:
            storage = "deck_config table (protobuf DeckConfig.Config blobs)"
            for preset_id, name, blob in con.execute(
                "select id, name, config from deck_config order by id"
            ):
                state = _preset_feature_state(_scan_message(bytes(blob)))
                presets.append({"id": preset_id, "name": name, **state})
            for did, name, kind in con.execute("select id, name, kind from decks"):
                deck_names[did] = name
                normal = _last_scalar(_scan_message(bytes(kind)), 1)  # Deck.kind.normal
                if isinstance(normal, bytes):  # Normal.config_id = 1; 0/absent -> 1
                    config_id = _last_scalar(_scan_message(normal), 1)
                    deck_preset[did] = int(config_id) if config_id else 1
                else:
                    deck_preset[did] = None  # filtered decks carry no preset
        else:
            storage = "col.dconf (legacy schema11 JSON)"
            (dconf_json,) = con.execute("select dconf from col").fetchone()
            for key, conf in sorted(
                json.loads(dconf_json or "{}").items(), key=lambda kv: int(kv[0])
            ):
                state = _schema11_feature_state(conf)
                presets.append({"id": int(key), "name": conf.get("name", ""), **state})
            (decks_json,) = con.execute("select decks from col").fetchone()
            for key, deck in json.loads(decks_json or "{}").items():
                deck_names[int(key)] = deck.get("name", "")
                deck_preset[int(key)] = deck.get("conf")

        # -- notes / cards / revlog -----------------------------------------
        note_tags: dict[int, list[str]] = {}
        for nid, tags in con.execute("select id, tags from notes"):
            note_tags[nid] = [t.lower() for t in str(tags).split()]
        card_info: dict[int, tuple[int, int]] = {
            cid: (nid, did)
            for cid, nid, did in con.execute("select id, nid, did from cards")
        }

        def tag_value(tags: list[str], prefix: str) -> str | None:
            for tag in tags:
                if tag.startswith(prefix) and len(tag) > len(prefix):
                    return tag[len(prefix) :]
            return None

        probe_note_ids = {
            nid
            for nid, tags in note_tags.items()
            if probe_harness.PROBE_HELD_OUT_TAG in tags
        }
        reviews: list[StudyReview] = []
        revlog_rows = ungraded = probe_answers = 0
        for ms, cid, ease in con.execute(
            "select id, cid, ease from revlog order by id"
        ):
            revlog_rows += 1
            if ease <= 0:
                ungraded += 1  # manual/reschedule entries are not answers
                continue
            info = card_info.get(cid)
            if info is None:  # card deleted since; deck/tags unknowable
                reviews.append(
                    StudyReview(
                        ms,
                        collection_day(ms, mins_west, rollover),
                        cid,
                        None,
                        ease,
                        None,
                        None,
                        True,
                    )
                )
                continue
            nid, did = info
            tags = note_tags.get(nid, [])
            if nid in probe_note_ids:
                probe_answers += 1  # probe answers are never study touches
                continue
            reviews.append(
                StudyReview(
                    ms,
                    collection_day(ms, mins_west, rollover),
                    cid,
                    did,
                    ease,
                    tag_value(tags, probe_harness.TOPIC_TAG_PREFIX),
                    tag_value(tags, probe_harness.CLUSTER_TAG_PREFIX),
                    False,
                )
            )

        (n_cards,) = con.execute("select count(*) from cards").fetchone()
        (n_notes,) = con.execute("select count(*) from notes").fetchone()
        return {
            "storage": storage,
            "config": config,
            "mins_west": mins_west,
            "rollover_hours": rollover,
            "presets": presets,
            "deck_preset": deck_preset,
            "deck_names": deck_names,
            "study_reviews": reviews,
            "counts": {
                "cards": n_cards,
                "notes": n_notes,
                "revlog_rows": revlog_rows,
                "ungraded_rows": ungraded,
                "graded_probe_answers": probe_answers,
                "graded_study_reviews": len(reviews),
            },
        }
    finally:
        con.close()


def study_metrics(reviews: list[StudyReview]) -> dict:
    """Memory-side outcome family on graded study reviews: overall and
    per-topic retention (ease > 1 share, probe_harness's correctness rule),
    review counts, study days, cards touched. Sections abstain (None) when
    there is nothing to count."""

    def bucket_of(review: StudyReview) -> str:
        if review.deleted:
            return DELETED_BUCKET
        return review.topic or UNTAGGED_BUCKET

    def rates(subset: list[StudyReview]) -> dict:
        n = len(subset)
        correct = sum(1 for r in subset if r.correct)
        return {
            "n": n,
            "correct": correct,
            "retention": _r(correct / n) if n else None,
            "again_rate": _r(1.0 - correct / n) if n else None,
            "cards": len({r.card_id for r in subset}),
        }

    by_bucket: dict[str, list[StudyReview]] = {}
    for review in reviews:
        by_bucket.setdefault(bucket_of(review), []).append(review)
    # real topics first (alphabetical), then the disclosed catch-all buckets
    order = sorted(by_bucket, key=lambda b: (b.startswith("("), b))

    overall = rates(reviews)
    overall.pop("cards")
    span = None
    if reviews:
        span = [
            datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
            .date()
            .isoformat()
            for ms in (reviews[0].ms, reviews[-1].ms)
        ]
    return {
        **overall,
        "study_days": len({r.day for r in reviews}),
        "cards_touched": len({r.card_id for r in reviews}),
        "cards_touched_live": len({r.card_id for r in reviews if not r.deleted}),
        "first_to_last_review_utc": span,
        "per_topic": {bucket: rates(by_bucket[bucket]) for bucket in order},
    }


def adjacency_metrics(reviews: list[StudyReview]) -> dict:
    """The simulated report's adjacency notion, observed: over consecutive
    same-day pairs of graded study reviews (revlog order), a "true" pair is
    two different cards sharing one cluster tag ([R8] discrimination
    training); a "wasted" pair shares only the family name (last ::
    segment) across different clusters."""
    same_day = true_pairs = wasted_pairs = 0
    prev: StudyReview | None = None
    for review in reviews:
        if prev is not None and prev.day == review.day:
            same_day += 1
            if (
                review.card_id != prev.card_id
                and review.cluster is not None
                and prev.cluster is not None
            ):
                if review.cluster == prev.cluster:
                    true_pairs += 1
                elif review.cluster.split("::")[-1] == prev.cluster.split("::")[-1]:
                    wasted_pairs += 1
        prev = review
    return {
        "same_day_pairs": same_day,
        "true_pairs": true_pairs,
        "wasted_pairs": wasted_pairs,
        "true_share": _r(true_pairs / same_day) if same_day else None,
    }


def analyze_real_collection(path: str | Path) -> dict:
    """Assemble the real-collection OBSERVATIONAL report dict (read-only;
    never an ablation - see the module docstring). Every section carries
    its n and abstains, with the reason, where the data permits nothing."""
    data = read_real_collection(path)
    reviews: list[StudyReview] = data["study_reviews"]
    counts: dict = dict(data["counts"])
    memory = study_metrics(reviews)

    # -- feature states -> the arm the real data observationally matches ----
    reviews_per_preset = Counter(
        data["deck_preset"].get(r.deck_id) for r in reviews if not r.deleted
    )
    presets: list[dict] = []
    for preset in data["presets"]:
        features = preset["features"]
        presets.append(
            {
                **preset,
                "observational_arm": arm_for_features(
                    features["contrastScheduling"],
                    features["fadeEnabled"],
                    features["readinessAllocation"],
                ),
                "decks": sorted(
                    name
                    for did, name in data["deck_names"].items()
                    if data["deck_preset"].get(did) == preset["id"]
                ),
                "graded_study_reviews": reviews_per_preset.get(preset["id"], 0),
            }
        )
    active_arms = {p["observational_arm"] for p in presets if p["graded_study_reviews"]}
    if not active_arms:  # nothing studied yet: fall back to configured state
        active_arms = {p["observational_arm"] for p in presets}
    overall_arm = active_arms.pop() if len(active_arms) == 1 else "mixed"
    n_deleted = sum(1 for r in reviews if r.deleted)
    if overall_arm == "vanilla":
        arm_note = (
            "All Speedrun features are OFF on every preset with review "
            "history, so the real data observationally corresponds to the "
            "vanilla arm of the simulation."
        )
    elif overall_arm == "mixed":
        arm_note = (
            "Reviews span presets with different feature states; no single "
            "simulated arm corresponds (see the per-preset rows)."
        )
    else:
        arm_note = (
            f"Every preset with review history has the same feature state, "
            f"so the real data observationally corresponds to the "
            f"{overall_arm} arm of the simulation."
        )
    feature_states = {
        "storage": data["storage"],
        "presets": presets,
        "observational_arm": overall_arm,
        "arm_note": arm_note,
        "history_caveat": (
            "Feature states are the deck-config values at read time; the "
            "collection stores no toggle history, so past reviews cannot be "
            "re-attributed to past states."
            + (
                f" {n_deleted} graded reviews sit on since-deleted cards and "
                "cannot be attributed to any preset."
                if n_deleted
                else ""
            )
        ),
    }

    # -- delayed held-out probe outcomes (probe_harness, reused by import) --
    probe_data = probe_harness.read_collection(path)
    study_times = {
        cluster: [ms for ms, _ in cluster_reviews]
        for cluster, cluster_reviews in probe_data["study_reviews"].items()
    }
    outcomes = probe_harness.compute_outcomes(probe_data["probes"], study_times)
    inputs = probe_harness.readiness_inputs(outcomes)
    n_delayed_all = sum(pool["delayed"] for pool in outcomes["pools"].values())
    delayed = {
        "source": (
            "probe_harness.read_collection + compute_outcomes (imported, not "
            "reimplemented); delay rule mirrors rslib/src/readiness/probes.rs: "
            "first graded answer, delayed iff >= 7 days after the cluster's "
            "last non-probe study touch, never-studied counts as delayed"
        ),
        "probe_cards": len(probe_data["probes"]),
        "readiness_inputs": inputs,
        "delayed_outcomes_all_pools": n_delayed_all,
        "pools": outcomes["pools"],
    }
    if not probe_data["probes"]:
        delayed["abstained"] = True
        delayed["abstain_reason"] = (
            "0 probe cards in the collection - the held-out probe deck has "
            "never been imported/answered; no real bridge measurement yet"
        )
    elif n_delayed_all == 0:
        delayed["abstained"] = True
        delayed["abstain_reason"] = (
            "0 delayed probe outcomes - no real bridge measurement yet"
        )
    else:
        delayed["abstained"] = False

    # -- readiness-calibration record status --------------------------------
    raw_record = data["config"].get(probe_harness.CALIBRATION_CONFIG_KEY)
    calibration = {
        "config_key": probe_harness.CALIBRATION_CONFIG_KEY,
        "present": raw_record is not None,
        "record": json.loads(raw_record) if raw_record is not None else None,
        "note": (
            "record present (written by probe_harness --apply)"
            if raw_record is not None
            else "no record - probe_harness --apply has never run (it "
            "refuses below 10 delayed calibration-pool outcomes)"
        ),
    }

    # -- contrast adjacency (observational) ---------------------------------
    contrast_presets = [
        f"{p['name']} (id {p['id']})"
        for p in presets
        if p["features"]["contrastScheduling"]
    ]
    contrast_active = [
        p["id"]
        for p in presets
        if p["features"]["contrastScheduling"] and p["graded_study_reviews"]
    ]
    adjacency: dict[str, object] = {
        "contrast_enabled_presets": contrast_presets,
    }
    if contrast_presets and (
        contrast_active or not any(p["graded_study_reviews"] for p in presets)
    ):
        adjacency.update(adjacency_metrics(reviews))
        adjacency["note"] = (
            "Mirrors the simulated report's adjacency notion: true = "
            "same-cluster different-card back-to-back within one day "
            "(trains discrimination, [R8]); wasted = same family name "
            "across different clusters. Current-state caveat above applies."
        )
        adjacency["applicable"] = True
    else:
        adjacency["applicable"] = False
        adjacency["note"] = "not applicable (feature off for all history)"

    n_graded = len(reviews)
    headline = (
        "REAL-COLLECTION OBSERVATIONAL REPORT - this is NOT an ablation: one "
        f"learner, no counterfactual arm, no random assignment; n=1 with "
        f"{n_graded} graded reviews. It cannot estimate the feature effects "
        "the simulation estimates; it exists to ground the simulated claims "
        "in what real usage exists and to be re-run as real data accumulates."
    )
    return {
        "schema": REAL_SCHEMA,
        "generated_by": "tools/speedrun/ablation.py --collection",
        "observational_disclosure": {
            "is_ablation": False,
            "headline": headline,
            "details": [
                "One real learner, one realized history: counterfactual arms "
                "(what vanilla/full_on WOULD have produced on the same days) "
                "do not exist in this data and are not fabricated.",
                "Simulated companion: ablation_report.{json,md} carries the "
                "actual (simulated) ablation with equal-budget arms.",
                "Sections abstain with the reason where the data permits no "
                "number; nothing here is imputed or guessed.",
            ],
        },
        "collection": {
            "path": str(path),
            "opened": "sqlite3 URI mode=ro (read-only)",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "mins_west": data["mins_west"],
            "rollover_hours": data["rollover_hours"],
            **counts,
        },
        "feature_states": feature_states,
        "memory": memory,
        "delayed_performance": delayed,
        "readiness_calibration": calibration,
        "adjacency": adjacency,
        "limitations": [
            "OBSERVATIONAL, not causal: one real learner (n=1), one realized "
            "history; feature effects are NOT estimable from this data.",
            "Feature states are current-state only; Anki keeps no toggle "
            "history in the collection.",
            "Per-topic rows cover only notes tagged cfa::topic::*; untagged "
            "and since-deleted cards are reported in their own buckets, "
            "never guessed into topics.",
            "Retention here is study-side recognition accuracy (ease > 1 "
            "share), not the delayed application performance the held-out "
            "probe bank measures - the two must not be conflated.",
            f"Power: {n_graded} graded reviews over "
            f"{memory['study_days']} study days; every number is descriptive "
            "and should be re-read as data accumulates.",
        ],
    }


# ---------------------------------------------------------------------------
# real-collection markdown rendering
# ---------------------------------------------------------------------------


def _cell(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "-"


def render_real_markdown(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add(
        "# Real-collection observational report - Anki Speedrun Phase 3 "
        "(companion to the SIMULATED ablation)"
    )
    add("")
    add("> **" + report["observational_disclosure"]["headline"] + "**")
    for detail in report["observational_disclosure"]["details"]:
        add("> " + detail)
    add(">")
    col = report["collection"]
    add(
        f"> Collection: `{col['path']}` ({col['opened']}); "
        f"{col['cards']} cards, {col['notes']} notes, "
        f"{col['revlog_rows']} revlog rows. Generated {col['generated_at']}."
    )
    add("")

    features = report["feature_states"]
    add("## Speedrun feature state per deck-config preset")
    add("")
    add(
        "| preset | contrastScheduling | fadeEnabled | readinessAllocation | "
        "observational arm | decks | graded study reviews |"
    )
    add("|---|---|---|---|---|---|---|")
    for preset in features["presets"]:
        f = preset["features"]
        decks = ", ".join(preset["decks"]) if preset["decks"] else "(none)"
        add(
            f"| {preset['name']} (id {preset['id']}) | {f['contrastScheduling']} | "
            f"{f['fadeEnabled']} | {f['readinessAllocation']} | "
            f"{preset['observational_arm']} | {decks} | "
            f"{preset['graded_study_reviews']} |"
        )
    add("")
    add(f"**{features['arm_note']}**")
    add("")
    add(features["history_caveat"])
    add("")

    memory = report["memory"]
    add("## Memory-side outcomes (graded study reviews; probe answers excluded)")
    add("")
    if memory["n"]:
        add(
            f"Overall: {memory['correct']}/{memory['n']} correct - retention "
            f"{_cell(memory['retention'])}, again-rate {_cell(memory['again_rate'])} "
            f"(n={memory['n']}). {memory['study_days']} study days "
            f"({memory['first_to_last_review_utc'][0]} to "
            f"{memory['first_to_last_review_utc'][1]} UTC), "
            f"{memory['cards_touched']} cards touched "
            f"({memory['cards_touched_live']} still in the collection)."
        )
        add("")
        add("| topic | n | retention | again-rate | cards |")
        add("|---|---|---|---|---|")
        for topic, row in memory["per_topic"].items():
            add(
                f"| {topic} | {row['n']} | {_cell(row['retention'])} | "
                f"{_cell(row['again_rate'])} | {row['cards']} |"
            )
        add("")
        add(
            "Retention = share of graded study reviews answered above Again "
            "(ease > 1), the same correctness rule probe_harness applies. "
            "This is recognition-side memory, not delayed application."
        )
    else:
        add("ABSTAIN: 0 graded study reviews - nothing to report yet (n=0).")
    add("")

    delayed = report["delayed_performance"]
    add("## Delayed held-out probe outcomes ([R7], via probe_harness import)")
    add("")
    if delayed["abstained"]:
        add(
            f"ABSTAIN: {delayed['abstain_reason']} (probe cards: {delayed['probe_cards']})."
        )
    else:
        inputs = delayed["readiness_inputs"]
        add(
            f"{delayed['probe_cards']} probe cards; readiness inputs "
            f"x={inputs['x_correct']} of n={inputs['n_delayed']} delayed "
            f"performance-pool outcomes "
            f"({delayed['delayed_outcomes_all_pools']} delayed across both "
            "pools). Per-pool detail in the JSON."
        )
    add("")
    add(delayed["source"] + ".")
    add("")

    calibration = report["readiness_calibration"]
    add("## Readiness-calibration record")
    add("")
    status = "PRESENT" if calibration["present"] else "ABSENT"
    add(f"`{calibration['config_key']}`: {status} - {calibration['note']}.")
    if calibration["record"] is not None:
        add("")
        add(f"Record: `{json.dumps(calibration['record'])}`")
    add("")

    adjacency = report["adjacency"]
    add("## Contrast adjacency (observational)")
    add("")
    if adjacency["applicable"]:
        add(
            f"Of {adjacency['same_day_pairs']} consecutive same-day study-review "
            f"pairs, {adjacency['true_pairs']} were true contrast pairs "
            f"(same cluster, different card; share "
            f"{_cell(adjacency['true_share'])}) and "
            f"{adjacency['wasted_pairs']} were same-name-different-cluster "
            f"(wasted) pairs. Contrast-enabled presets: "
            f"{', '.join(adjacency['contrast_enabled_presets'])}."
        )
        add("")
        add(adjacency["note"])
    else:
        add(f"{adjacency['note']}.")
    add("")

    add("## What this report cannot say (read before quoting any number)")
    add("")
    for limitation in report["limitations"]:
        add(f"- {limitation}")
    add("")
    add(
        "Feature-state field names: `rslib/src/deckconfig/schema11.rs` / "
        "`proto/anki/deck_config.proto`. Outcome extraction: "
        "`tools/speedrun/probe_harness.py` (imported). The ablation itself "
        "(simulated): `ablation_report.md`."
    )
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--replications", type=int, default=DEFAULT_REPLICATIONS)
    parser.add_argument(
        "--collection",
        help="Anki collection (.anki2) to read READ-ONLY; writes the "
        "observational companion ablation_real_report.{json,md} instead of "
        "running the simulation (see module docstring: NOT an ablation)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(HERE / "eval"),
        help="where ablation_report.{json,md} are written (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    if args.collection:
        return _real_collection_main(args.collection, Path(args.output_dir))

    start = time.monotonic()
    report = run_ablation(
        seed=args.seed,
        days=args.days,
        budget=args.budget,
        replications=args.replications,
    )
    elapsed = time.monotonic() - start

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ablation_report.json"
    md_path = out_dir / "ablation_report.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    primary = report["primary_comparison"]
    print(
        "PRIMARY (pre-registered): full_on vs vanilla on delayed-Performance: "
        f"{primary['full_on']['mean']:.3f} vs {primary['vanilla']['mean']:.3f} "
        f"(paired delta {primary['delta']['mean']:+.3f} +/- {primary['delta']['sd']:.3f})"
    )
    print("SIMULATION ONLY - descriptive, not inferential (see report).")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"runtime: {elapsed:.1f}s")
    return 0


def _real_collection_main(collection: str, out_dir: Path) -> int:
    """The --collection entry point: observational companion, one command."""
    report = analyze_real_collection(collection)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ablation_real_report.json"
    md_path = out_dir / "ablation_real_report.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_real_markdown(report), encoding="utf-8")

    memory = report["memory"]
    delayed = report["delayed_performance"]
    print(
        "OBSERVATIONAL (not an ablation): feature state -> "
        f"{report['feature_states']['observational_arm']} arm; "
        f"{memory['n']} graded study reviews over {memory['study_days']} days, "
        f"overall retention {memory['retention']}"
    )
    if delayed["abstained"]:
        print(f"delayed probes: ABSTAIN - {delayed['abstain_reason']}")
    else:
        inputs = delayed["readiness_inputs"]
        print(
            f"delayed probes: x={inputs['x_correct']} of n={inputs['n_delayed']} "
            "delayed performance-pool outcomes"
        )
    print(
        "calibration record: "
        + ("present" if report["readiness_calibration"]["present"] else "absent")
    )
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
