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
  recall. Desktop: a deck's gear menu, or `/concept-graph` in the dev
  server. Android: long-press a deck → **Concept map** (the same Svelte
  page, served from the `.aar` on-device).
- **An honest readiness dashboard**: three separate gauges — **Memory**,
  **Performance**, **Readiness** — each with a range, its reasons, and a
  written **give-up rule**. Readiness abstains until it has real evidence.
  No AI anywhere in this phase.
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
excluded from readiness). The ladder is inert until you enable **Fade
Ladder** in deck options and set an exam date on the dashboard. To
regenerate the content and the deck:

```bash
cd desktop/tools/speedrun
# generate items + validation gates + retrieval grounding + eval reports
python3 aig/run_pipeline.py --backend mock --out items/generated.jsonl
# build the .apkg (validates every item + runs the R9 feedback lint)
cd ../..
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_ladder_deck.py \
    --items "tools/speedrun/items/generated.jsonl"
```

Companion offline tools (all under `desktop/tools/speedrun/`):

- `aig/confusability.py` — behavioural confusion-mining over the revlog;
  writes `confusable::high` markers only when the computed signal beats a
  surface-similarity baseline on held-out behavioural labels, else abstains.
- `retire_items.py` — [R24] auto-retirement: flags generated items whose
  live point-biserial shows no discrimination (`--apply` tags them
  `aig::retired`).
- `python3 -m unittest discover -s tools/speedrun/tests` runs the 120-test
  tooling suite (schema/lint, generators, gates, retrieval, confusability
  mining, retirement).

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

## What is custom in this fork (Phase 1 + Phase 2)

| Area | Change |
| --- | --- |
| `desktop/proto/anki/deck_config.proto` | Phase 1: `contrast_scheduling` (47), `contrast_tag_prefix` (48). Phase 2: `contrast_confusable_tag` (49) + the fade-ladder fields (50–58) and `FadeSignal`/`FadeOrder` enums |
| `desktop/rslib/src/scheduler/queue/builder/contrast.rs` | the contrast pass (Phase 2: + confusability/fluency gate; 6 unit tests) |
| `desktop/rslib/src/scheduler/queue/builder/fade.rs` | **Phase 2**: FSRS-driven fade gating (new file, 13 unit tests) |
| `desktop/rslib/src/scheduler/queue/builder/gathering.rs` | **Phase 2**: bury-style gate check in the gather path |
| `desktop/rslib/src/stats/mastery.rs` | `TopicMastery` RPC (Phase 2: + user tag→topic map, unmapped-tag buckets, `aig::ungraded` exclusion; 8 unit tests) |
| `desktop/rslib/src/stats/concept_graph.rs` | `ConceptGraph` RPC (new file, 2 unit tests) |
| `desktop/proto/anki/stats.proto` | `TopicMastery` + `ConceptGraph` messages (Phase 2: `tag_topic_map`, `unmapped_tags`, `ungraded_aig_cards`) |
| `desktop/ts/routes/concept-graph/` | force-directed knowledge map (new page) |
| `desktop/pylib/anki/collection.py` | `Collection.topic_mastery()` (+ map argument) + pytests |
| `desktop/ts/routes/dashboard/` | the three-gauge dashboard (Phase 2: Map-tags editor, exam-date field, `aig::ungraded` disclosure, vitest suite) |
| `desktop/ts/routes/deck-options/{ContrastOptions,FadeOptions}.svelte` | the toggle UIs |
| `desktop/qt/aqt/speedrun_dashboard.py`, `toolbar.py`, `mediasrv.py` | Dashboard dialog + toolbar link + config RPC exposure |
| `desktop/tools/speedrun/` | tagged sample deck + generator; **Phase 2**: ladder note types + deck builder, the automated AIG pipeline (`aig/`), grounding corpus, confusability mining, item retirement, 120 tooling tests |
| `android/`, `android-backend/` | monorepo local-backend wiring; engine version pin |

Full details, the "why Rust, not Python" note, and the upstream-merge
analysis: [`desktop/RUST_CHANGE_NOTE.md`](desktop/RUST_CHANGE_NOTE.md).
Model definitions and the give-up rule:
[`desktop/MODEL_DESCRIPTIONS.md`](desktop/MODEL_DESCRIPTIONS.md).

## Honesty rules (enforced in code)

- No gauge ever blends Memory, Performance and Readiness into one number.
- Memory abstains when FSRS is off or nothing is studied — no proxies.
- Performance is labelled **uncalibrated** (Memory × a documented transfer
  factor) until a held-out exam-style question bank exists (deferred).
- Readiness **abstains** until ≥ 300 graded reviews, ≥ 70% topic coverage,
  and ≥ 50 held-out probes — and names exactly which inputs are missing.
- AI is **authoring-time only** (Phase 2): the review loop makes no model
  calls, ever — generated cards and their named sources are baked into the
  deck offline. The generation pipeline is fully automated (no human
  sign-off — an explicitly accepted risk), so its output is quarantined:
  `aig::ungraded` items **never feed readiness** (the dashboard shows the
  exclusion count), and non-discriminating items are auto-retired from
  live responses. Retrieval evals use synthetic qrels and say so.
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
