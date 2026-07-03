# The Rust Change — Contrast Scheduling + TopicMastery RPC + Fade Gating

Phase 1 ships two engine artifacts inside Anki's Rust core (`rslib/`), per
`PHASE1_PLAN_V2.md`; Phase 2 adds the FSRS-driven fade ladder and the
signed confusability gate, per `PHASE2_PLAN_V2.md`. This note covers what
they are, why they belong in Rust, what was touched upstream, and how
future merges look.

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
via the mediasrv method allowlist.

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

| File                                                                                               | Change                                                                                            | Future-merge risk                                                                                            |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `proto/anki/deck_config.proto`                                                                     | +2 fields (47/48), +10 fields (49–58) + 2 enums (Phase 2)                                         | **Low** — additive; risk only if upstream claims the same numbers; renumbering is a one-line fix pre-release |
| `proto/anki/stats.proto`                                                                           | +1 rpc, +2 messages                                                                               | **Low** — additive                                                                                           |
| `rslib/src/scheduler/queue/builder/mod.rs`                                                         | 12 fields on `QueueSortOptions`/`QueueBuilder`, contrast + fade hooks in `build()`/`build_queues` | **Medium** — this file changes upstream occasionally; the hooks are ~30 focused lines                        |
| `rslib/src/scheduler/queue/builder/contrast.rs`                                                    | new file (Phase 2: + confusability/fluency gate on adjacency)                                     | **None**                                                                                                     |
| `rslib/src/scheduler/queue/builder/fade.rs`                                                        | new file (Phase 2)                                                                                | **None**                                                                                                     |
| `rslib/src/scheduler/queue/builder/gathering.rs`                                                   | 2-line bury-style gate check in `add_new_card`/`add_due_card` (Phase 2)                           | **Low** — tiny, at a stable seam                                                                             |
| `rslib/src/deckconfig/mod.rs`                                                                      | +12 defaults, band validation, `DEFAULT_CONFUSABLE_TAG`                                           | **Low**                                                                                                      |
| `rslib/src/deckconfig/schema11.rs`                                                                 | +12 fields, both From impls, reserved keys                                                        | **Low** — mechanical, mirrors upstream's own pattern for new fields                                          |
| `rslib/src/stats/{mod,service}.rs`                                                                 | module + trait impl                                                                               | **Low**                                                                                                      |
| `rslib/src/stats/mastery.rs`                                                                       | new file                                                                                          | **None**                                                                                                     |
| `rslib/src/storage/card/mod.rs`                                                                    | +2 read-only query helpers (`FadeLadderCardRow` in Phase 2)                                       | **Low**                                                                                                      |
| `rslib/src/storage/revlog/mod.rs`                                                                  | +1 count query, +1 batch read (Phase 2)                                                           | **Low**                                                                                                      |
| `rslib/src/tests.rs`                                                                               | test-only `NoteAdder.tags()` builder                                                              | **None**                                                                                                     |
| `build/ninja_gen/src/{configure,git}.rs`                                                           | build from a monorepo whose `.git` is at the repo root; skip submodule sync for vendored trees    | **Low** — build-system only, no runtime effect                                                               |
| `pylib/anki/collection.py`                                                                         | +`topic_mastery()` wrapper                                                                        | **Low**                                                                                                      |
| `qt/aqt/{__init__,toolbar,webview,mediasrv,main}.py`, `qt/aqt/speedrun_dashboard.py`               | dashboard dialog, toolbar link, page/API allowlists, window title                                 | **Low** — additive registrations                                                                             |
| `ts/routes/dashboard/*`, `ts/routes/deck-options/ContrastOptions.svelte`, `DeckOptionsPage.svelte` | new page + options section                                                                        | **Low**                                                                                                      |
| `ftl/core/deck-config.ftl`, `ftl/qt/qt-misc.ftl`                                                   | +6 strings                                                                                        | **Low** — append-only                                                                                        |

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
- **Python (1):** `pylib/tests/test_stats.py::test_topic_mastery`, which
  drives the RPC through the `Collection` wrapper.
- The full `./ninja check` suite (formatters, clippy, mypy, ruff, eslint,
  svelte-check, 347 Rust tests, 45 Python tests, vitest) passes.
- End-to-end: importing the sample deck and flipping the toggle changes the
  built queue (max same-cluster run 7 → 4; cluster-to-cluster switches
  4 → 7) while the card multiset stays identical — pure reordering.
