# Anki Speedrun — CFA Level I study app (desktop + Android)

**Exam (stated up front): CFA Level I** — pass/fail; 180 standalone A/B/C
multiple-choice questions; 10 weighted topic areas; fact-, formula- and
ethics-heavy. Everything in this repository is built for that one exam.

A fork of [Anki](https://apps.ankiweb.net) (AGPL-3.0-or-later, credit to
Ankitects and contributors) and [AnkiDroid](https://github.com/ankidroid/Anki-Android)
that turns a spaced-repetition app into a **transfer-and-readiness** engine:

- **Contrast scheduling** (the Phase 1 Rust change): the study queue is
  reordered so confusable cards — the Macaulay/modified/effective duration
  trio, FIFO/LIFO/weighted-average, forwards/futures/swaps, neighbouring
  Ethics Standards — appear **back-to-back**, so the interference between
  them becomes the lesson. Pure reordering, per-deck toggle, no-op without
  curated cluster tags.
- **Per-topic mastery RPC**: a new `TopicMastery` backend call aggregates
  FSRS recall per topic tag in one SQL pass, powering the dashboard.
- **Concept map**: a force-directed knowledge graph (`ConceptGraph` RPC) —
  one node per tag, edges where two tags co-occur on a note, coloured by
  behavioural answer difficulty (Again/Hard share) with a toggle for FSRS
  recall. The layout is settled before first paint (no load-in jitter);
  tags attributed to a topic (canonical `cfa::topic::*` tags or the
  dashboard's tag→topic map, same read-time rules as the dashboard)
  cluster under a topic heading, and the remaining tags group by note
  co-occurrence, each island on its own packed centre. Desktop: a deck's
  gear menu, or `/concept-graph` in the dev server. Android: long-press a
  deck → **Concept map** (the same Svelte page, served from the `.aar`
  on-device).
- **An honest readiness dashboard**: three separate gauges — **Memory**,
  **Performance**, **Readiness** — each with a range, its reasons, and a
  written **give-up rule**. Readiness abstains until it has real evidence.
  The gauges themselves use no AI — they read only FSRS state and the
  review log (an optional, off-by-default assistant can *narrate* them, but
  never feeds them; see below). Desktop: toolbar → **CFA Dashboard**.
  Android: deck list overflow menu → **CFA Dashboard** (the same Svelte
  page, served from the `.aar` on-device).
- **A banded, two-number, abstaining Readiness backend** (the Phase 3 Rust
  change): the Readiness math and its give-up gate moved out of the display
  layer into `rslib/src/readiness/` (a new `GetReadiness` RPC). The
  estimate is a **Beta-Binomial band over delayed held-out probe
  outcomes** (Jeffreys prior, mapped through a configurable unpublished-MPS
  band, half-width floored, certainty capped at the mock↔exam r≈0.7
  ceiling) plus a **second honest number** — the confidence of the
  pass/fail call, which abstains "too close to call" when the band
  straddles 50%. The gate (≥300 graded study reviews, ≥70% weighted
  coverage, ≥50 **delayed** probes, band half-width ≤0.20) is enforced in
  the engine — an abstaining response carries zeroed numbers, so no
  display bug can leak an unearned probability. The full honesty contract
  (evidence, missing inputs, calibration history, band, best next topic —
  with a documented Ethics tie-break near the boundary) renders even while
  abstaining, on both apps.
- **A held-out delayed-probe bank + one-command harness** (Phase 3): 35
  concepts × 2 hand-authored, reworded application MCQs, split into
  concept-disjoint **performance** and **calibration** pools
  (`probe::pool::*` tags), leakage-scanned against the corpus and the
  generated items. Probe outcomes only count when answered **≥7 days**
  after their cluster was last studied (measured from the revlog — the
  delay is measured, never claimed). Probe cards are excluded from Memory
  and coverage (the instrument never feeds the gauges it tests), and the
  harness reports the memory→performance bridge gap, calibration
  (Brier/log-loss + fitted temperature) on the calibration pool only, and
  writes the calibration record the dashboard surfaces.
- **Readiness-optimization allocation** (Phase 3, demoted SPOV 4): an
  off-by-default deck-options toggle that stably re-orders the day's
  merged queue by **exam-weight × topic recall gap** (CFA blueprint
  midpoints as fixed priors, within-topic credit only — no cross-topic
  transfer credit anywhere). Pure permutation: limits and counts stay
  exact, and the contrast pass still enforces confusable adjacency inside
  the allocation's macro order.
- **The fade ladder** (the Phase 2 Rust change, SPOV 2): within a cluster,
  cards tagged `rung::worked` → `rung::faded` → `rung::solve` form a
  worked-example ladder, and the queue builder serves **exactly one rung
  per cluster per day**, positioned by **FSRS predicted recall at the exam
  horizon** with a two-sided hysteresis band, a spaced-session promotion
  gate, and comprehension/fluency preconditions. Withheld cards are skipped
  bury-style (limits stay exact, nothing is lost) and re-gating happens at
  the next queue build. Default **off**; per-deck toggle + tunables in deck
  options; needs an exam date (set on the dashboard).
- **A signed confusability gate** (Phase 2, R18): contrast adjacency is now
  forced only for clusters carrying a `confusable::high` marker written by
  an offline **behavioural confusion-mining** pass over the review log —
  never hand-curated — because forcing adjacency on merely-similar material
  measurably hurts. Empty marker = the legacy ungated behaviour.
- **A fully-automated authoring pipeline** (Phase 2, authoring-time only —
  the review loop stays AI-free by construction): parameterized numeric
  generators with misconception-grounded distractors (formulas emitted as
  **MathJax**, which Anki typesets natively on desktop and AnkiDroid),
  machine validation gates (independent recomputation, self-consistency
  solve-check, critic model hooks, leakage wall), and
  retrieval-for-grounding that attaches a **named source passage** to every
  item. The guaranteed retrieval arm is stdlib BM25; the dense + reranker
  arms are **opt-in** (`SPEEDRUN_DENSE=1` — the torch stack is ABI-fragile),
  with the full-stack eval that did run archived under
  `desktop/tools/speedrun/eval/archive/`. Generated items ship tagged
  `aig::ungraded`: they may be studied but **never feed readiness**, and
  non-discriminating items are auto-retired from live responses.
- **An optional runtime assistant layer** (the newest addition, and the
  first AI that runs at **study time** rather than authoring time —
  desktop-only, **default-off**): three read-only helpers that sit
  **outside the review loop** — a post-session **error-pattern debrief**, a
  **study coach** ("what should I do today?"), and an AI **tag→topic
  suggester** for the dashboard's Map-tags editor. Each is
  **grounded-or-abstain** — it may only restate numbers the app already
  computed (never invents a score, and never states a pass probability
  while Readiness is abstaining) — each **falls back to the existing
  deterministic view** when AI is off, offline, or unsure, and none of them
  ever writes to grading, scheduling, or the readiness gauges. Backends are
  pluggable (an offline **mock** by default; `claude-cli` or any
  OpenAI-compatible endpoint opt-in). The **review loop stays AI-free by
  construction**, and on Android the AI affordances simply don't render.

The learning-science grounding (interleaving as a discrimination trainer,
transfer-of-testing moderators, the boundary conditions) lives in
[`desktop/brainlift.md`](desktop/brainlift.md); the product requirements in
[`desktop/PRD.md`](desktop/PRD.md); the phase plans in
`desktop/PHASE*_PLAN*.md`.

---

## Repository structure

```
anki-speedrun/
├── desktop/           # Anki fork — Rust engine (rslib/) + Python/Qt + Svelte web UI
├── android/           # AnkiDroid fork (Kotlin app)
├── android-backend/   # rsdroid — builds the Rust engine into an Android .aar
│   └── anki -> ../desktop   (symlink: ONE engine source for both apps)
```

One engine: `desktop/rslib` is compiled into the desktop app via PyO3 and
into the Android app via the rsdroid `.aar` (the `android-backend/anki`
symlink points at `desktop/`), so the contrast pass, the mastery RPC, and
the deck-config toggle ship to **both** apps from the same source. The
toggle itself syncs as ordinary deck config.

Engine baseline: anki `26.05b1` (the tag rsdroid `0.1.65-anki26.05b1`
targets), AnkiDroid main line.

## Building the desktop app

Prerequisites: git, a C toolchain, [rustup](https://rustup.rs), and Ninja
(`brew install ninja` / `apt install ninja-build`). Node, protoc, uv and
Python are downloaded automatically by the build.

```bash
cd desktop
./run                    # build + launch the app (./run.bat on Windows)
./ninja check            # formatters, linters, and all Rust/Python/TS tests
```

The Speedrun additions in the app:

- Toolbar → **Dashboard** opens the CFA readiness dashboard
  (`?readinessTest=1` on the page URL enables the loudly-labelled test mode).
  Phase 2 adds two controls there: an **exam date** field (drives the fade
  ladder's horizon; unset = fading disabled) and a **Map tags** editor that
  maps any deck's raw tags onto the 10 CFA topics (read-time only, synced
  collection config, never rewrites note tags; unmapped tags stay visible
  as coverage gaps).
- Deck options → **Contrast Scheduling (Speedrun)** toggles the contrast
  pass per deck preset and sets the cluster tag prefix (default `cluster::`)
  plus, in Phase 2, the **confusability marker tag** gating adjacency.
- Deck options → **Fade Ladder (Speedrun)** turns on worked → faded → solve
  gating per preset, with the fade signal, hysteresis band, promotion
  sessions, fluency floor, fade order, self-explanation variant, and the
  element-interactivity scope as tunables (all synced deck config).

### The runtime assistant layer (optional, desktop-only)

Everything above is deterministic. The assistant layer adds three optional,
**default-off** AI helpers on the dashboard page that only ever *read* the
numbers the app already computed and *narrate / suggest* — they never touch
grading, scheduling, or the readiness gauges (full spec:
[`desktop/RUNTIME_AI_PLAN.md`](desktop/RUNTIME_AI_PLAN.md)):

- **Session debrief** — turns the trailing session's mistakes into a short
  pattern narrative (topics missed, confusable pairs that co-occurred,
  misconceptions behind missed MCQs). A deterministic table always renders;
  the AI narration abstains below three mistakes or on any ungrounded reply.
- **Study coach** — a grounded "what should I do today?" built from the
  dashboard model + days-to-exam. It **defers to the gauge**: while
  Readiness abstains it echoes the abstention reasons and never states a
  pass probability.
- **Tag→topic suggester** — an "AI suggest" button in the dashboard's
  Map-tags editor that *pre-fills* dropdowns for unmapped tags (low
  confidence is left blank); **nothing persists until you click Save**, so
  the deterministic map semantics are untouched.

Turn them on from the dashboard's **AI assistant settings** panel, or by
setting the synced config keys `speedrun:aiAssist` (master) +
`speedrun:debriefEnabled` / `speedrun:coachEnabled` /
`speedrun:tagSuggestEnabled`. Choose a backend with `speedrun:aiBackend`
(`mock` | `claude-cli` | `openai-compatible`), or leave it empty to read the
environment (`SPEEDRUN_AI_BACKEND`, `SPEEDRUN_AI_MODEL`); the default is an
**offline mock**, so nothing leaves the machine until you opt in. The page
talks to a desktop-only host bridge (`/_anki/speedrunAssistant`,
`qt/aqt/speedrun_assistant.py`) that re-checks every flag server-side and
stays read-only; when it is absent (e.g. on Android) the page hides the AI
affordances and ships no broken buttons.

### The sample deck

A curated, tagged 72-card CFA Level I deck (two-level taxonomy:
`cfa::topic::<area>` + `cluster::<topic>::<family>`) is checked in at
`desktop/tools/speedrun/cfa_level1_sample.apkg`, with contrast scheduling
pre-enabled on its preset. Import it via File → Import. To regenerate:

```bash
cd desktop
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/make_cfa_deck.py
```

### The fade-ladder deck (Phase 2)

A 59-note worked/faded/solve/compare deck for the narrow
high-element-interactivity slice (duration family, TVM, FIFO/LIFO) is
checked in at `desktop/tools/speedrun/cfa_ladder.apkg` — importing it next
to the sample deck **merges** into the same deck + preset. Every note is
generated by the automated pipeline and tagged `aig::ungraded` (studyable,
excluded from readiness); each card's feedback block now cites its grounding
source as a readable, labelled reference (e.g. **Source: Duration —
Compounding conventions**) rather than raw corpus coordinates. The ladder is
inert until you enable **Fade Ladder** in deck options and set an exam date
on the dashboard. To regenerate the content and the deck:

```bash
cd desktop/tools/speedrun
# generate items + validation gates + retrieval grounding + eval reports
python3 aig/run_pipeline.py --backend mock --out items/generated.jsonl
# build the .apkg (validates every item + runs the R9 feedback lint)
cd ../..
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_ladder_deck.py \
    --items "tools/speedrun/items/generated.jsonl"
```

### The held-out probe deck (Phase 3)

The measurement instrument behind Readiness: **35 concepts × 2 reworded
application MCQs**, hand-authored (no AI), covering all 10 topics, split
concept-disjoint into a 50-item **performance** pool (feeds the gauge) and
a 20-item **calibration** pool (feeds only the offline harness — no
circularity). Checked in at `desktop/tools/speedrun/cfa_probes.apkg`;
import it next to your study decks. Probe cards never feed Memory or
coverage, their answers never count as study reviews, and an outcome only
reaches Readiness when the probe was answered **≥ 7 days** after its
cluster was last studied (the delay is measured from the revlog, never
assumed). Contract: `desktop/tools/speedrun/probes/PROBE_SCHEMA.md`. To
regenerate and re-verify:

```bash
cd desktop
# validate the bank + leakage-scan it against the corpus/generator + self-test
python3 tools/speedrun/probe_harness.py
# rebuild the .apkg from the JSONL bank
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_probe_deck.py
# after real study: bridge proof + calibration from your collection, then
# write the calibration record the dashboard surfaces
python3 tools/speedrun/probe_harness.py --collection <collection.anki2> --apply
```

### The ablation harness (Phase 3)

`python3 tools/speedrun/ablation.py` (from `desktop/`) runs the
**simulation** ablation: nine arms (vanilla / contrast / fade / allocation
/ full-on / cross-topic-leakage / leave-one-out arms) on equal study
budgets and identical content, scored on Memory, **delayed** Performance
and Readiness-calibration, with the pre-registered primary comparison
stated ahead (full-on vs vanilla on delayed performance) and the
lenient-vs-strict **abstention arm** quantifying the honesty cost of
over-claiming. Seeded and deterministic; reports land in
`tools/speedrun/eval/ablation_report.{json,md}` and the analysis in
[`desktop/PHASE3_RESULTS.md`](desktop/PHASE3_RESULTS.md) — which discloses
loudly that these are properties of a documented learner model, not human
data.

### BYO decks — "Prepare this deck for Speedrun" (Phase 3)

For imported/untagged decks: a deck gear-menu action (desktop-only,
**default-off** — enable `speedrun:byoOnboardingEnabled`) that proposes
`cfa::topic::*`, `cluster::*`, `rung::*` and `interactivity::*` tags from
deterministic lexicon/structure heuristics (an optional AI pass may fill
blanks the heuristics abstained on, same grounded-or-abstain rules as the
assistant), mines `confusable::high` markers from the deck's own revlog
when ≥200 reviews exist (auto-validated, abstaining), and can draft
missing rung items through the full AIG gate pipeline (always
`aig::ungraded` — studyable, never feeding Readiness). Every proposal
shows its **evidence and confidence** in a preview dialog; nothing is
written until you click Apply, the write is tags-only and **undoable**,
and note content is never touched.

Companion offline tools (all under `desktop/tools/speedrun/`):

- `aig/confusability.py` — behavioural confusion-mining over the revlog;
  writes `confusable::high` markers only when the computed signal beats a
  surface-similarity baseline on held-out behavioural labels, else abstains.
- `retire_items.py` — [R24] auto-retirement: flags generated items whose
  live point-biserial shows no discrimination (`--apply` tags them
  `aig::retired`).
- `python3 -m unittest discover -s tools/speedrun/tests` runs the 382-test
  suite (schema/lint, generators, gates, retrieval, confusability mining,
  retirement, the runtime assistant's grounding / abstention / bridge
  gating — and Phase 3's probe bank/harness, ablation and onboarding
  suites).

## Building the Android app

Prerequisites: Android SDK + NDK (version pinned in
`android-backend/gradle/libs.versions.toml`), a JDK (17+), rustup.

```bash
# 1. Build the backend .aar from the shared engine (desktop/ via the symlink)
cd android-backend
echo 'sdk.dir=<your android sdk path>' > local.properties
./build.sh                      # cargo run -p build_rust

# 2. Build AnkiDroid against that local backend
cd ../android
printf 'sdk.dir=<your android sdk path>\nlocal_backend=true\n' > local.properties
./gradlew :AnkiDroid:assemblePlayDebug

# 3. Install on a running emulator/device and launch
adb install -r AnkiDroid/build/outputs/apk/play/debug/AnkiDroid-play-arm64-v8a-debug.apk
adb shell am start -n com.ichi2.anki.debug/com.ichi2.anki.IntentHandler
```

Push the sample deck and import it from the AnkiDroid deck picker to review
the same deck the desktop app uses:

```bash
adb push desktop/tools/speedrun/cfa_level1_sample.apkg /sdcard/Download/
```

## Setting up sync (desktop ↔ Android)

Both apps share Anki's sync engine; they just need a server. The engine
ships a self-hosted one:

```bash
# 1. Run the sync server (any machine both apps can reach)
cd desktop
PYTHONPATH=out/pylib \
  SYNC_BASE=~/.anki-speedrun-syncserver \
  SYNC_PORT=27701 \
  SYNC_USER1=cfa:speedrun \
  out/pyenv/bin/python -m anki.syncserver
```

Pick a port other than 8080 (the dev app uses it for devtools). Users are
`SYNC_USER1..N` as `username:password`; the folder in `SYNC_BASE` holds the
server-side collections.

**Desktop:** Preferences → Syncing → Self-hosted sync server →
`http://127.0.0.1:27701/`, restart, then press **Sync** in the toolbar and
log in (`cfa` / `speedrun`).

**Android (emulator):** Settings → Sync → Custom sync server → Sync URL
`http://10.0.2.2:27701/` (10.0.2.2 is the emulator's alias for the host's
localhost; on a real phone use the host's LAN IP). Then Settings → Sync →
AnkiWeb account → log in with the same credentials, and press the Sync icon
in the deck list.

Notes:

- The **first** sync between two non-empty collections is a **one-way full
  sync** — Anki asks which side wins (upload or download). After that,
  syncs are incremental and merge both ways.
- Conflict rule (Anki-native, documented for challenge 7b): the review log
  is **append-only**, so reviews made on both devices are all kept — none
  lost, none double-counted; for a card's scheduling state, the copy with
  the newer modification time wins.
- If the server is restarted with a different `SYNC_BASE` or different
  credentials, clients hold a stale login (an `hkey` the server no longer
  accepts) and must log out and back in once.
- Startup failing with `opening media → open media db → DbError → Locked`
  means another server instance is already running against the same
  `SYNC_BASE` (SQLite holds the lock). Stop it first —
  `pkill -f anki.syncserver` — or use a different `SYNC_BASE`. Restarting
  with the same `SYNC_BASE` and users is transparent to the apps: the data
  and logins carry over, no re-login needed.

## What is custom in this fork (Phase 1 + Phase 2 + runtime assistant)

| Area | Change |
| --- | --- |
| `desktop/proto/anki/deck_config.proto` | Phase 1: `contrast_scheduling` (47), `contrast_tag_prefix` (48). Phase 2: `contrast_confusable_tag` (49) + the fade-ladder fields (50–58) and `FadeSignal`/`FadeOrder` enums. **Phase 3**: `readiness_allocation` (59) |
| `desktop/rslib/src/scheduler/queue/builder/contrast.rs` | the contrast pass (Phase 2: + confusability/fluency gate; 6 unit tests) |
| `desktop/rslib/src/scheduler/queue/builder/fade.rs` | **Phase 2**: FSRS-driven fade gating (13 unit tests; **Phase 3**: + the M0 combined-pass integration test — gate first, then cluster the survivors) |
| `desktop/rslib/src/scheduler/queue/builder/gathering.rs` | **Phase 2**: bury-style gate check in the gather path |
| `desktop/rslib/src/scheduler/queue/builder/allocation.rs` | **Phase 3**: readiness-optimization allocation — stable weighted-gap reorder of the merged queue (new file, 4 unit tests) |
| `desktop/rslib/src/readiness/` | **Phase 3**: the Readiness backend — self-contained Beta/Binomial math (`beta.rs`), versioned CFA blueprint priors (`blueprint.rs`), delayed-probe outcome extraction (`probes.rs`), and the banded, two-number, abstaining gauge with the backend-enforced give-up gate (`mod.rs`); 19 unit tests |
| `desktop/rslib/src/stats/mastery.rs` | `TopicMastery` RPC (Phase 2: + user tag→topic map, unmapped-tag buckets, `aig::ungraded` exclusion; **Phase 3**: + `probe::held_out` exclusion; 9 unit tests) |
| `desktop/rslib/src/stats/concept_graph.rs` | `ConceptGraph` RPC (new file, 2 unit tests) |
| `desktop/proto/anki/stats.proto` | `TopicMastery` + `ConceptGraph` messages (Phase 2: `tag_topic_map`, `unmapped_tags`, `ungraded_aig_cards`); **Phase 3**: `GetReadiness` RPC + the full honesty-contract response (`held_out_probe_cards` on TopicMastery) |
| `desktop/ts/routes/concept-graph/` | force-directed knowledge map (new page) |
| `desktop/pylib/anki/collection.py` | `Collection.topic_mastery()` + `concept_graph()` + **Phase 3** `get_readiness()` + pytests |
| `desktop/ts/routes/dashboard/` | the three-gauge dashboard (Phase 2: Map-tags editor, exam-date field, `aig::ungraded` disclosure, vitest suite; **Phase 3**: Readiness is a thin display layer over `GetReadiness` — the honesty-contract panel shows the call + its confidence, probe evidence, lags, and calibration history) |
| `desktop/ts/routes/deck-options/{ContrastOptions,FadeOptions,ReadinessOptions}.svelte` | the toggle UIs (**Phase 3**: + Readiness Allocation) |
| `desktop/qt/aqt/speedrun_dashboard.py`, `toolbar.py`, `mediasrv.py` | Dashboard dialog + toolbar link + config/RPC exposure (**Phase 3**: + `getReadiness`) |
| `desktop/tools/speedrun/` | tagged sample deck + generator; **Phase 2**: ladder note types + deck builder (now with readable source citations), the automated AIG pipeline (`aig/`), grounding corpus, confusability mining, item retirement; **Phase 3**: the held-out probe bank (`probes/`), probe deck builder, one-command probe harness, simulation ablation harness, BYO onboarding engine |
| `desktop/tools/speedrun/assistant/` | **Runtime assistant** (new): the grounded-or-abstain adapter (S1, reusing `aig/models.py`) + the read-only debrief / coach / tag-suggest features; 98 unit tests |
| `desktop/qt/aqt/speedrun_assistant.py` (+ route in `mediasrv.py`) | **Runtime assistant** (new): the desktop-only, read-only host bridge (`/_anki/speedrunAssistant`) — default-off, every flag re-checked server-side |
| `desktop/ts/routes/dashboard/assistant.ts` (+ `DashboardPage.svelte`, `config.ts`) | **Runtime assistant** (new): the coach / debrief / suggest UI, the bridge client, and the `speedrun:aiAssist*` toggles (+ vitest) |
| `desktop/qt/aqt/speedrun_onboard.py` (+ `deckbrowser.py` hook) | **Phase 3**: the "Prepare this deck for Speedrun" onboarding action — desktop-only, default-off (`speedrun:byoOnboardingEnabled`), previewed, undoable |
| `android/`, `android-backend/` | monorepo local-backend wiring; engine version pin; deck long-press → **Concept map** and deck-list menu → **CFA Dashboard** (WebView hosts + RPC routing in `AnkiDroid/.../pages/`; **Phase 3**: + `getReadiness` route so the phone gauge shows the same backend band); the runtime assistant and onboarding are desktop-only and their affordances stay hidden here |

Full details, the "why Rust, not Python" note, and the upstream-merge
analysis: [`desktop/RUST_CHANGE_NOTE.md`](desktop/RUST_CHANGE_NOTE.md).
Model definitions and the give-up rule:
[`desktop/MODEL_DESCRIPTIONS.md`](desktop/MODEL_DESCRIPTIONS.md). The runtime
assistant layer's spec, invariants, and per-feature acceptance criteria:
[`desktop/RUNTIME_AI_PLAN.md`](desktop/RUNTIME_AI_PLAN.md).

## Honesty rules (enforced in code)

- No gauge ever blends Memory, Performance and Readiness into one number.
- Memory abstains when FSRS is off or nothing is studied — no proxies.
- Performance is labelled **uncalibrated** (Memory × a documented transfer
  factor); the probe harness measures the real memory→performance gap on
  delayed held-out MCQs, and that transfer factor never feeds Readiness.
- Readiness **abstains** until ≥ 300 graded study reviews, ≥ 70% topic
  coverage, and ≥ 50 **delayed** held-out probe outcomes with a usefully
  narrow band — and names exactly which inputs are missing. Since Phase 3
  the gate lives **in the Rust engine** (`GetReadiness` zeroes the numbers
  while abstaining), never in the display layer; it never emits a bare
  point, caps its certainty at the mock↔exam ceiling, and near the pass
  boundary it may abstain forever — the unpublished MPS is irreducible
  uncertainty, and saying so beats a confident guess.
- Held-out hygiene: probe-bank cards never feed Memory or coverage, probe
  answers don't count as study reviews, the performance and calibration
  pools are concept-disjoint (no circular calibration), and the probe bank
  is leakage-scanned against the corpus and every generator prompt.
- **The review loop is AI-free by construction** — it makes no model calls,
  ever. Authoring-time AI bakes generated cards and their named sources into
  the deck offline; the optional **runtime assistant layer** runs only at
  study time, **outside** that loop, and is **read-only** — it may narrate
  or suggest but never writes to grading, scheduling, or Readiness, is
  **off by default**, and is **grounded-or-abstain** (it may not state a
  number the app did not compute, and defers to the gauge rather than invent
  a pass probability). The tag→topic suggester only pre-fills; nothing is
  persisted until the user saves.
- Authoring-time generation is fully automated (no human sign-off — an
  explicitly accepted risk), so its output is quarantined: `aig::ungraded`
  items **never feed readiness** (the dashboard shows the exclusion count),
  and non-discriminating items are auto-retired from live responses.
  Retrieval evals use synthetic qrels and say so.
- The tag→topic mapping is read-time and user-authored — unmapped tags stay
  visible as coverage gaps; nothing is ever silently attributed.
- The fade ladder needs a user-set exam date; without one it falls back to
  always-worked rather than guessing a horizon.

## Licenses

- `desktop/` (Anki fork): [GNU AGPL v3 or later](desktop/LICENSE), with
  credit to Anki — Ankitects Pty Ltd and contributors. Some components are
  BSD-3-Clause; see the source headers.
- `android/` (AnkiDroid fork): GPL-3.0 (see `android/COPYING`).
- `android-backend/` (rsdroid): GPL-3.0, building the AGPL Anki engine.
