# Automated Tests — Full Catalog

Repo: `Anki_Speedrun` · App version: `26.05b1` · Catalog generated: 2026-07-05

This is an **index**, not a copy. Every suite below stays in its home directory (moving
test files would break imports, path assumptions, and CI); this document tells you where
each suite lives, what it covers, how big it is, and how to run it.

Two kinds of automated tests live in this repo:

1. **Speedrun-authored tests** — written for this fork's new features (readiness engine,
   contrast scheduling, fade ladder, probe bank, AIG pipeline, runtime AI assistant,
   dashboard/concept-map UI). These are the tests *this project* is responsible for.
2. **Vendored upstream tests** — inherited from Anki / AnkiDroid / Anki-Android-Backend.
   They are the regression safety net proving the fork did not break stock behaviour.

Count provenance: numbers marked **(run-verified)** come from actually executing the suite.
Numbers marked **(static)** are ripgrep counts of test declarations (`def test_`, `#[test]`,
`test(`/`it(`, `@Test`) — reliable as suite sizes, but parametrized/expanded cases mean the
executed total can differ.

---

## Totals at a glance

| Category | Suite | Files | Tests | Provenance |
| --- | --- | ---: | ---: | --- |
| Speedrun | Python unit (`tools/speedrun/tests/`) | 23 | **570** | run-verified (8.0 s, all pass) |
| Speedrun | Rust unit (new `rslib` modules) | 8 | ~53 | static |
| Speedrun | TypeScript/vitest (dashboard + concept-graph) | 5 | 28 | run-verified in user-test (15/15 + 13/13) |
| Speedrun | Measurement harnesses (`tools/speedrun/*.py`) | 17 tools | n/a | run-verified (each writes `eval/*_report.*`) |
| Upstream | Python (`pylib/tests/`, `qt/tests/`) | 17 | 125 | static |
| Upstream | Rust (`rslib/`, incl. the 53 Speedrun ones) | — | ~386 | static |
| Upstream | TypeScript/vitest | 8 | 44 | static |
| Upstream | Android (`android/`, `android-backend/`) | 427 | ~2121 | static |

Speedrun-authored automated test cases: **~651** (570 Py + 53 Rust + 28 TS) plus 17 runnable
measurement harnesses. Everything else is the inherited upstream regression suite.

> Doc-drift note: older docs cite "561"/"547" Speedrun Python tests (`README.md`,
> `usertest/PRIMER.md`). The suite has grown; the **run-verified** figure today is **570**.

---

## 1. Speedrun Python unit tests — `desktop/tools/speedrun/tests/`

23 files, **570 tests, all green in ~8.0 s** (run-verified 2026-07-05).

Run all:

```bash
cd desktop
PYTHONPATH=out/pylib out/pyenv/bin/python -m unittest discover -s tools/speedrun/tests
```

Most files are stdlib-only and also run under a plain `python3`; the ones that touch the
engine (e.g. probe/ladder collection reads) want `PYTHONPATH=out/pylib out/pyenv/bin/python`.

### 1a. AIG pipeline (automated item generation) — 79 tests

| File | Tests | Covers |
| --- | ---: | --- |
| `test_aig_gates.py` | 25 | Machine-validation gates (`aig/gates.py`) + the mock-LLM path. |
| `test_aig_retrieval.py` | 28 | Retrieval-for-grounding (`aig/retrieval.py`), stdlib arms forced for determinism; degraded-arm reporting. |
| `test_aig_generators.py` | 14 | Parameterized numeric generators (`aig/generators.py`). |
| `test_aig_confusability.py` | 12 | The computed confusability signal (`aig/confusability.py`). |

### 1b. Runtime AI assistant (default-off, grounded-or-abstain) — 98 tests

| File | Tests | Covers |
| --- | ---: | --- |
| `test_assistant_debrief.py` | 33 | Post-session error-pattern debrief (`assistant/debrief.py`). |
| `test_assistant_tag_suggest.py` | 22 | Tag→topic suggester pre-fills only what survives validation; never persists. |
| `test_assistant_core.py` | 20 | Backend adapter grounded-or-abstain semantics (`assistant/core.py`). |
| `test_assistant_coach.py` | 12 | Coach is grounded, prioritisation-only, defers to Readiness (no pass % while abstaining). |
| `test_assistant_bridge.py` | 11 | Desktop host bridge gating + read-only invariants; default-OFF; degraded modes. |

### 1c. Measurement-harness unit tests — 237 tests

Unit coverage of the pure logic inside each harness (see §4 for the harnesses themselves).

| File | Tests | Covers |
| --- | ---: | --- |
| `test_probe_harness.py` | 53 | `speedrun-probe-v1` validator, ≥7-day delay rule (mirrors `rslib/.../readiness/probes.rs`), calibration math, collection read, self-test. |
| `test_ablation.py` | 40 | Ablation rigor mechanics: determinism, equal-budget invariant, within-topic discrimination credit [R8], abstention accounting. |
| `test_memory_calibration.py` | 34 | Engine-pinned forgetting curve, Brier/log-loss/ECE binning, bootstrap determinism, holdout rule, leakage guard, SVG chart. |
| `test_crash_test.py` | 30 | Crash-test pure helpers: committed-vs-in-flight accounting, integrity parsing, engine-check classification, no-pylib import hygiene. |
| `test_bench.py` | 27 | Nearest-rank percentile math, §10 target PASS/FAIL logic, report rendering, deterministic deck specs; import-without-pylib. |
| `test_card_check.py` | 23 | Card-checker units, frozen-cutoff pinning, gold-set integrity (re-derives every quantitative answer). |
| `test_sync_test.py` | 16 | Revlog union/dedupe (seeded loss/dup faults must be caught), conflict-winner assertion, report render, CLI, import hygiene. |
| `test_injection_eval.py` | 14 | Prompt-injection resistance eval logic. |

### 1d. Content, schema & onboarding — 156 tests

| File | Tests | Covers |
| --- | ---: | --- |
| `test_onboard.py` | 51 | BYO-deck onboarding proposal engine [M5]: deterministic-first, honest abstention, lexicon margin rule, AI pass gating. |
| `test_ladder_schema.py` | 37 | `speedrun-item-v1` contract (`ITEM_SCHEMA.md`) + the [R9] feedback lint. |
| `test_probe_bank.py` | 31 | Real probe bank (`probes/probe_bank.jsonl`) vs the `speedrun-probe-v1` contract. |
| `test_ladder_notetypes.py` | 26 | The four fade-ladder card variants (worked / faded-cloze / solve-MCQ / compare). |
| `test_strip_source_citation.py` | 7 | Pure transforms in `strip_source_citation.py`. |
| `test_retire_items.py` | 4 | [R24] retirement tool: discriminating vs. flat items. |

---

## 2. Speedrun Rust unit tests — `desktop/rslib/src/`

~53 `#[test]` cases in modules **added by this fork** (confirmed created in Phase 1–3 commits,
absent from `.upstream/anki`). They live inline with the engine code they test.

| Module | Tests | Feature |
| --- | ---: | --- |
| `scheduler/queue/builder/fade.rs` | 14 | Fade ladder queue gating (worked→faded→solve). |
| `stats/mastery.rs` | 9 | Per-topic mastery RPC (`topic_mastery`). |
| `readiness/beta.rs` | 8 | Beta-distribution band math for the readiness estimate. |
| `readiness/mod.rs` | 6 | Honesty contract: abstention gates, zeroed response constructor. |
| `scheduler/queue/builder/contrast.rs` | 6 | Contrast scheduling (confusable cards back-to-back). |
| `readiness/probes.rs` | 4 | Held-out probe accounting + ≥7-day delay rule. |
| `scheduler/queue/builder/allocation.rs` | 4 | Readiness allocation of the daily queue. |
| `stats/concept_graph.rs` | 2 | Concept-graph nodes/edges. |

Run:

```bash
cd desktop
cargo test -p anki readiness          # scoped example
cargo test -p anki                     # all rslib tests (upstream + Speedrun)
```

---

## 3. Speedrun TypeScript / Svelte tests — `desktop/ts/routes/`

28 vitest cases across 5 files, on the two new SvelteKit surfaces. Verified green during the
AI user-test (dashboard **15/15**, concept-graph **13/13**).

| File | Cases | Covers |
| --- | ---: | --- |
| `routes/dashboard/metrics.ts` → `metrics.test.ts` | 9 | Gauge/coverage/best-next math, abstention & zero-state handling. |
| `routes/dashboard/assistant.test.ts` | 6 | Dashboard AI-assistant panel logic (default-off, grounded). |
| `routes/concept-graph/grouping.test.ts` | 6 | Tag grouping for the knowledge map. |
| `routes/concept-graph/layout.test.ts` | 5 | Force-directed layout. |
| `routes/concept-graph/colour.test.ts` | 2 | Honest node colouring. |

Run (from `desktop/ts/` so `@generated` resolves):

```bash
cd desktop/ts
../node_modules/.bin/vitest run routes/dashboard
../node_modules/.bin/vitest run routes/concept-graph
```

---

## 4. Speedrun measurement harnesses — `desktop/tools/speedrun/*.py`

Not unit tests but **runnable evaluation/ship-gate commands**; each writes a report to
`desktop/tools/speedrun/eval/*_report.{json,md}`. Their pure logic is unit-tested in §1c.
Committed headline results (2026-07-05):

| Harness | What it proves | Committed headline |
| --- | --- | --- |
| `bench.py` | §10 performance vs targets on a 50k-card collection | `button_press_ack` p95 0.707 ms — PASS |
| `crash_test.py` | SIGKILL mid-review + network-off robustness | **0 of 20** collections corrupted; 14,148 answers committed |
| `sync_test.py` | Two-client offline sync, union + conflict rule | Union correct; newer-mod-wins conflict verdict |
| `card_check.py` | Gold-set card ship gate (7f) | Blocks wrong/bad-teaching/unverifiable cards; exit 1 on fail |
| `injection_eval.py` | Prompt-injection resistance | **PASS** — 6 payloads × 4 model-facing surfaces |
| `probe_harness.py` | Held-out probe bank validity + leakage wall | Bank valid 70 = 35×2; leakage **CLEAN** (8-gram wall) |
| `memory_calibration.py` | FSRS retrievability calibration (Brier/ECE) | Engine-pinned curve; low Brier on holdout |
| `ablation.py` | Feature-effect ablation (simulated) + real-collection companion | Simulated Δ with equal-budget arms; `ablation_real_report.md` is n=1 observational |

Builders/validators (produce the decks & schemas the tests consume): `build_ladder_deck.py`,
`build_probe_deck.py`, `make_cfa_deck.py`, `cfa_sample_cards.py`, `ladder_notetypes.py`,
`ladder_schema.py`, `onboard.py`, `retire_items.py`, `strip_source_citation.py`.

Invocation cheatsheet (from `desktop/`):

```bash
# needs the engine:
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/bench.py
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/crash_test.py
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/sync_test.py
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/memory_calibration.py
# stdlib only:
python3 tools/speedrun/card_check.py
python3 tools/speedrun/injection_eval.py
python3 tools/speedrun/probe_harness.py
python3 tools/speedrun/ablation.py
```

---

## 5. Vendored upstream Python tests — `desktop/pylib/tests/`, `desktop/qt/tests/`

125 test functions (static). Inherited from Anki; run for regression safety.

`pylib/tests/` (14 files, 97 fns): `test_schedv3.py` (36), `test_models.py` (12),
`test_importing.py` (10), `test_collection.py` (8), `test_stats.py` (8), `test_exporting.py` (6),
`test_cards.py` (4), `test_decks.py` (3), `test_find.py` (3), `test_media.py` (3),
`test_flags.py` (1), `test_latex.py` (1), `test_template.py` (1), `test_utils.py` (1).
Helpers: `shared.py`, `support/`.

`qt/tests/` (3 files, 28 fns): `test_mediasrv.py` (16), `test_addons.py` (10), `test_i18n.py` (2),
plus `qwebengine_csp_smoke.py`.

Run via the build system (`./check`) or directly:

```bash
cd desktop
PYTHONPATH=out/pylib out/pyenv/bin/python -m pytest pylib/tests
```

---

## 6. Vendored upstream Rust tests — `desktop/rslib/`

~386 `#[test]`/`#[tokio::test]` attributes across `rslib` (static; **includes** the ~53
Speedrun modules in §2 — so ~333 are strictly upstream). Dedicated integration modules:
`import_export/package/apkg/tests.rs`, `import_export/package/colpkg/tests.rs`,
`sync/collection/tests.rs`, `sync/media/tests.rs`, `rslib/src/tests.rs`. Heaviest inline
suites: `cloze.rs` (18), `import_export/.../notes.rs` (18), `csv/metadata.rs` (16),
`typeanswer.rs` (12), `scheduler/fsrs/params.rs` (11).

```bash
cd desktop && cargo test -p anki
```

---

## 7. Vendored upstream TypeScript tests — `desktop/ts/`

44 upstream vitest cases (static; 8 files): `lib/domlib/surround/surround.test.ts` (17),
`reviewer/lib.test.ts` (5), `deck-options/lib.test.ts` (5), `lib/domlib/surround/unsurround.test.ts` (4),
`routes/change-notetype/lib.test.ts` (4), `deck-options/steps.test.ts` (4),
`html-filter/index.test.ts` (3), `lib/tslib/time.test.ts` (2).

```bash
cd desktop && ./ninja check:svelte     # or scope with vitest as in §3
```

---

## 8. Vendored Android tests — `android/`, `android-backend/`

The largest suite in the repo: **~2,121 `@Test` methods** (static) across **359 unit test
files** (`src/test/`) and **68 instrumented files** (`src/androidTest/`).

| Source root | Files | Type |
| --- | ---: | --- |
| `android/AnkiDroid/src/test/` | 305 | JVM unit (Robolectric) |
| `android/AnkiDroid/src/androidTest/` | 62 | Instrumented (device/emulator) |
| `android-backend/rsdroid-instrumented/src/androidTest/` | 26 | Instrumented backend |
| `android/lint-rules/src/test/` | 22 | Custom lint rules |
| `android/libanki/src/test/` | 20 | libanki JVM |
| `android/common/src/test/` | 8 | shared utils |
| `android/compat/src/test/` | 7 | compat shims |
| `android/{api, common/android}/src/test/`, `android-backend/rsdroid/src/test/` | 7 | misc |

Run (standard AnkiDroid workflow):

```bash
cd android
./gradlew test                 # JVM unit tests
./gradlew connectedCheck       # instrumented tests (needs a device/emulator)
```

---

## Honest gaps (things no automated suite covers)

Carried over from the AI user-test coverage matrix (`AI_USER_TESTS.md` §Coverage) and the
build system:

- Real Qt **keyboard input** and native surfaces (menu bar, gear menu, file pickers) — not
  unit-testable; exercised only via affordances/code review in the user-test.
- **AnkiWeb sync** against the real service (only the bundled server is covered by `sync_test.py`).
- **Add-on ecosystem** compatibility.
- End-to-end **installer** artifacts (DMG/APK) — hashes verified, not installed.
- Non-US **locale** rendering.
