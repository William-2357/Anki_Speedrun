# Model Descriptions — Memory · Performance · Readiness (Phase 1)

Three gauges, three different questions, shown separately with ranges and
never blended. Implementation: `rslib/src/stats/mastery.rs` (data) +
`ts/routes/dashboard/metrics.ts` (gauge computation; all constants below
live there and in `cfa_weights_2026.json`).

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
- **Calibration status.** Not yet calibrated; the Brier/log-loss check on
  held-out reviews is a Phase 3 deliverable and is stated as missing.

## Performance — "can the student answer a new exam-style question?"

- **Estimator (uncalibrated proxy, labelled as such).**
  `Performance(s) = Memory(s) × τ(s)`, where `τ(s)` is a **documented
  per-topic transfer factor** (0.60–0.90; recall-heavy topics like Ethics
  high, computation-heavy topics like Derivatives low). The factors are
  stated assumptions in `topics.ts`, not measurements.
- **Range.** Memory's band propagated through `τ`, then widened by ±0.15
  because `τ` itself is an assumption. Confidence is **low** by
  construction in Phase 1.
- **Why a proxy.** Phase 1 has no held-out exam-style question bank, so
  transfer cannot be measured yet. Showing `Memory × τ` under an explicit
  "uncalibrated estimate" badge measures-and-shows the Memory→Performance
  gap instead of hiding it. Phase 2 (30 cards × 2 delayed paraphrased MCQs,
  challenge 7d) replaces `τ` with fitted, held-out-validated values.
- **Give-up.** Abstains whenever Memory abstains.

## Readiness — "what is the probability of passing today?"

- **Scale.** CFA Level I is pass/fail, so Readiness is a **pass
  probability** — no invented numeric score, ever.
- **Method (documented now, abstaining by default).**
  1. Weighted performance `P̄ = Σ w(s)·Performance(s) / Σ w(s)` over topics
     with data, using the **midpoint** of each topic's published 2026 weight
     range (`cfa_weights_2026.json`, versioned by exam year; the range width
     is carried as uncertainty).
  2. `P(pass) = logistic(k · (P̄ − MPS))` with slope `k = 14`. CFA never
     publishes the minimum passing standard, so MPS is carried as the band
     `[0.60, 0.70]`, never a point.
  3. Band: pessimistic corner = (P̄ with uncovered exam weight counted as
     zero, MPS = 0.70); optimistic corner = (P̄ + proxy widening,
     MPS = 0.60).
- **The give-up rule (written down, enforced in `metrics.ts`).** Readiness
  shows **no probability** unless all of:
  - ≥ **300 graded reviews** (revlog entries with an answer button),
  - ≥ **70% weighted topic coverage** (topics with ≥ 1 studied card),
  - ≥ **50 held-out performance probes answered**, and
  - the resulting band's half-width ≤ **0.20**.
    When abstaining it names each missing input (e.g. "0 held-out probes; the
    probe bank ships in a later phase", "Not studied yet: Derivatives, …").
    Phase 1 ships no probe bank, so Readiness **always abstains in real use**
    — that is the honest state, not a bug.
- **Test mode.** `dashboard?readinessTest=1` relaxes the gates so the
  pipeline can be exercised end-to-end, but every number is banner-labelled
  "TEST DATA — not a real prediction". The default that ships and demos is
  the abstaining gauge.
- **Best next study.** `argmax_s w(s) · (0.80 − Performance(s))` — the
  largest weighted gap, shown even while abstaining, so the gauge is useful
  without pretending to know the score.

## Data honesty

- The engine reports `fsrs_enabled`, per-topic studied counts, the graded
  review count and per-topic dispersion, so every abstention above is
  decided on real data, in one place, and displayed with its reasons.
- Cards without a `cfa::topic::*` tag are counted and shown as
  "not counted towards any topic" rather than silently dropped.
- All CFA-specific mappings (topic list, aliases, weights, τ, MPS band)
  live in the frontend as reviewable data files; the Rust engine stays
  exam-agnostic.
