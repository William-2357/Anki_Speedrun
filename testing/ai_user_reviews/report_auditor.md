# Auditor report — "Ada" (engine / harness / measurement-honesty audit)

Date: 2026-07-05 · Repo: `/Users/william/Anki_Speedrun` (work run from `desktop/`) ·
Scratch: mktemp dir `auditor_ada.E8OPS6smLw` (backup, regenerated reports, scripts) ·
Artifacts checked in: `usertest/artifacts/auditor_engine_drive_output.txt`,
`usertest/artifacts/auditor_unittest_output.txt`

---

## 1. Persona & journey

I am Ada, a data-skeptical engineer deciding whether a CFA study group can trust this
app's measurement claims. I never opened the GUI (three GUI testers + a veteran were
live on ports 40101–40104/9301–9304; untouched). Everything below is engine- or
harness-level, per PRIMER §1a/§1c, with all writes in my own mktemp dirs.

Exact path taken (all from `desktop/` unless noted):

1. **Backup**: `cp -Rp tools/speedrun/eval/ $AUDIT/eval_backup/` then
   `diff -r` → identical (27 files incl. `archive/`).
2. **Unit suite**: `python3 -m unittest discover -s tools/speedrun/tests`
   → **`Ran 561 tests in 8.433s — OK`** (0 failures, 0 errors; full output in
   `artifacts/auditor_unittest_output.txt`).
3. **Engine end-to-end drive** (PRIMER recipe 1a, extended; script + full output in my
   scratch, output copied to `artifacts/auditor_engine_drive_output.txt`): throwaway
   collection via `PYTHONPATH=out/pylib out/pyenv/bin/python`, FSRS on, phases:
   - Phase 0: RPCs on the **empty** collection;
   - Phase 1: import `cfa_level1_sample.apkg`, study **50** cards through the real v3
     scheduler (`get_queued_cards`/`build_answer`/`answer_card`, rating=Good);
   - Phase 2: **30-answer rating=0 (Again) loop**;
   - Phase 3: import `cfa_probes.apkg` on top, answer **10 probe cards**, re-call RPCs;
   - Phase 4: import `cfa_ladder.apkg` on top, re-call RPCs;
   - Phase 5: `get_readiness(test_mode=True)`.
     After every phase: `topic_mastery()`, `get_readiness()`, `concept_graph()` with
     mechanical assertions on the honesty contract (kind, zeroed band, missing[],
     call/call_confidence, probe exclusion). **26/26 checks passed; zero tracebacks.**
4. **Harness verification** (I am the only persona allowed): ran
   `card_check.py`, `injection_eval.py`, `probe_harness.py` (`--self-test
   --collection <my copy>`), `ablation.py` (sim + `--collection` companion),
   and `PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/memory_calibration.py
   (--self-test) --collection <my copy>` — every run redirected to my scratch via
   `--eval-dir/--report-dir/--output-dir/--out-dir/--work-dir` (collection runs used my
   private copy of `out/speedrun_eval/real_collection.anki2`; the shared cache was
   never written). Runtimes: 0.2 s / 0.2 s / 0.2 s / 4.7 s / 0.4 s — nowhere near the
   10-minute kill threshold. Each regenerated report was diffed (raw + a
   volatile-field-ignoring semantic JSON compare) against the committed one.
5. **Skipped by instruction** (loaded machine): `bench.py`, `sync_test.py`,
   `crash_test.py` — instead audited their committed reports for internal consistency
   and doc agreement (§2 below).
6. **Restore**: copied the two files my `injection_eval` default run had regenerated
   back from backup, then `diff -r eval $AUDIT/eval_backup` → **empty; byte-identical
   restoration verified** (re-verified again at teardown).
7. **Cross-doc audit**: grep/read-only pass mapping every README "Honesty rules" bullet
   to its enforcing code path, and MODEL_DESCRIPTIONS thresholds to engine constants
   and to the live RPC output of step 3.

---

## 2. Formatting / report / CLI-output quality observations

My persona's "UX" is the docs, CLI output, and generated reports.

**Reproducibility & doc-vs-report agreement (the good news).** Every headline number I
could legally re-run reproduced exactly; the semantic JSON diff (timestamps/paths
excluded) of committed vs regenerated:

| harness                              | regenerated vs committed                                                                                                                                           | headline claims vs docs                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `card_check.py`                      | identical (only `meta.generated`, paths, argv differ)                                                                                                              | 50/50 gold shipped, 14/15 defects blocked (miss = `defect::wrong::05`, disclosed), 11 correct-useful / 0 wrong / 39 bad-teaching, 41/50 blocked, ship-gate exit 1 + sidecar — **matches** `PHASE3_RESULTS.md:177-193` and `README.md:318` verbatim; gold file really contains 50+15 records (`gold/gold_set_v1.jsonl`, counted)                           |
| `injection_eval.py`                  | identical except its detail string                                                                                                                            | 6 payloads × 4 surfaces ALL PASS — **matches** `PHASE3_RESULTS.md:208-218`, `README.md:319`                                                                                                                                                                                                                                                               |
| `probe_harness.py`                   | identical except its lag stats                                                                                                                                  | bank valid (70 = 35×2, pools disjoint, all 10 topics), leakage CLEAN both directions incl. reference PDF, self-test x=29/n=50, bridge gap 0.2128, calibration log-loss 0.811→0.678 at T=4.34, collection run: 0 probe cards, calibration **REFUSES** below 10 outcomes — **matches** `PHASE3_RESULTS.md:90-97,104-115`                                    |
| `ablation.py` (sim)                  | **semantically identical** (fully seeded)                                                                                                                          | primary: **full_on 0.839 ± 0.036 vs vanilla 0.726 ± 0.053, Δ +0.113 ± 0.062, n=20** — matches `PHASE3_RESULTS.md:33-35` to the digit; per-SPOV (+0.060/+0.008/+0.024; +0.049/+0.012/+0.015), leakage arm (0.779 vs 0.786; 303.4 vs 11.3 wasted pairs; 0.650 vs 0.714), abstention arm (89 vs 61 days, 31.1% over-claims, Brier 0.48–0.88) all match §§2-4 |
| `ablation.py --collection`           | semantically identical                                                                                                                                             | 638 graded reviews, 5 study days, retention 0.9545, adjacency share 0.098, 0 delayed probes, no calibration record, "NOT an ablation" label — **matches** `PHASE3_RESULTS.md:201-207`                                                                                                                                                                     |
| `memory_calibration.py --collection` | collection tier **identical**: n=64, Brier 0.000673 (CI 0.000519–0.000826), log-loss 0.020503, ECE 0.020159, constant-baseline 0.002959, zero-lapse caveat present | **matches** `MODEL_DESCRIPTIONS.md:31-39` and `PHASE3_RESULTS.md:170-176` exactly, including the honest "degenerate holdout / one-sided evidence / single-bin reliability" caveats                                                                                                                                                                        |

**Audit-only (not re-run; machine loaded — instructed skip) of `bench` / `crash` /
`sync` committed reports:**

- `bench_report.md` (2026-07-05T08:02Z): p95s 0.707 / 1.284 / 432 / 369 / 17.4 ms,
  cold start 178.5 ms, peak 93.9 MB — **agree with** `PHASE3_RESULTS.md:143-153` to
  rounding; JSON `exit_code: 0`, `target_failures: []` consistent with all-PASS table.
  Disclosures are unusually honest (nearest-rank percentiles, headless-engine scope,
  "no phone number in this report is real", stated-after-measuring memory limit,
  §10 freeze target only _proxied_: `bench_report.md:36`). Numbers **not
  independently re-measured** by me — marked _not verified_, by design.
- `crash_test_report.md/.json`: row sums recomputed by me — attempted 14,159 =
  committed 14,148 + rolled-back 7 + unlogged 4; revlog delta sum 14,152 = committed +
  unlogged; `corrupted 0/20`; the network-off section's `graded_reviews=14152`
  (`crash_test_report.md:42`) is consistent with that same arithmetic. **Matches**
  `PHASE3_RESULTS.md:154-164` and `README.md:315`. Internally consistent throughout.
- `sync_test_report.md/.json`: union_check `passed: true`, 20/20 found, 0 lost / 0
  duplicated / 0 id collisions, `full_revlog_identical: true`; conflict keeps both
  revlog entries and winner = B via newer `card.mod` (1783230551 > 1783230550),
  `converged: true` — **matches** `README.md:316`, `PHASE3_RESULTS.md:165-169`. The
  report even discloses a real protocol hazard it found (same-millisecond revlog-id
  collision, `sync_test_report.md:33`) — exemplary.
- `SHIPPING.md` artifact hashes: I re-hashed both artifacts —
  DMG `eaa17179…65937` matches `SHIPPING.md:21`; APK `f3e0e3f6…ba847e` matches
  `SHIPPING.md:22`. (Installers themselves not executed.)
- `retrieval_eval.md:18-25` (BM25 0.500/0.955 beats vector 0.182 and RRF 0.364; the
  "fusion wins" claim quoted only from the archived torch run) — **matches**
  `README.md:324-330`, an honest negative correctly propagated to the README.

**CLI/report quality notes:** harness stdout is exemplary (one-line honest headlines,
non-zero exits where promised: card_check ship-gate exit 1 with sidecar). The
readiness `missing[]` strings are genuinely actionable ("The probe bank is not
imported (tools/speedrun/build_probe_deck.py builds it)"; "8 more were answered too
soon after study and are excluded (≥7-day rule)") — measured from my own runs.
Deductions: the −0% coverage string, a stray stderr line, post-formatted committed
reports that defeat byte-diff verification, and the self-test wall-clock drift which
slightly undermines the "Seeded" label a grader would rely on.

---

## 3. Learning experience vs. traditional Anki — "can a student trust these instruments?"

This is the trust audit, so I answer as an instrumentation reviewer, not a UI user.

**What is genuinely better than Anki's plain stats:**

- **The refusal machinery is real, engine-side, and survives adversarial poking.** I
  mechanically verified — on empty, young, Again-looped, probe-imported and
  ladder-imported collections — that `get_readiness()` returns `kind=ABSTAIN` with
  `p_pass_low/high/center = 0.0`, empty `call`, `call_confidence = 0.0`, and named,
  quantified missing inputs (`auditor_engine_drive_output.txt`). The zeroing is in the
  Rust response constructor (`rslib/src/readiness/mod.rs:342-348`), so a display bug
  _cannot_ leak an unearned pass % — a structurally stronger promise than any UI-side
  check, and one Anki doesn't attempt at all.
- **The instrument can't feed itself.** Probe answers don't count as study reviews
  (verified live: 80 graded reviews before == 80 after answering 10 probes;
  `mod.rs:174-177`, `probes.rs:60,132`), probe cards never enter Memory/coverage
  (topic studied counts unchanged 72→72; excluded at `mastery.rs:216-222`, disclosed
  as `held_out_probe_cards=70`), the ≥7-day delay rule visibly excluded 8 of my 10
  same-day probe answers with an explanatory message, and AI-generated ladder cards
  are quarantined (`ungraded_aig_cards=119` after ladder import; `mastery.rs:209-212`).
  Anki has no concept of any of this.
- **Claims trace to commands.** Every headline number I re-ran reproduced exactly
  (§2). The over-claiming cost is itself measured (lenient gate would emit on 89/90
  days with Brier 0.48–0.88 — worse than a coin flip's 0.25; `PHASE3_RESULTS.md:69-83`,
  reproduced in my ablation run). The docs' abstention thresholds (≥300 reviews, ≥70%
  coverage, ≥50 delayed probes, half-width ≤0.20, floor 0.10, cap 0.85, MPS
  [0.68,0.75]) are literal engine constants (`mod.rs:65-84`) echoed back in every RPC
  response I captured — three-way agreement of docs, code, and runtime behaviour.
- **AI stays out of the measurement loop, verifiably.** The queue builders contain no
  model/network code (grep: only AGPL header URLs in
  `rslib/src/scheduler/queue/builder/*.rs`); the assistant is a desktop-only bridge
  that re-checks default-off flags server-side (`qt/aqt/speedrun_assistant.py:16-18,
  190,233,273`) and the injection eval (re-run by me, ALL PASS) shows model output is
  never trusted.

**What a student should stay skeptical about (honest limits, mostly disclosed):**

- **Readiness may tell you nothing for weeks — possibly forever.** 50 _delayed_
  probe outcomes require ≥7-day gaps; near the pass boundary the width gate can
  abstain permanently (`MODEL_DESCRIPTIONS.md:102-104`, unit-tested at `mod.rs:582`).
  That's honest, but a mock exam gives a (noisy) number in one afternoon. This gauge
  complements mocks; it does not replace them, and the docs could say that louder.
- **Performance is a labelled guess.** `Memory × τ` with hand-set τ (0.60–0.90,
  `topics.ts:35-44`), correctly badged "uncalibrated estimate" (`metrics.ts:406-409`)
  — trust the badge, not the number.
- **Memory's calibration evidence is thin and one-sided**: n=64 held-out reviews with
  zero lapses; Brier 0.0007 is real but over-prediction _could not have shown up_ —
  the report says so itself (`memory_calibration_report.md:39-40`). Treat "Memory is
  calibrated" as "not yet contradicted".
- **All effect sizes are simulation** (+0.113 delayed-performance etc. come from a
  hand-specified learner model; `PHASE3_RESULTS.md:10-18` disclosies this loudly).
  The scheduling benefit for _you_ is unproven and, at n=1, unprovable.
- The probe bank is only 70 hand-written items; once consumed, its evidence ages.

**Would I recommend it to a CFA study group?** Yes, with framing: use it as an honest
memory tracker + discrimination trainer whose readiness gauge _refuses to flatter
you_, alongside real mocks. Versus traditional Anki: strictly more trustworthy
instrumentation (Anki's plain stats make no claims and thus can't lie, but also can't
gate; Speedrun makes claims and — on everything I could test mechanically — earns
them). The feature complexity does pay off _for this exam_, because the honesty gate
converts "no data" from a silent dashboard into an explicit, itemized to-do list.

---

## 4. Top fixes (ranked)

1. **Fix the `-0%` coverage rendering** — one-line: clamp/`.max(0.0)` before
   formatting at `rslib/src/readiness/mod.rs:243-244`. It's the first thing every
   fresh-profile user reads on the flagship honesty surface; a gauge whose brand is
   "we never show a wrong number" should not show a signed zero.
2. **Align the `GetReadinessResponse` field contracts with behaviour**:
   correct the `best_next_topic` comment (`stats.proto:291-293`) to "empty when no
   topic has a positive weighted gap", and either populate `reasons[]` while
   abstaining or drop "(always rendered)" from `stats.proto:287-288`. Display layers
   (including Android's) will be written against these comments.
3. **Refresh stale doc numbers**: PRIMER.md:91 field names,
   README.md:301 + PRIMER.md:195 "547" → 561, `ablation.py:1364` "30x2" → "35x2".
   Cheap, and exactly the kind of drift that erodes trust in an honesty-first repo.
4. **Make the "seeded" harness sections fully deterministic**: sort the
   leaked-n-gram set before reporting (`aig/gates.py:433`) and derive the self-tests'
   "now" from the seed (or stamp the wall-clock dependence in the report header), so
   a grader's semantic re-run diff is empty rather than explained-away.
5. **Ship the eval reports unformatted or format on generation**, and
   silence the intentional `--child requires …` stderr line in the test suite
   — both purely about keeping re-verification friction at zero.

---

## Teardown & isolation statement

- `eval/` restored and **verified byte-identical** to the pre-run backup
  (`diff -r tools/speedrun/eval $AUDIT/eval_backup` → empty), re-checked after the
  last write of this audit.
- No processes left running: post-run `ps` shows no auditor-owned processes; the
  GUI testers' instances (novice/skeptic/ux/veteran bases) were never touched, no
  ports were opened by me, and `bench/sync/crash` (the instance-spawning harnesses)
  were never invoked.
- Shared caches untouched: nothing was written to `out/speedrun_eval/` (all harness
  output redirected to my mktemp scratch; collection runs used a private copy of the
  cached real collection). No repo source file was modified. Repo writes limited to:
  this report + two `auditor_*` artifacts under `usertest/artifacts/`.
