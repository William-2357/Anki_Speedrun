# Model Descriptions — Memory · Performance · Readiness (Phase 3)

Three gauges, three different questions, shown separately with ranges and
never blended. Implementation: `rslib/src/stats/mastery.rs` (Memory data),
`rslib/src/readiness/` (the Readiness estimate + give-up gate, Phase 3),
`ts/routes/dashboard/metrics.ts` (Memory/Performance gauge computation and
the thin Readiness display layer; frontend constants live there and in
`cfa_weights_2026.json`).

## Memory — "can the student recall this fact right now?"

- **Estimator.** Per card, FSRS predicted retrievability `R(c)` evaluated at
  query time by the engine (`extract_fsrs_retrievability`, using each
  card's stored stability/difficulty/decay and elapsed time). Per topic
  `s`: `Memory(s) = mean R(c)` over that topic's **studied** cards (cards
  with an FSRS memory state). Overall: studied-count-weighted mean across
  topics. Unseen cards are excluded here and appear as **coverage** instead,
  so gaps read as low coverage, not fake-low memory.
- **Range.** Per topic, a ~90% band `mean ± 1.645 · sd/√n` from the
  engine-reported per-topic standard deviation; aggregated with the same
  weights. Fewer studied cards ⇒ wider band.
- **Labelling.** Cards at `R ≥ 0.9` are counted as **"high recall
  probability"**, deliberately not "mastered": retrievability is a
  scheduling target, not a competence threshold.
- **Give-up.** Abstains (no number, reasons shown) when FSRS is disabled or
  no card has a memory state. No `reviewed/seen` proxy is ever substituted.
- **Calibration status.** Checked on held-out reviews
  (`just memory-calibration`, §9 Step 1): a chronological cutoff holds out
  the last ~25% of graded reviews; the engine recomputes memory states from
  the truncated history (params never see the holdout) and each held-out
  first-per-card review is scored against FSRS retrievability. On the real
  collection (n = 64 held-out observations): **Brier 0.0007** (95% CI
  0.0005–0.0008), log-loss 0.021, ECE 0.020 — beating the constant-rate
  baseline (0.0030) and chance (0.25). Honest limits, from the report: the
  holdout contains **zero lapses** (observed recall 1.0), so the evidence
  is one-sided — over-prediction could not have shown up — and all
  predictions fall in the top bin (the reliability "curve" is one point).
  _Calibrated within the data's reach_; re-run the same command as lapses
  and longer gaps accumulate. Full report + reliability chart:
  `tools/speedrun/eval/memory_calibration_report.md` /
  `memory_calibration_chart.svg`.

## Performance — "can the student answer a new exam-style question?"

- **Estimator (uncalibrated proxy, labelled as such).**
  `Performance(s) = Memory(s) × τ(s)`, where `τ(s)` is a **documented
  per-topic transfer factor** (0.60–0.90; recall-heavy topics like Ethics
  high, computation-heavy topics like Derivatives low). The factors are
  stated assumptions in `topics.ts`, not measurements.
- **Range.** Memory's band propagated through `τ`, then widened by ±0.15
  because `τ` itself is an assumption. Confidence is **low** by
  construction in Phase 1.
- **Why a proxy.** Real transfer is measured by the Phase 3 probe harness
  (30+ concepts × 2 delayed paraphrased MCQs, challenge 7d) as the
  **memory-vs-performance gap** report; until enough delayed probe
  outcomes exist, the gauge shows `Memory × τ` under an explicit
  "uncalibrated estimate" badge — measuring-and-showing the gap instead of
  hiding it. `τ` never feeds Readiness.
- **Give-up.** Abstains whenever Memory abstains.

## Readiness — "what is the probability of passing today?"

Rewritten in Phase 3: the math and the give-up gate moved into the Rust
backend (`rslib/src/readiness/`, `GetReadiness` RPC), so no display layer
can bypass the gate. `metrics.ts` renders the response and computes
nothing.

- **Scale.** CFA Level I is pass/fail, so Readiness is a **pass-probability
  BAND** on [0,1] — never a bare point, and never a number on an invented
  scale (the 1600-style scaled score is deliberately not emitted: CFA never
  publishes the MPS's location on it, so any such figure would be made up).
- **Outcomes, not proxies.** The estimate is built from **delayed held-out
  probe outcomes** — first answers to hand-authored application MCQs
  (`probe::pool::performance`) taken **≥ 7 days** after their cluster was
  last studied (never-studied clusters count as delayed; immediate answers
  are logged but excluded, because immediate accuracy overstates transfer).
  FSRS recall is never converted into outcomes: deriving `(x, n)` from the
  model under test would be fabrication.
- **Method of record (C5/C6-corrected: simple and defensible at n=1).**
  1. Posterior over the true probe-success rate: `Beta(x+0.5, n−x+0.5)`
     (Jeffreys prior; Brown, Cai & DasGupta 2001).
  2. MPS map: `P(pass) = P(score ≥ MPS)` under a `Binomial(180, p)` exam
     model. The MPS is carried as a **configurable mock-proxy band**
     (default `[0.68, 0.75]`, key `speedrun:passBand`), never a point.
  3. Band: centre = posterior predictive (Beta-Binomial tail at the band
     midpoint); low/high = the Jeffreys 90% quantiles pushed through the
     pessimistic `(q05, MPS_high)` / optimistic `(q95, MPS_low)` corners.
  4. **Certainty caps ([R25]).** Half-width floored at 0.10, the band
     clamped into [0.02, 0.98], and the call confidence capped at 0.85 —
     mocks predict the real exam only moderately (r ≈ 0.7), so no amount
     of probe data may read as near-certainty.
  5. **Second honest number ([R5]).** The confidence of the pass/fail
     CALL: `max(P(pass), 1−P(pass))`, capped as above; the call **abstains
     ("too close to call")** whenever the band straddles 50%. (Rudner-style
     IRT classification accuracy is cited as future work — item parameters
     are unidentifiable for a single sparse learner.)
- **The give-up rule (written down, enforced in the Rust backend).**
  Readiness shows **no probability** unless all of:
  - ≥ **300 graded study reviews** (probe answers don't count as study),
  - ≥ **70% weighted topic coverage** (topics with ≥ 1 studied card),
  - ≥ **50 delayed held-out probe outcomes**, and
  - the resulting band's half-width ≤ **0.20** — near the pass boundary
    this may never clear; the unpublished MPS is irreducible uncertainty,
    and permanent abstention there is the honest answer.
    When abstaining it names each missing input, and the full honesty
    contract still renders: the evidence (x/n, lags, coverage), the
    calibration history, and the best next topic.
- **Calibration.** The offline probe harness scores the disjoint
  calibration pool (`probe::pool::calibration`) against a documented proxy
  prediction, reports Brier/log-loss, fits a temperature scalar, and
  writes the record to `speedrun:readinessCalibration`; the gauge surfaces
  it as "calibration history". Readiness is never calibrated against its
  own inputs (the pools are concept-disjoint), and the Beta-Binomial point
  of record is never rescaled — it is calibrated against outcomes by
  construction.
- **Test mode.** `dashboard?readinessTest=1` asks the backend to relax the
  gates (`GetReadinessRequest.test_mode`); the response is `kind=TEST` and
  every number is banner-labelled "TEST DATA — not a real prediction". The
  default that ships and demos is the abstaining gauge.
- **Best next study.** `argmax_s w(s) · (0.80 − Memory(s))` over blueprint
  weights — computed backend-side without the τ guess, shown even while
  abstaining. Near the boundary, **Ethics** takes a documented tie-break
  (largest weight + CFA's ethics adjustment for borderline candidates).

## Data honesty

- The engine reports `fsrs_enabled`, per-topic studied counts, the graded
  review count and per-topic dispersion, so every abstention above is
  decided on real data, in one place, and displayed with its reasons.
- Cards without a `cfa::topic::*` tag are counted and shown as
  "not counted towards any topic" rather than silently dropped.
- Held-out hygiene: probe-bank cards (`probe::held_out`) are excluded from
  Memory and coverage (the measurement instrument never feeds the gauges
  it tests), and `aig::ungraded` generated cards never feed any gauge;
  both exclusions are disclosed with counts.
- CFA-specific display mappings (topic list, aliases, τ) live in the
  frontend as reviewable data files. The Phase 3 exception is deliberate:
  the CFA blueprint weights and the probe/gate logic live in
  `rslib/src/readiness/` — the backend owns the gate precisely so the
  display cannot weaken it, and the blueprint is versioned data
  (`blueprint.rs`, mirrored by `cfa_weights_2026.json`).
