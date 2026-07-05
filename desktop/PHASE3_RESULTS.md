# Phase 3 Results — Analysis & Write-up (M6)

**Date:** 2026-07-04 · **Inputs:** the M4 simulation ablation
(`tools/speedrun/eval/ablation_report.{json,md}`, seed 20260704, 90 days ×
40 cards/day × 20 replications) and the M3 probe harness
(`tools/speedrun/eval/probe_harness_report.{json,md}`). Companion to
`PHASE3_PLAN_V2.md` (v2.1).

> **Read this first.** Every effect size below comes from a **simulated
> learner** (a single hand-specified exponential-forgetting model — the
> equations are in `tools/speedrun/ablation.py`'s docstring), because one
> real learner cannot be split into nine study-policy arms. The numbers are
> **descriptive, not inferential**: replication SDs describe seed noise
> inside one model, not population uncertainty. No human outcome data
> existed at the time of writing — the live Readiness gauge accordingly
> **abstains**, which is the designed behaviour, not a gap in it.

---

## 1. The pre-registered primary comparison

Stated ahead of the run (and recorded in the harness before scoring):
**full_on vs vanilla on delayed-Performance** — held-out probe accuracy
≥ 7 simulated days after last study, equal budget, same content.

> **full_on 0.839 ± 0.036 vs vanilla 0.726 ± 0.053; paired Δ = +0.113 ±
> 0.062 (n = 20 replications).**

So within this learner model, the unified graph scheduler (contrast + fade

- allocation) beats vanilla Anki on delayed application performance at
  equal study time. Everything below is exploratory.

## 2. Per-SPOV contribution (exploratory)

| feature                      | alone vs vanilla (delayed-Perf) | removed from full_on (delayed-Perf) |
| ---------------------------- | ------------------------------- | ----------------------------------- |
| contrast (SPOV 1/3)          | **+0.060**                      | **+0.049**                          |
| fade ladder (SPOV 2)         | +0.008                          | +0.012                              |
| allocation (SPOV 4, demoted) | +0.024                          | +0.015                              |

- **Contrast does most of the delayed-performance work**, consistent with
  its mechanism (discrimination training transfers to reworded application
  probes; the confusion-error rate drops from 0.112 to 0.047).
- **Fade helps Memory more than Performance** (+0.023 Memory alone) —
  expected: it optimizes support/withdrawal of scaffolding, not
  discrimination.
- **Allocation's gain is real but bounded** (+0.024): it reallocates a
  fixed budget toward heavy weak topics; it cannot create new learning.

## 3. The [R8] cross-topic-leakage arm

Granting contrast credit **across** topic boundaries — the evidence says
general/far transfer is ~zero (St. Hilaire & Carpenter 2023 g=0.04; Pan &
Rickard PEESE ≈ 0) — spent 303.4 adjacency pairs per replication on
cross-topic pairs (vs 11.3 wasted in the within-topic arm) and produced
**worse trained discrimination (0.650 vs 0.714)** and slightly lower
delayed performance (0.779 vs 0.786) than within-topic contrast. The
default (within-topic-only credit) is the right one; cross-topic credit is
confirmed as a pure cost in this model.

## 4. The abstention arm — the honesty cost of over-claiming

Replaying every trajectory through both gates:

- the retired **lenient gate** (≥15 reviews & ≥1% coverage) would have
  emitted a number on **89 of 90 days**; the strict [R1] gate emits on 61
  (first emission: simulated day 29, once 300 reviews + coverage + probe
  evidence accumulate);
- **31.1% of lenient-gauge days are over-claims** (days the strict gate
  abstains), and those emissions carry a **Brier of 0.48–0.88** against
  the simulated exam outcome — i.e. the numbers the strict gate refuses to
  show are, on average, **worse than useless** (a constant 0.5 guess
  scores 0.25). That is the measured cost the R10 auto-fail rule exists to
  prevent.

## 5. Readiness calibration status (the honest part)

- **Live gauge: abstaining.** No human has answered ≥50 delayed probes,
  so `GetReadiness` returns ABSTAIN with the full honesty contract
  (evidence, missing inputs, calibration history "never run"). This is
  the correct cold-start behaviour ([R1]/[R6]-corrected).
- **Pipeline: proven end-to-end.** The harness self-test drives synthetic
  outcomes through the whole path — delayed-outcome extraction (x=29 of
  n=50), the bridge gap (0.213 synthetic), and temperature calibration on
  the disjoint calibration pool (log-loss 0.811 → 0.678 at T=4.34), and
  `--apply` writes the exact `speedrun:readinessCalibration` record the
  RPC surfaces. In the ablation, the strict banded gauge reaches a Brier
  of **0.12 (full_on)** against simulated outcomes vs 0.37 for vanilla —
  better study → tighter, better-calibrated bands.
- **Conformal coverage: not reported** — conformal backstops were
  descoped to future work (C6); the shipped backstop is the gate + the
  width floor, and its behaviour (permanent abstention near the cut) is
  verified by unit test
  (`readiness::test::near_the_cut_the_width_gate_abstains_even_with_rich_data`).

## 6. The memory→performance gap (challenge 7d)

- **Simulated:** vanilla ends at Memory 0.859 vs delayed-Performance
  0.726 — a **0.13 gap**; full_on narrows it to 0.057 (0.896 vs 0.839),
  mostly via discrimination training. The harness self-test reproduces a
  0.213 gap on its synthetic revlog.
- **Real:** the 35×2 hand-authored probe bank ships
  (`tools/speedrun/probes/probe_bank.jsonl` → `cfa_probes.apkg`), pools
  concept-disjoint, leakage-scanned clean (8-gram wall, both directions,
  reference PDF included). Once a learner answers probes ≥7 days delayed,
  `probe_harness.py --collection` reports the real gap and calibration —
  the same command, no code changes ("re-runnable", challenge 7h).

## 7. Disclosed limitations

1. **Simulation, not human data** — all effect sizes are properties of a
   documented model; they motivate the design, they do not validate it.
2. **Power** — single user / small cohort by design; even the future real
   probe data is n=1. Descriptive statistics only; disclosed as the
   primary limitation, not hidden.
3. **Mock↔exam ceiling** — mocks predict the real exam only moderately
   (r ≈ 0.7, Castro 2025). The engine caps call confidence at 0.85 and
   floors the band half-width at 0.10, so no amount of probe data can
   display near-certainty.
4. **Delay sensitivity** — immediate probe accuracy overstates transfer
   (Rohrer 2015); undelayed answers are logged but excluded, and the
   measured study→probe lag is reported rather than assumed.
5. **The MPS is unpublished** — carried as a configurable band
   ([0.68, 0.75] default); near the boundary the width gate may abstain
   forever, and that is the honest answer.

## 8. Verdict against the Phase 3 questions (M6)

| Question                                          | Answer                                                                                                                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Does graph scheduling beat vanilla at equal time? | **Yes in simulation** (+0.113 delayed-Performance, primary comparison); untested on humans, live gauge abstains until real probe evidence exists.                  |
| Per-SPOV contribution?                            | Contrast ≫ allocation > fade for delayed performance; fade leads on Memory (§2).                                                                                   |
| Is Readiness calibrated?                          | The pipeline is (self-test + ablation Brier 0.12 for full_on); the live gauge honestly reports "calibration never run" until real calibration-pool outcomes exist. |
| Memory→performance gap?                           | 0.13 (vanilla, simulated), narrowed to 0.06 by full_on; real measurement one command away once probes are answered (§6).                                           |
