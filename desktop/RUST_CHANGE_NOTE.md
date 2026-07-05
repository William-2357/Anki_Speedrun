# The Rust Change — Contrast Scheduling + TopicMastery RPC + Fade Gating + the Readiness Backend

Phase 1 ships two engine artifacts inside Anki's Rust core (`rslib/`), per
`PHASE1_PLAN_V2.md`; Phase 2 adds the FSRS-driven fade ladder and the
signed confusability gate, per `PHASE2_PLAN_V2.md`; Phase 3 adds the
banded, abstaining Readiness backend (`rslib/src/readiness/`, the
`GetReadiness` RPC) and the readiness-allocation queue pass, per
`PHASE3_PLAN_V2.md`. This note covers what they are, why they belong in
Rust, what was touched upstream, and how future merges look.

## What changed

### 1. Contrast scheduling (SPOV 1 + SPOV 3)

`rslib/src/scheduler/queue/builder/contrast.rs` (new). When the per-deck
`contrast_scheduling` toggle is on, a pass runs inside
`Collection::build_queues` that:

1. batch-loads the gathered notes' tags (one query,
   `get_note_tags_by_id_list`) and derives **confusable clusters** from tags
   under `contrast_tag_prefix` (default `cluster::`), keyed **within** a
   `cfa::topic::*` topic so clusters never span topics (R28);
2. after the new/review piles are merged into the main queue (so the
   intersperser cannot split a pair — grilling C3), reorders the queue so
   same-cluster cards form **adjacent runs of ≤ 4**, with runs from
   different clusters round-robined;
3. never places two templates of the same note adjacently (C10 — that
   would be repetition, not discrimination);
4. is a strict no-op when no usable cluster tags exist (C13) — there is
   deliberately **no** fallback to grouping by arbitrary first tags, which
   would block whole readings together (the Carvalho & Goldstone 2014
   blocking loss).

The pass is a **pure permutation** of the already-gathered queue: no card is
added, dropped, gated or re-dated, so daily counts stay exact, undo is
untouched (queues are transient, rebuilt state), and the collection cannot
corrupt. Toggle + prefix are protobuf fields 47/48 on `DeckConfig.Config`,
mapped through schema11 so they survive the legacy JSON round-trip and sync
to mobile like any other deck option.

### 2a. `ConceptGraph` RPC (StatsService)

`rslib/src/stats/concept_graph.rs` (new). Builds the knowledge-map data for
a deck (or the whole collection): one node per tag with card/studied counts,
mean FSRS retrievability and an answer-difficulty signal (share of graded
answers that were Again/Hard, from the revlog), plus one edge per tag pair
co-occurring on a note. Two SQL passes over the searched set; tag semantics
stay in the frontend. Exposed as `Collection.concept_graph()` in Python and
rendered by the `ts/routes/concept-graph` force-directed page (d3-force),
which colours abstaining nodes grey rather than guessing.

### 3. Fade gating — the worked → faded → solve ladder (SPOV 2, Phase 2)

`rslib/src/scheduler/queue/builder/fade.rs` (new). When the per-deck
`fade_enabled` toggle is on (default **off**) and the collection has a
`speedrun:exam_date` config key, a pass runs **before** `gather_cards()`
that decides, per confusable cluster, which single rung of the
`rung::worked` / `rung::faded` / `rung::solve` ladder today's queue serves:

1. one batch query loads every active-deck card whose note carries a
   `rung::` tag, together with its FSRS memory state (`cards.data`); one
   batch revlog query loads the ladder's graded history — the signal is
   computed **before the gather**, because FSRS state is not in the
   lightweight gather structs (architecture erratum [A2]);
2. the **fade signal** is predicted retrievability **at the exam horizon**,
   computed with `FSRS::current_retrievability_seconds` and the card's own
   fitted `decay` — the same primitive `extract_fsrs_retrievability` uses,
   never the legacy hand-rolled power law (grilling **C1**);
3. a **two-sided hysteresis band** (`fade_up_r` > `fade_down_r`, defaults
   0.90/0.80) moves the served rung up or back; a **spaced-session
   promotion gate** (≥ `promotion_spaced_sessions` distinct days with a
   correct answer, last answer correct, from the revlog) caps how far the
   ladder may advance; **comprehension/fluency preconditions** hold the
   solve rung (and confusable adjacency) until the cluster has a correct
   encoding and clears `fluency_stability_floor`;
4. withheld cards are skipped **bury-style inside
   `add_new_card`/`add_due_card`** — the only place a card can be withheld
   without consuming a `LimitTreeMap` slot ([A1]) — so daily counts stay
   exact with no extra bookkeeping;
5. re-gating is **build-time only**: Anki excludes `Op::AnswerCard` from
   queue rebuilds, so a newly-qualified prerequisite unlocks its dependent
   on the next build, exactly like sibling burying (grilling **C2**);
6. the ladder is scoped to `interactivity::high` clusters when
   `element_interactivity_gate` is on ([R17]); faded cloze siblings are
   introduced one at a time in `fade_order` (mastery-driven by default,
   backward/forward as ablation arms — [R15]); solve notes carrying a
   self-explanation template variant serve exactly one sibling, picked by
   `self_explain_enabled` ([R16], C9 — the flag changes what the learner
   sees, so it is not inert config).

The pass never mutates cards, the revlog or the collection — it only
withholds cards from a transient queue build — so undo and sync semantics
are untouched, and a gated card can never be lost (it reappears on any
later build once its prerequisite qualifies).

### 3b. Signed confusability gate on contrast adjacency (R18, Phase 2)

`contrast.rs` gains the Phase 2 gate: when the per-deck
`contrast_confusable_tag` marker (default `confusable::high` for new
presets; empty = legacy ungated) is set, only clusters whose notes carry
the marker — written by the offline behavioural confusion-mining pass,
never hand-curated — are forced adjacent; merely-similar clusters keep
default SRS spacing (forcing adjacency on low-similarity pairs is a
measured d=0.76 loss). Clusters that failed the fade ladder's fluency
preconditions are likewise not forced adjacent ([R13]).

New deck-config fields (proto 49–58, all mirrored through schema11 and
`QueueSortOptions`): `contrast_confusable_tag`, `fade_enabled`,
`fade_signal`, `fade_up_r`, `fade_down_r`, `promotion_spaced_sessions`,
`fluency_stability_floor`, `fade_order`, `self_explain_enabled`,
`element_interactivity_gate`. Per grilling **C9**, there is deliberately
**no** `format_congruency_mult` field — nothing in the engine reads it, so
format congruency stays an analysis-time factor in the write-up.

### 2. `TopicMastery` RPC (StatsService)

`rslib/src/stats/mastery.rs` (new). One SQL pass joins the searched cards to
their notes and evaluates the existing `extract_fsrs_retrievability` SQLite
UDF per card, aggregating per topic tag: total cards, studied cards (with an
FSRS memory state), cards at/above a **"high recall probability"** threshold
(0.9 — deliberately not called "mastered"), mean and standard deviation of
predicted retrievability. It also returns the collection-wide **graded
review count** (`revlog.ease > 0`) for the dashboard's give-up rule, and
`fsrs_enabled` so the UI abstains instead of substituting proxies (C11).
Exposed to Python as `Collection.topic_mastery()` (C14) and to the web UI
via the mediasrv method allowlist. Phase 3 adds the `probe::held_out`
exclusion (held-out hygiene: the probe bank never feeds Memory or the
coverage it tests), disclosed via `held_out_probe_cards`.

### 4. The Readiness backend + `GetReadiness` RPC (Phase 3)

`rslib/src/readiness/` (new — `mod.rs`, `beta.rs`, `blueprint.rs`,
`probes.rs`). The Readiness estimate and its give-up gate moved out of the
TypeScript display layer into the engine, so **no display layer can bypass
the gate** — an abstaining response carries zeroed numbers.

1. `probes.rs` extracts real outcomes from the revlog: the **first graded
   answer** of each `probe::pool::performance` card, counted only when
   taken **≥ 7 days** after the last graded review of a non-probe card in
   the probe's cluster (never-studied clusters count as delayed; probe
   answers are never study touches). Bounded work: a few tag searches plus
   batched revlog reads over ≤ ~70 probe cards.
2. `beta.rs` is a self-contained ~200-line stats core (Lanczos `ln Γ`,
   regularized incomplete beta, quantile bisection, exact Binomial and
   Beta-Binomial tails) so `rslib` gains **no stats dependency**; unit
   tests cross-check the continued-fraction and direct-summation
   implementations against each other and pinned reference values.
3. `mod.rs` assembles the contract: Jeffreys posterior over (x, n), the
   MPS map (`P(score ≥ MPS)` under `Binomial(180, p)` at the configurable
   `speedrun:passBand`, default [0.68, 0.75]), the corner-evaluated band,
   the width floor / band clamp / 0.85 confidence cap ([R25]), the
   pass/fail call with "too close to call" abstention ([R5]), the [R1]
   gate (300 study reviews / 70% coverage / 50 delayed probes / half-width
   ≤ 0.20), the calibration-history surface
   (`speedrun:readinessCalibration`, written only by the offline harness),
   and the best-next-topic hint with the Ethics tie-break.
4. `blueprint.rs` holds the CFA topic-weight midpoints as **versioned
   fixed priors** — the one deliberately exam-specific corner of `rslib`,
   because the gate that uses them lives here.

Exposed as `Collection.get_readiness()` in Python, `getReadiness` over
mediasrv for the dashboard, and routed on Android via
`pages/PostRequestHandler.kt` so the phone renders the identical backend
band.

### 5. Readiness-optimization allocation (Phase 3, demoted SPOV 4)

`rslib/src/scheduler/queue/builder/allocation.rs` (new). When the per-deck
`readiness_allocation` toggle (field 59, default **off**) is on, the merged
main queue is **stably** re-ordered by `blueprint-weight × (0.8 − topic
recall)` — within-topic credit only ([R8]), user tag→topic map respected,
no cross-topic transfer credit anywhere. Pure permutation at the same seam
as the contrast pass (which runs after it, preserving confusable adjacency
inside the allocation's macro order); fade gating still runs first, so the
precedence is gate → allocate → cluster.

## Why this belongs in Rust, not Python

- **The queue is built in Rust and only in Rust.** `build_queues` gathers,
  limits, buries, sorts and merges entirely inside `rslib`; by the time any
  Python or TypeScript sees cards, the order is already fixed and consumed
  incrementally. A Python-side reorder would have to re-implement gathering
  (limits, burying, day boundaries) or fight the queue cache — and would not
  exist at all on Android, which drives the engine from Kotlin. Placing the
  pass at the `build_queues` seam gives **one implementation for both
  apps**, which is the point of the shared engine.
- **Scale.** The mastery aggregation must stay fast on 50k-card
  collections. In Rust it is one prepared SQL statement using an existing
  deterministic UDF, no per-card round-trips, no data marshalled across the
  FFI. Doing the same from Python means shipping every card's data blob
  over PyO3 and re-implementing the FSRS retrievability formula (already
  wrong once — see grilling C1 — when hand-rolled).
- **Sync correctness for the toggle.** Deck config is engine-owned state
  with a schema11 legacy representation used by the sync protocol and by
  AnkiDroid. Only the Rust layer can add the fields once and have every
  client (desktop, Android, future iOS) read the same values.

## Upstream files touched (merge-difficulty analysis)

| File                                                                                                                | Change                                                                                                         | Future-merge risk                                                                                            |
| ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `proto/anki/deck_config.proto`                                                                                      | +2 fields (47/48), +10 fields (49–58) + 2 enums (Phase 2), +1 field (59, Phase 3)                              | **Low** — additive; risk only if upstream claims the same numbers; renumbering is a one-line fix pre-release |
| `proto/anki/stats.proto`                                                                                            | +2 rpcs, +4 messages (Phase 3: `GetReadiness` request/response + `held_out_probe_cards`)                       | **Low** — additive                                                                                           |
| `rslib/src/scheduler/queue/builder/mod.rs`                                                                          | 13 fields on `QueueSortOptions`/`QueueBuilder`, contrast + fade + allocation hooks in `build()`/`build_queues` | **Medium** — this file changes upstream occasionally; the hooks are ~35 focused lines                        |
| `rslib/src/scheduler/queue/builder/contrast.rs`                                                                     | new file (Phase 2: + confusability/fluency gate on adjacency)                                                  | **None**                                                                                                     |
| `rslib/src/scheduler/queue/builder/fade.rs`                                                                         | new file (Phase 2; Phase 3: + M0 combined-pass test)                                                           | **None**                                                                                                     |
| `rslib/src/scheduler/queue/builder/allocation.rs`                                                                   | new file (Phase 3)                                                                                             | **None**                                                                                                     |
| `rslib/src/readiness/` (4 files)                                                                                    | new module (Phase 3) — no new dependencies                                                                     | **None**                                                                                                     |
| `rslib/src/scheduler/queue/builder/gathering.rs`                                                                    | 2-line bury-style gate check in `add_new_card`/`add_due_card` (Phase 2)                                        | **Low** — tiny, at a stable seam                                                                             |
| `rslib/src/deckconfig/mod.rs`                                                                                       | +13 defaults, band validation, `DEFAULT_CONFUSABLE_TAG`                                                        | **Low**                                                                                                      |
| `rslib/src/deckconfig/schema11.rs`                                                                                  | +13 fields, both From impls, reserved keys                                                                     | **Low** — mechanical, mirrors upstream's own pattern for new fields                                          |
| `rslib/src/stats/{mod,service}.rs`                                                                                  | module + trait impls (Phase 3: + `get_readiness`)                                                              | **Low**                                                                                                      |
| `rslib/src/stats/mastery.rs`                                                                                        | new file (Phase 3: + probe exclusion, pub(crate) map helpers)                                                  | **None**                                                                                                     |
| `rslib/src/storage/card/mod.rs`                                                                                     | +2 read-only query helpers (`FadeLadderCardRow` in Phase 2)                                                    | **Low**                                                                                                      |
| `rslib/src/storage/revlog/mod.rs`                                                                                   | +1 count query, +1 batch read (Phase 2)                                                                        | **Low**                                                                                                      |
| `rslib/src/lib.rs`                                                                                                  | +1 module registration (Phase 3)                                                                               | **Low**                                                                                                      |
| `rslib/src/tests.rs`                                                                                                | test-only `NoteAdder.tags()` builder                                                                           | **None**                                                                                                     |
| `build/ninja_gen/src/{configure,git}.rs`                                                                            | build from a monorepo whose `.git` is at the repo root; skip submodule sync for vendored trees                 | **Low** — build-system only, no runtime effect                                                               |
| `pylib/anki/collection.py`                                                                                          | +`topic_mastery()` / `concept_graph()` / `get_readiness()` wrappers                                            | **Low**                                                                                                      |
| `qt/aqt/{__init__,toolbar,webview,mediasrv,main}.py`, `qt/aqt/speedrun_dashboard.py`                                | dashboard dialog, toolbar link, page/API allowlists (Phase 3: + `get_readiness`), window title                 | **Low** — additive registrations                                                                             |
| `ts/routes/dashboard/*`, `ts/routes/deck-options/{Contrast,Fade,Readiness}Options.svelte`, `DeckOptionsPage.svelte` | new page + options sections (Phase 3: Readiness display is a thin RPC projection)                              | **Low**                                                                                                      |
| `ftl/core/deck-config.ftl`, `ftl/qt/qt-misc.ftl`                                                                    | +9 strings                                                                                                     | **Low** — append-only                                                                                        |

No shipped schema migration was edited; no DB schema bump (edges are tags);
no protobuf field renumbered or removed; the public add-on API is untouched.

## Tests

- **Rust (7):** `contrast::test::{clusters_adjoin_across_new_and_review_piles,
  noop_without_cluster_tags, clusters_do_not_bridge_topics,
  sibling_templates_do_not_adjoin}` and
  `mastery::test::{groups_cards_by_topic_and_abstains_on_unstudied,
  graded_review_count_ignores_manual_entries, scoped_search_and_custom_prefix}`.
- **Rust, Phase 2 (15):** `fade::test::{fade_off_by_default_gates_nothing,
  missing_exam_date_serves_worked_only, unstudied_ladder_starts_at_worked,
  promotion_needs_spaced_sessions_and_high_signal,
  promotion_requires_last_answer_correct,
  signal_inside_band_holds_current_rung, low_signal_falls_back_to_worked,
  fluency_floor_blocks_solve, gated_cards_do_not_consume_limits,
  interactivity_gate_scopes_the_ladder,
  faded_rung_introduces_one_step_at_a_time,
  self_explain_flag_picks_solve_template, exam_date_parsing}` and
  `contrast::test::{confusability_gate_blocks_unmarked_clusters,
  confusability_marker_matches_child_tags}`.
- **Rust, Phase 3 (24):** `readiness::beta::test::*` (7 — the stats core
  cross-checked between independent implementations),
  `readiness::blueprint::test::midpoints_sum_and_aliases_fold`,
  `readiness::probes::test::{no_probes_is_empty,
  delayed_undelayed_and_unanswered_probes_are_partitioned,
  first_answer_is_the_outcome_and_pools_stay_disjoint,
  probe_answers_are_not_study_touches}`,
  `readiness::test::{abstains_by_default_and_names_every_missing_input,
  test_mode_emits_labelled_wide_band_and_keeps_missing_list,
  emits_value_band_when_every_gate_passes,
  near_the_cut_the_width_gate_abstains_even_with_rich_data,
  pass_band_is_configurable_and_calibration_surfaces,
  ethics_tie_break_applies_near_the_boundary}`,
  `allocation::test::{weak_heavy_topics_lead_and_off_is_vanilla,
  priorities_are_within_topic_only,
  user_map_attribution_matches_dashboard_rules,
  contrast_adjacency_survives_allocation}`,
  `fade::test::gate_first_then_cluster_survivors_in_one_pass` (M0), and
  `mastery::test::held_out_probe_cards_are_excluded_and_counted`.
- **Python (5):** `pylib/tests/test_stats.py` — `test_topic_mastery`,
  `test_topic_mastery_tag_map`, `test_concept_graph`, `test_get_readiness`,
  `test_readiness_probe_outcomes` — all through the `Collection` wrapper.
- **Undo (7a proof):** `pylib/tests/test_schedv3.py` —
  `test_speedrun_undo_with_toggles_on` answers through the v3 scheduler with
  contrast + allocation enabled, undoes, and asserts the revlog entry is
  removed, the card's scheduling state is restored byte-for-byte, the card
  re-queues, and the engine DB check reports nothing beyond housekeeping.
- The full `./ninja check` suite (formatters, clippy, mypy, ruff, eslint,
  svelte-check, 394 Rust tests, Python tests, vitest) passes.
- End-to-end: importing the sample deck and flipping the toggle changes the
  built queue (max same-cluster run 7 → 4; cluster-to-cluster switches
  4 → 7) while the card multiset stays identical — pure reordering.
