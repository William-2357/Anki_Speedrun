# The Rust Change — Contrast Scheduling + TopicMastery RPC

Phase 1 ships two engine artifacts inside Anki's Rust core (`rslib/`), per
`PHASE1_PLAN_V2.md`. This note covers what they are, why they belong in
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

| File                                                                                               | Change                                                                                         | Future-merge risk                                                                                            |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `proto/anki/deck_config.proto`                                                                     | +2 fields (47/48)                                                                              | **Low** — additive; risk only if upstream claims the same numbers; renumbering is a one-line fix pre-release |
| `proto/anki/stats.proto`                                                                           | +1 rpc, +2 messages                                                                            | **Low** — additive                                                                                           |
| `rslib/src/scheduler/queue/builder/mod.rs`                                                         | 2 fields on `QueueSortOptions`/`QueueBuilder`, contrast hook in `build()`/`build_queues`       | **Medium** — this file changes upstream occasionally; the hook is ~20 focused lines                          |
| `rslib/src/scheduler/queue/builder/contrast.rs`                                                    | new file                                                                                       | **None**                                                                                                     |
| `rslib/src/deckconfig/mod.rs`                                                                      | +2 defaults                                                                                    | **Low**                                                                                                      |
| `rslib/src/deckconfig/schema11.rs`                                                                 | +2 fields, both From impls, reserved keys                                                      | **Low** — mechanical, mirrors upstream's own pattern for new fields                                          |
| `rslib/src/stats/{mod,service}.rs`                                                                 | module + trait impl                                                                            | **Low**                                                                                                      |
| `rslib/src/stats/mastery.rs`                                                                       | new file                                                                                       | **None**                                                                                                     |
| `rslib/src/storage/card/mod.rs`                                                                    | +1 read-only query helper                                                                      | **Low**                                                                                                      |
| `rslib/src/storage/revlog/mod.rs`                                                                  | +1 count query                                                                                 | **Low**                                                                                                      |
| `rslib/src/tests.rs`                                                                               | test-only `NoteAdder.tags()` builder                                                           | **None**                                                                                                     |
| `build/ninja_gen/src/{configure,git}.rs`                                                           | build from a monorepo whose `.git` is at the repo root; skip submodule sync for vendored trees | **Low** — build-system only, no runtime effect                                                               |
| `pylib/anki/collection.py`                                                                         | +`topic_mastery()` wrapper                                                                     | **Low**                                                                                                      |
| `qt/aqt/{__init__,toolbar,webview,mediasrv,main}.py`, `qt/aqt/speedrun_dashboard.py`               | dashboard dialog, toolbar link, page/API allowlists, window title                              | **Low** — additive registrations                                                                             |
| `ts/routes/dashboard/*`, `ts/routes/deck-options/ContrastOptions.svelte`, `DeckOptionsPage.svelte` | new page + options section                                                                     | **Low**                                                                                                      |
| `ftl/core/deck-config.ftl`, `ftl/qt/qt-misc.ftl`                                                   | +6 strings                                                                                     | **Low** — append-only                                                                                        |

No shipped schema migration was edited; no DB schema bump (edges are tags);
no protobuf field renumbered or removed; the public add-on API is untouched.

## Tests

- **Rust (7):** `contrast::test::{clusters_adjoin_across_new_and_review_piles,
  noop_without_cluster_tags, clusters_do_not_bridge_topics,
  sibling_templates_do_not_adjoin}` and
  `mastery::test::{groups_cards_by_topic_and_abstains_on_unstudied,
  graded_review_count_ignores_manual_entries, scoped_search_and_custom_prefix}`.
- **Python (1):** `pylib/tests/test_stats.py::test_topic_mastery`, which
  drives the RPC through the `Collection` wrapper.
- The full `./ninja check` suite (formatters, clippy, mypy, ruff, eslint,
  svelte-check, 347 Rust tests, 45 Python tests, vitest) passes.
- End-to-end: importing the sample deck and flipping the toggle changes the
  built queue (max same-cluster run 7 → 4; cluster-to-cluster switches
  4 → 7) while the card multiset stays identical — pure reordering.
