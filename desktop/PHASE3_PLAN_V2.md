# Phase 3 Plan v2.1 — Readiness (Banded + Calibrated), Unification & the Rigorous Ablation

> ⚠️ **GRILLING ERRATA — see [`GRILLING_NOTES.md`](./GRILLING_NOTES.md), which SUPERSEDES conflicting
> text below.** Corrected here: **C4** (drop the "distance-to-1600 scaled-score band" — it invents a
> number the rubric forbids; keep P(pass) on [0,1] with abstention); **C5** (Beta-Binomial/calibration
> need real outcomes the demo deck lacks → they come from the manual **30×2 paraphrase set**, else
> abstain); **C6** (IRT/PFA/LKT + Rudner + Venn-Abers + conformal are **descoped to future work** —
> unidentifiable for n=1, no stats runtime in `rslib`; ship shrunk-logistic/Beta-Binomial + fixed
> blueprint priors + honest abstention); **C8** (held-out testing must NOT depend on the deferred AI
> pipeline — use the 30×2 set, simulate the ≥7-day delay from the revlog). The mobile rebase is a
> cherry-pick **spike-first** decision (see `GRILLING_NOTES.md` §4).

> 🔧 **v2.1 (2026-07-04) — pre-implementation audit; body text below is now consistent with the
> errata and with the shipped tree.** Substantive corrections, each marked **[v2.1]** in place:
>
> 1. **The "shipped lenient gate" claims were stale.** `metrics.ts` no longer ships
>    `MIN_GRADED_REVIEWS=15` / `MIN_COVERAGE=0.01` or a bare point `pPass`: the strict [R1] gate
>    (300 graded reviews / 70% weighted coverage / 50 held-out probes / half-width ≤ 0.20) is
>    already live, `HELD_OUT_PROBES_ANSWERED` is 0 by construction, so **Readiness always abstains
>    in real use today**; the logistic method is reachable only in the loudly-labelled
>    `?readinessTest=1` mode. GRILLING Tier 0 (the auto-fail fix) is DONE. Phase 3's M1 job is to
>    **move the math to a testable Rust backend and replace the test-mode logistic with a
>    probe-outcome Beta-Binomial band**, not to remove a live auto-fail (already removed).
> 2. **The mobile PREREQUISITE (old deliverable 7 / [R27]) is RESOLVED — no rebase.** Its premise
>    ("rsdroid 0.1.64 pins 25.09.2") does not match this repo: the vendored `android-backend` is
>    **`0.1.65-anki26.05b1`**, which pins exactly the `26.05b1` baseline `desktop/` is on, and the
>    Android build on the shared engine was already verified (commits `03a9d86`, `fe1ba0e`). "One
>    commit builds desktop + AnkiDroid" already holds. A rebase-down to 25.09.2 would _drop_ ~265
>    upstream commits Phase 1/2 code depends on (`note_tags_by_id`, `extract_fsrs_retrievability`,
>    `CardData.decay` are 26.05-era). Decision per GRILLING §4.1: **no spike needed; prerequisite
>    closed.**
> 3. **C14 (pylib exposure) is already half-done:** `Collection.topic_mastery()` _and_
>    `Collection.concept_graph()` exist with pytests. Only the new `get_readiness` needs wiring.
> 4. **M0 is largely built:** `build_queues` already runs fade-gate → gather → contrast as one
>    coordinated pass with gate-before-cluster precedence, **topic-scoped cluster keys** (within-
>    topic-only credit, `clusters_do_not_bridge_topics` test) and the C10 sibling guard. Remaining
>    M0 work is an integration test of the combined pass + doc note.
> 5. **The second honest number ([R5]) is re-specified without IRT** (C6 descope): "confidence of
>    the pass/fail call" = the Beta-posterior classification confidence
>    `max(P(pass), 1 − P(pass))` propagated through the MPS band, capped by the mock↔exam ceiling
>    ([R25]), abstaining ("too close to call") when the band straddles 50%. Rudner CA/CC via IRT
>    stays cited as future work.
> 6. **Calibration ([R3]) is scoped to what n=1 supports:** temperature scaling (one scalar) fit by
>    the harness on the disjoint calibration-mock pool, reported as Brier/log-loss + fit date
>    (the honesty contract's "calibration history"), stored in collection config for the RPC to
>    surface. Venn-Abers / conformal / isotonic stay descoped (C6), named as future work.
> 7. **The pass gate band default moves to ~68–75% mock-proxy** ([R25]) — replacing the
>    `[0.60, 0.70]` MPS band constants — and stays configurable; certainty is capped and the band
>    half-width floored (`W_min`) so it can never collapse to near-certainty.

> **BANNER — evidence-refined revision of `PHASE3_PLAN.md` (v1).** This supersedes v1 by folding
> in `RESEARCH_ADDENDUM.md` (9 threads, 29 concrete plan changes). Same skeleton as v1
> (scope / milestones M0–M6 / deliverables / touch-points / risks), but every readiness claim is
> now grounded in a cited method with a concrete parameter. **Core stance for the 20%-honesty and
> 12%-held-out-tests weights: never emit a point pass-probability — emit a calibrated BAND, add a
> SECOND honest number (classification confidence), ABSTAIN loudly when thin, and prove
> memory→performance on DELAYED, held-out, re-runnable application MCQs.**
>
> Companion to `brainlift.md`, `PHASE1_PLAN.md`, `PHASE2_PLAN.md`, `PRD.md`, `RESEARCH_ADDENDUM.md`.

---

## What changed vs PHASE3_PLAN.md (v1) — delta section

**[v2.1 state audit]** The Readiness **display** gauge in `ts/routes/dashboard/metrics.ts` is
built and already carries the strict [R1] give-up rule (300 graded reviews / 70% coverage /
50 held-out probes / half-width ≤ 0.20, always-abstaining in real use because
`HELD_OUT_PROBES_ANSWERED = 0` by construction); its logistic MPS-band method
(`mpsLow=0.6, mpsHigh=0.7`, corner-evaluated range) is reachable only in the labelled test mode.
Everything else in Phase 3 is NOT started. v2 rewrites the _math and contract_ of that gauge
(band from real probe outcomes, never a point; second honest number; calibration history) and
specifies the Rust backend it needs; the rows below marked ~~struck~~ were corrected by the
GRILLING errata before implementation.

| #  | v1 said                                           | v2 changes it to                                                                                                                                                                                                                                                                                                                                         | Cite  |
| -- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| 1  | `pPass.point = logistic(...)` (a point number)    | **Never emit a point.** Point-of-record = Beta-Binomial posterior mean, Jeffreys prior; ship a Wilson/Jeffreys 90% BAND. ~~Output as distance-to-1600 scaled band~~ **[v2.1: C4 — stay on [0,1]; any 1600 figure would invent a number]**                                                                                                                | [R2]  |
| 2  | Wald `band()` (`mean±Z·√(p(1−p)/n)`)              | Wilson/Jeffreys interval (Wald under-covers at small n)                                                                                                                                                                                                                                                                                                  | [R2]  |
| 3  | (no calibration; deferred)                        | **Temperature scaling** (one scalar, fit by the harness on the calibration-mock pool). ~~Venn-Abers/Beta for a distribution-free guarantee~~ **[v2.1: C6 — future work]**; **never isotonic** under ~1000 outcomes                                                                                                                                       | [R3]  |
| 4  | logistic map on hand-guessed `TRANSFER[]` factors | Readiness point-of-record comes from **held-out probe outcomes** (Beta-Binomial), with CFA blueprint weights as **fixed priors** for coverage/allocation — ~~IRT/PFA/LKT backbone~~ **[v2.1: C6 — unidentifiable at n=1; future work]**. `TRANSFER[]` survives only inside the separate, labelled-uncalibrated Performance proxy, never inside Readiness | [R4]  |
| 5  | one number (`pPass`)                              | **two** numbers: P(pass) band AND classification confidence of the pass/fail call — **[v2.1: from the Beta posterior through the MPS band, not IRT (C6); abstains "too close to call" when the band straddles 50%]**                                                                                                                                     | [R5]  |
| 6  | (none)                                            | ~~conformal backstop w/ Small-Sample Beta Correction~~ **[v2.1: C6 — future work; the shipped backstop is the abstention gate + width floor]**                                                                                                                                                                                                           | [R6]  |
| 7  | held-out "measurement" deferred, undated          | held-out probes must be **DELAYED ≥1 week** application MCQs, not immediate accuracy; delay measured from the revlog (C8), lag logged honestly                                                                                                                                                                                                           | [R7]  |
| 8  | cluster credit unqualified                        | **within-topic near-transfer only**; cross-topic leakage is the ablation; ZERO transfer credit w/o success+congruency+feedback                                                                                                                                                                                                                           | [R8]  |
| 9  | `MIN_GRADED_REVIEWS=15`, `MIN_COVERAGE=0.01`      | abstain unless `graded_reviews≥300` AND `coverage≥70%` AND `≥50 delayed probe items` AND `interval_half_width≤W_max` **[v2.1: already shipped in `metrics.ts`; Phase 3 moves it into the Rust gate so the backend, not the display, enforces it]**                                                                                                       | [R1]  |
| 10 | hardcoded `MPS_CENTER=0.65` / logistic to 1.0     | cap max certainty at `prior_mock_exam_corr≈0.7`; gate = configurable band **~68–75%**; enforce a **minimum interval width**                                                                                                                                                                                                                              | [R25] |
| 11 | (mobile: doc-only)                                | ~~PREREQUISITE: rebase onto anki 25.09.2 tag (rsdroid 0.1.64)~~ **[v2.1: RESOLVED — repo is `26.05b1` + rsdroid `0.1.65-anki26.05b1`; one commit already builds desktop + phone; rebase would drop needed upstream commits]**                                                                                                                            | [R27] |

New backend needed: `rslib/src/readiness/` (Beta-Binomial + Wilson/Jeffreys, the abstention gate,
the MPS-band map with certainty cap + width floor, the probe-outcome extraction, and the
calibration-history surface — **[v2.1: IRT/Rudner/Venn-Abers/conformal descoped per C6, cited as
future work]**) exposed via a `GetReadiness` RPC; `metrics.ts` becomes a thin display layer over
it. Deferred-in-v1 M3/M4 are **partially promoted** here because the honesty (20%) and held-out
(12%) weights depend on them.

---

## Scope

**In scope**

- **Unification** — one graph-aware `build_queues` pass over the **tag taxonomy** (cluster
  interference edges on `cluster::*` + rung dependencies on `rung::*`), all three SPOVs at once.
  No synced edge table (tags already sync; a local-only `card_relationships` cache only if needed).
- **Readiness gauge, banded + calibratable** — a documented, cited method that **emits a band, not
  a point**, adds a **second** honest number (classification confidence), **abstains** on thin
  data, and displays the full honesty contract (evidence + missing data + calibration history +
  range + best-next-topic). Plus the readiness-optimization allocation selector (demoted SPOV4).
- **Held-out mock harness (promoted from deferred)** — a disjoint, **delayed** application-MCQ
  probe bank that is both the memory→performance bridge proof (challenge 7d) and the calibration
  ground truth; re-runnable (challenge weight 12%).
- **Generalization** — validated edge sourcing for BYO / untagged decks.

**Builds on Phases 1–2:** Phase 1 contrast (interference edges) + Phase 2 fade (dependency edges +
Performance gauge + content pipeline). Depends on Phase 1 [R1] abstention thresholds.

**Out of scope:** general productization; full statistically-powered multi-subject RCT (single-user

- small-cohort ablation only — power is honestly disclosed as a limitation).

---

## Milestones

### M0 — Unify the graph (over tags) — **[REVISE → mostly DONE; verify]**

Run both edge types through one `build_queues` pass: contrast on `cluster::*` (interference) +
gating/fading on `rung::*` (dependency). Precedence when a card is in both: **gate first, then
order survivors by cluster.** No new synced data. **[R8] constraint added:** cluster ordering
credit is applied **within-topic only** — never reorder across topic boundaries, because
general/far transfer is ~zero (St. Hilaire & Carpenter 2023, general g=0.04). Cross-topic
reordering is reserved as an explicit ablation arm (M4), not a default.

**[v2.1 status]** Already implemented by the Phase 2 work: `build_queues` runs
`load_fade_gate` (pre-gather, bury-style, limits exact) → `gather_cards` → `load_contrast_clusters`
→ contrast permutation on the **merged** main queue (C3), with topic-scoped cluster keys
(`clusters_do_not_bridge_topics`) and the C10 sibling guard. Remaining: one integration test that
exercises **both** passes in a single build (a gated rung card must not participate in cluster
adjacency; survivors still adjoin), plus a doc note. The fade↔contrast coupling
(`fluency_blocked_clusters`) already exists and is tested.

### M1 — Readiness gauge: banded, two-number, abstaining — **[PARTIAL-DONE → REVISE heavily]**

Display exists in `metrics.ts` (strict gate already live, abstaining by default — see v2.1 note);
its math and contract are replaced. New backend does the stats.

- **[R2] Emit a BAND, never a point.** Replace the test-mode-only `logistic(...)` method as the
  method of record.
  - Point-of-record = Beta-Binomial posterior mean with **Jeffreys prior Beta(0.5,0.5)**:
    `p̂ = (x + 0.5) / (n + 1)` over graded held-out outcomes (x correct of n) from the M3 probe
    bank's _Performance-probe_ pool (C5/C8 — never derived from FSRS recall).
  - Band = **Wilson / Jeffreys 90%** interval (replace the current Wald-style corner `band()` —
    Wald under-covers at small n). Widen the band as it propagates through the MPS map:
    P(pass) = P(exam score ≥ MPS) under the posterior, evaluated at the pessimistic
    `(posterior 5th pct, MPS_high)` and optimistic `(posterior 95th pct, MPS_low)` corners.
  - **[v2.1: C4]** Output stays **P(pass) on [0,1]** with the abstention contract. ~~Distance-to-
    1600 scaled-score band~~ — CFA never publishes the MPS location on the 1600 scale, so any
    such figure invents a number (auto-fail vector). Interval evidence: Brown, Cai & DasGupta
    (2001), recommended for n≤40.
- **[R4] Backbone — [v2.1: C6-corrected].** ~~IRT / PFA / LKT (logistic)~~ is **future work**:
  item parameters are unidentifiable for a single sparse user and `rslib` carries no stats
  runtime. What ships: the Beta-Binomial probe-outcome estimate above, with **CFA-blueprint topic
  weights as FIXED priors** (never fitted) for coverage weighting and allocation, and honest
  abstention. DKT stays rejected (Wilson 2016; Khajah 2016) — the _simple_ model is the
  _defensible_ one and beats a simpler baseline by design. The hand-guessed `TRANSFER[]` table
  survives **only** inside the separate, labelled-uncalibrated Performance proxy; Readiness never
  reads it.
- **[R5] Second honest number — [v2.1: C6-corrected] Beta-posterior classification confidence at
  the MPS** (Rudner-2005-style CA/CC via IRT is cited as future work). Surfaced as **"confidence
  of this pass/fail CALL"**, distinct from P(pass): `max(P(pass), 1 − P(pass))` under the
  posterior propagated through the MPS band, capped by [R25]. **Abstain the call ("too close to
  call")** when the P(pass) band straddles 50%.
- **[R25] Cap certainty; configurable gate; floor the interval width.**
  - `prior_mock_exam_corr ≈ 0.7` caps max attainable certainty (mocks predict the exam only
    moderately: Castro 2025 r≈0.71–0.76; Ronen: mocks reward recall, exam demands application) —
    implemented as a documented cap on call confidence and a floored band.
  - Readiness gate is a **configurable mock-proxy BAND ~68–75%** (300Hours revised the L1 target
    down to 68% in Nov 2025) — replaces the hardcoded `[0.6, 0.7]` MPS constants and the
    logistic-to-1.0 tails.
  - Enforce a **minimum interval half-width `W_min`** reflecting irreducible residual error, so the
    band can never collapse to near-certainty.
- **[R1] Abstention gate (coordinated with Phase 1) — moves into the Rust backend.** Emit any
  P(pass) band **only if ALL** hold: `graded_reviews ≥ 300` AND `topic_coverage ≥ 70%` (≥7/10
  areas) AND `delayed_held_out_probe_items ≥ 50` AND `interval_half_width ≤ W_max`; else render
  **"READINESS: insufficient data (abstaining)."** **[v2.1]** These thresholds are already live in
  `metrics.ts`; Phase 3 makes the **backend** enforce them so no display layer can bypass the
  gate, keeping the labelled test mode (owner decision, GRILLING §4.4) as an explicit RPC flag
  whose output is marked test data. Evidence: selective prediction / Chow rule (El-Yaniv &
  Wiener 2010).
- **Honesty-rule display contract (always render, even when abstaining):** (1) the **evidence** the
  call rests on (n graded, per-topic coverage, calibration sample size), (2) **what's missing**
  (which gate failed), (3) **calibration history** (last calibration date + Brier/log-loss), (4)
  the **range/band** (never a bare point), (5) the **single best next topic** (largest weighted gap;
  over-weight **Ethics** near the boundary — dual role: largest weight + tie-break, T8).

### M2 — Readiness-optimization allocation (demoted SPOV4) — **[REVISE]**

Card selection weighted by `exam-weight × marginal Δ P(pass-band-center)`, a Readiness-gradient
selector; toggle it; ablate against vanilla uniform desired-retention. **[R8]** the Δ it optimizes
uses **within-topic** performance credit only. **[R25]** exam-weight uses the **(min,max,midpoint)**
topic-weight config (versioned by exam year), budgeting by midpoint and carrying the range as
uncertainty; single weighted-overall target (no topic-level cutoffs, since CFA publishes only an
overall MPS).

### M3 — Held-out mock harness (calibration + the bridge proof) — **[PROMOTED from Deferred → NEW]**

Promoted because honesty (20%) and held-out re-runnable tests (12%) depend on it; kept scoped.

- **[R7] Probes are DELAYED (≥1 week), held-out application MCQs — not immediate accuracy.**
  Transfer/discrimination benefits are delay-sensitive (Rohrer 2015: d=0.42 immediate → 0.79 at
  30 days). Schedule each probe item ≥7 days after its last study touch; log the study→probe lag.
- **Bridge proof = challenge 7d paraphrase test:** ≥30 cards × 2 reworded application variants;
  report the **memory-vs-performance gap** (retention accuracy minus delayed-paraphrase accuracy).
- **Partition the held-out bank into disjoint pools:** a _Performance-probe_ pool and a
  _calibration-mock_ pool, so Readiness is never calibrated against its own inputs (no circularity).
- **[R3] Calibration method = temperature scaling** (one scalar, fit by the harness on the
  calibration-mock pool; applied only to the labelled test-mode logistic display, never to the
  Beta-Binomial point-of-record, which is calibrated against outcomes by construction).
  **[v2.1: C6]** ~~Venn-Abers or Beta calibration for a distribution-free guarantee~~ — future
  work. **Never isotonic** below ~1000 pass/fail outcomes (Niculescu-Mizil & Caruana 2005:
  "<1000 cases"; a single learner will never reach this — soften the cutoff to a 200–1000
  learner-dependent range).
- **[R6] [v2.1: C6]** ~~Conformal backstop with Small-Sample Beta Correction~~ — **future work**
  (no stats runtime in `rslib`; n=1 cold start). The shipped backstop is the [R1] abstention gate
  - the [R25] width floor, which at cold start frequently says "abstain (interval too wide)" —
    that is the honest, correct behavior (Angelopoulos & Bates 2023; Zwart 2025,
    arXiv:2509.15349, kept as citations for the future-work section).
- **Re-runnable:** the whole harness runs from one command (ties to challenge 7h bench discipline);
  leakage scan (7e) walls the probe bank off from any generator prompt / calibration input.

### M4 — Rigorous ablation — **[REVISE — arms sharpened]**

Arms on **equal total study time** and the **same content**, scored on Memory / delayed-Performance
/ Readiness, reporting per-SPOV contribution + combined effect. Beyond v1's feature-ON / OFF /
vanilla-Anki, add the evidence-motivated arms:

- **[R8] SPOV1 cross-topic-leakage arm:** default within-topic cluster credit vs a cross-topic-credit
  variant — expected to NOT help and possibly hurt calibration (St. Hilaire g=0.04; Pan & Rickard
  PEESE intercept ≈0). A free, evidence-backed ablation.
- **Give-up / abstention arm:** shipped-lenient thresholds vs the [R1] gate — quantifies the
  honesty cost of over-claiming.
  Power is limited (single user / small cohort) and is disclosed as the primary limitation, not hidden.

### M5 — Generalization (BYO / untagged decks) — **[REVISE]**

AI edge sourcing for untagged decks: LLM cluster/rung proposals + behavioral confusion mining from
`revlog` + similarity **only with a confusability signal** (never raw embedding similarity). Validate
proposed edges (human + behavioral) before use. **[R8]** generated/untagged edges get memory-retention
credit but ZERO performance-transfer credit until validated on delayed held-out probes. Maintain
held-out hygiene at scale.

**Onboarding UX — the "Prepare this deck for Speedrun" action** _(moved here from `RUNTIME_AI_PLAN.md`
on 2026-07-03; was that plan's "Feature D")._ The user-facing, **desktop-only** wrapper around the
edge-sourcing above: an import-time **add-on action** (nothing auto-runs on import) that proposes a
BYO deck's scheduling structure — **topic** (`cfa::topic::`), **cluster** (`cluster::`), **rung**, and
**interactivity** tags, **confusability** edges, and, where useful, **generated missing rungs**
(worked/faded/solve) grounded in the deck's own content — then **previews it for approval** before any
tag write, and the write is **undoable**.

- **Reuse the existing pipeline wholesale:** `models.make_llm_path` (drafter + critic + solver
  consensus) → `aig/gates.py` (numeric / solve-check / rationale / leakage) → `aig/retrieval.py`
  grounding → `speedrun-item-v1` records (`ITEM_SCHEMA.md`) → `build_ladder_deck.py`. Do **not** fork
  the schema or the gates.
- **Confusability edges** via `aig/confusability.py` on the deck's revlog — behavioral, within-topic,
  auto-validated on a 70/30 time split, **abstaining** when it can't beat the surface baseline
  ([R18]); never raw embedding similarity.
- **Honesty ([R8]/[R24]):** proposed tags are **human-confirmed**; the apply step is an **undoable
  pylib note update** (pattern: `confusability.apply_markers`); generated items are tagged
  `aig::ungraded` → excluded from Readiness by `mastery.rs`, earning memory-retention credit but
  **zero performance-transfer credit until validated** on delayed held-out probes (never flip
  `aig::graded` here).
- **Toggle:** `speedrun:byoOnboardingEnabled` (synced collection config, default OFF).
- **Platform:** desktop only (pylib + add-on action). **Milestones:** scope the proposal set → note
  reader / candidate extraction → topic/cluster/rung/interactivity proposer (abstaining) →
  confusability edges → missing-rung generation → previewed, undoable apply → honesty wiring + tests.

### M6 — Analyze & write up — **[REVISE]**

Does graph scheduling beat vanilla Anki on **delayed** Performance/Readiness at equal study time?
Per-SPOV contribution? How well is Readiness calibrated (Brier/log-loss on the calibration-mock
pool, coverage of the conformal bands)? Report the memory→performance gap (7d). Disclose the
mock↔exam ceiling (r≈0.7), the delay sensitivity, and the power limitation.

---

## Deliverables

1. **Unified graph scheduler** over the tag taxonomy (cluster + rung in one `build_queues` pass),
   with within-topic-only cluster credit — no synced edge table. **[v2.1: mostly DONE — add the
   combined-pass integration test]**
2. **Banded, two-number, abstaining Readiness gauge**: Beta-Binomial/Wilson-Jeffreys P(pass) band
   on [0,1] **[v2.1: C4 — no 1600 scale]** + Beta-posterior classification confidence **[v2.1:
   C6 — Rudner via IRT is future work]** + full honesty display contract (evidence, missing
   gates, calibration history, band, best-next-topic), backed by a new `rslib/src/readiness/`
   module + `GetReadiness` RPC; `metrics.ts` is a thin display layer. **[REVISE / NEW backend]**
3. **Readiness-optimization allocation** toggle (within-topic Δ pass-prob × exam-weight). **[REVISE]**
4. **Held-out delayed-probe harness** (bridge proof 7d + temperature calibration **[v2.1: C6 —
   Venn-Abers/conformal future work]**), disjoint pools, one-command re-runnable, leakage-scanned. **[PROMOTED / NEW]**
5. **BYO/untagged-deck** validated edge-sourcing path + the "Prepare this deck for Speedrun"
   onboarding action (desktop-only, previewed, undoable, default-off). **[REVISE]**
6. **Sharpened three-plus-arm ablation** (ON/OFF/vanilla + cross-topic-leakage + abstention). **[REVISE]**
7. ~~PREREQUISITE — branch rebased onto anki 25.09.2~~ **[v2.1: RESOLVED — already satisfied by
   the `26.05b1` + rsdroid `0.1.65-anki26.05b1` baseline; no rebase (it would drop needed
   upstream commits)]**

---

## Engine / system touch points (reference)

| Concern                                    | File / area                                                                                                                                                                                                                                        | Change            |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| Unified graph pass (within-topic clusters) | `Collection::build_queues` — `rslib/src/scheduler/queue/builder/`                                                                                                                                                                                  | [REVISE]          |
| Edges over tags                            | `cluster::*` + `rung::*` on `notes.tags`; optional local-only `card_relationships` cache                                                                                                                                                           | keep              |
| **Readiness backend (NEW)**                | `rslib/src/readiness/` — Beta-Binomial+Wilson/Jeffreys, MPS-band map + certainty cap + width floor, probe-outcome SQL, abstention gate, calibration-history surface **[v2.1: C6 — IRT/Rudner/Venn-Abers/conformal are future work]**               | **[NEW]** [R2–R6] |
| Readiness RPC                              | `proto/anki/stats.proto` (`GetReadiness`); expose via Python Collection wrapper **[v2.1: `topic_mastery`/`concept_graph` already exposed (C14 half-done); only `get_readiness` to add]**                                                           | [NEW]             |
| Readiness display                          | `ts/routes/dashboard/metrics.ts` → thin layer over the RPC for Readiness: drop the local `logistic`/corner `band()` readiness math **[v2.1: `TRANSFER[]` stays only in the labelled Performance proxy; the lenient 15/0.01 gate is already gone]** | [REVISE]          |
| FSRS inputs (fade signal)                  | `extract_fsrs_*` — `rslib/src/storage/sqlite.rs`; revlog; predicted R at exam horizon w/ fitted decay                                                                                                                                              | ref               |
| Held-out probe bank + calibration          | new `readiness` submodule + one-command harness; leakage scan (7e)                                                                                                                                                                                 | [NEW]             |
| Toggles                                    | `proto/anki/deck_config.proto`, `rslib/src/deckconfig/mod.rs`                                                                                                                                                                                      | keep              |
| Behavioral mining                          | `revlog` analysis (SQL)                                                                                                                                                                                                                            | keep              |
| **Mobile prerequisite**                    | ~~rebase branch onto 25.09.2 tag (rsdroid 0.1.64)~~ **[v2.1: RESOLVED — no change needed]**                                                                                                                                                        | ~~[NEW]~~ [R27]   |

---

## Risks & decisions

- **[R10 AUTO-FAIL] Made-up / under-evidenced readiness** — **[v2.1: the lenient `≥15 reviews &
  ≥1% coverage` gate is already retired in the shipped `metrics.ts` (strict [R1] gate live,
  abstaining by default).** Residual risk: the gate lives only in the display layer; Phase 3
  moves enforcement into the Rust backend + keeps the labelled test mode. **Never surface a bare
  point pass-probability.**
- **[R27] Engine baseline mismatch — [v2.1: RESOLVED, no rebase].** The repo's `android-backend`
  is rsdroid `0.1.65-anki26.05b1`, pinning exactly the `26.05b1` baseline `desktop/` is on; the
  phone build on the shared engine was verified in Phase 1 (commits `03a9d86`, `fe1ba0e`).
  A rebase-down to 25.09.2 would drop ~265 upstream commits the Phase 1/2 code depends on.
- **Mock↔exam ceiling** — certainty is capped at `prior_mock_exam_corr≈0.7` with a floored interval
  width; do not let the band collapse to false confidence (Castro 2025; Ronen).
- **Cold-start abstention is EXPECTED and honest** — conformal + selective prediction will often say
  "abstain (interval too wide)"; render it as a feature, not a bug ([R5]/[R6]).
- **Calibration circularity** — Performance-probe and calibration-mock pools stay disjoint (M3).
- **Delay sensitivity of the bridge** — immediate accuracy overstates transfer; probes are ≥1 week
  delayed ([R7]).
- **Cross-topic leakage** — within-topic credit by default; cross-topic is an ablation, not a
  default ([R8]).
- **AI-sourced edge quality** — validate (human + behavioral); confusability-signal only; no
  ungraded generated items feed the readiness estimate.
- **Experiment power** — single-user/small-cohort; disclosed as the primary limitation.
- **No synced edge table** — unify over tags; a first-class synced table is avoided unless truly
  needed.
