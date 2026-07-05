# Speedrun probe schema (v1) — the held-out, delayed paraphrase probe bank

One JSON object per probe, stored as JSON Lines in
`desktop/tools/speedrun/probes/probe_bank.jsonl`. This is the Phase 3 M3
bank (GRILLING C8 / [R7]): **hand-authored** application MCQs that
paraphrase studied material, answered under a **measured ≥7-day delay**,
producing the real `(x correct, n)` outcomes the Rust readiness backend
(`rslib/src/readiness/probes.rs`) consumes. The bank is the memory→
performance bridge proof (challenge 7d) and the calibration ground truth,
decoupled from the AI pipeline by design.

Producers/consumers:

- `probe_harness.py` **validates** the bank, runs the leakage wall, extracts
  real outcomes from a collection's revlog with the same delay rule as the
  Rust side, computes the bridge proof + calibration, and (with `--apply`)
  writes the calibration record the readiness RPC surfaces.
- `build_probe_deck.py` **builds** `cfa_probes.apkg` from the bank; it must
  hard-fail on any record this schema rejects.

## Record fields (`speedrun-probe-v1`)

| field        | type   | required | notes                                                                                                 |
| ------------ | ------ | -------- | ----------------------------------------------------------------------------------------------------- |
| `schema`     | string | yes      | literal `"speedrun-probe-v1"`                                                                         |
| `concept_id` | string | yes      | `c01`…`c35`; the tested concept. Both variants of a concept share it.                                 |
| `variant`    | string | yes      | `"a"` \| `"b"` — two REWORDED application scenarios of the same concept                               |
| `pool`       | string | yes      | `"performance"` \| `"calibration"`; MUST match the partition rule below                               |
| `topic`      | string | yes      | `cfa::topic::` suffix; one of the 10 official areas (list below)                                      |
| `cluster`    | string | yes      | `cluster::` suffix (e.g. `fi::duration`) of the studied material it paraphrases                       |
| `title`      | string | yes      | short unique human name (sort field)                                                                  |
| `stem`       | string | yes      | self-contained application scenario (computation or judgment), ≥ 15 words                             |
| `choices`    | object | yes      | exactly keys `A`,`B`,`C`, non-empty, pairwise distinct (CFA L1 format)                                |
| `correct`    | string | yes      | one of `A`,`B`,`C` — exactly one defensible answer                                                    |
| `rationale`  | string | yes      | why the correct answer is right AND why **each** wrong letter is wrong (must name both wrong letters) |
| `provenance` | object | yes      | `{"author": "hand", "date": "YYYY-MM-DD"}` — hand-authored, no generator, no gates                    |

Topic suffixes (all 10 must be covered by the bank): `ethics`,
`quantitative_methods`, `economics`, `financial_statement_analysis`,
`corporate_issuers`, `equity_investments`, `fixed_income`, `derivatives`,
`alternative_investments`, `portfolio_management`.

Cluster suffixes have ≥ 2 `::`-separated components and their first
component must agree with the topic (`fi::*` ⇒ `fixed_income`, `qm::*` or
`quant::*` ⇒ `quantitative_methods`, `econ::*` ⇒ `economics`, `fsa::*` ⇒
`financial_statement_analysis`, `corp::*` ⇒ `corporate_issuers`,
`equity::*` ⇒ `equity_investments`, `deriv::*` ⇒ `derivatives`, `alt::*` ⇒
`alternative_investments`, `pm::*` ⇒ `portfolio_management`, `ethics::*` ⇒
`ethics`).

**Never-studied clusters are allowed and honest.** Where a concept has no
studied cluster in the shipped decks (`cfa_sample_cards.py` +
`items/generated.jsonl`), the probe still carries a plausible `cluster::`
tag for its topic. The Rust delay rule counts a probe whose cluster has no
non-probe study reviews as _delayed_ (it cannot be recency-inflated) and
reports it separately (`never_studied`), so these probes measure
performance without any pretence of a study→probe lag. The bank documents
which clusters are of this kind; the harness reports the split.

## Bank-level invariants (enforced by `probe_harness.py` and the tests)

- Exactly **70 records = 35 concepts × 2 variants** (`a` and `b` each
  exactly once per concept), concept ids contiguous `c01`…`c35`.
- Both variants of a concept share `pool`, `topic` and `cluster`.
- Variant `b` must be a genuine rewording of variant `a`: same tested
  concept, different numbers and surface story. Enforced mechanically:
  token Jaccard similarity of the two stems (lowercased, alphanumeric
  tokens, small fixed stopword list removed) must be **< 0.7**.
- All 10 topics covered (this bank covers all 10 in _each_ pool).
- Titles unique across the bank.
- **Leakage wall (challenge 7e):** no 8-gram (token) overlap between any
  probe `stem`+`choices` and (a) `corpus/*.md`, (b) `items/*.jsonl`
  stems/prompts/cloze text, (c) the `aig/prompts.py` template text, nor in
  the reverse direction (no corpus/generator text quotes a probe). The
  local git-ignored CFA reference PDF is also checked when present.

## Pool partition (concept-disjoint, deterministic)

> `c01`…`c25` → `performance` (25 concepts = **50 items**)
> `c26`…`c35` → `calibration` (10 concepts = **20 items**)

Both variants of a concept always share its pool, so the pools are
**concept-disjoint**: no reworded twin of a calibration item ever sits in
the performance pool. Why: Readiness's point of record is _estimated_ on
performance-pool outcomes and _calibrated_ against the disjoint
calibration pool — calibrating a gauge against its own inputs would be
circular (PHASE3_PLAN_V2 M3). The rule is a fixed function of the concept
id (sort order), not a coin flip, so the partition is reproducible and can
never drift item-by-item.

The performance pool has exactly 50 items because the Rust give-up gate
([R1], `MIN_DELAYED_PROBES`) requires **≥ 50 delayed performance-pool
outcomes**: the gate is satisfiable only when every performance probe has
been answered under the delay rule — deliberately tight, never padded.

## Tagging (what `build_probe_deck.py` writes; what `probes.rs` reads)

Every note gets exactly these tags, mechanically derived:

- `probe::held_out` — marks the note as measurement, not study. The Rust
  side keys everything off this tag.
- `probe::pool::performance` **or** `probe::pool::calibration` — exactly
  one, from `pool`.
- `cfa::topic::<topic>`
- `cluster::<cluster>` — the cluster whose studied material the probe
  paraphrases; drives the delay rule.
- `probe::concept::<concept_id>` and `probe::variant::<a|b>` —
  bookkeeping so revlog analysis can pair variants.

**Never** `aig::graded`/`aig::ungraded` (probes are hand-authored, not
generated), **never** `rung::*` (probes sit outside the fade ladder and
must never be gated by it), **never** `interactivity::*`.

## Outcome + delay rule (must mirror `rslib/src/readiness/probes.rs`)

- The **outcome** of a probe card is its **first graded answer**
  (`ease > 0`): `Again` = incorrect, `Hard`/`Good`/`Easy` = correct
  (Anki's true-retention convention). Later answers are practice on a
  burned probe and never count.
- The outcome is **delayed** when the first answer came **≥ 7 days**
  (`MIN_PROBE_DELAY_DAYS`) after the last graded review of any
  **non-probe** card sharing the probe's cluster tag, measured from the
  revlog. Probe answers are never study touches — only non-probe cards
  are study evidence.
- A probe whose cluster was **never studied** counts as **delayed** and is
  reported separately; it carries no lag.
- Undelayed outcomes are logged and reported but excluded (immediate
  accuracy overstates transfer — Rohrer 2015 [R7]). Lags are reported as
  measured; a delay that was not measured is never claimed.
- Only **performance-pool** delayed outcomes feed the Readiness estimate
  `(x, n)`. Calibration-pool outcomes exist solely for the offline
  harness.

## Calibration record (`speedrun:readinessCalibration`)

Written by `probe_harness.py --collection PATH --apply` via pylib
`Collection.set_config`, read by `rslib` (`CalibrationRecord`, exact
snake_case shape — serde deserializes these five fields and no others):

```json
{
    "fitted_at": "YYYY-MM-DD",
    "brier": 0.19,
    "log_loss": 0.55,
    "n": 16,
    "temperature": 1.31
}
```

- `n` = calibration-pool probes with a **delayed** first answer (same
  delay rule; calibrating on undelayed answers would re-admit the recency
  inflation the rule exists to remove). The harness **refuses to fit**
  (abstains, writes nothing) when `n < 10`.
- Predictions being calibrated (documented proxy, computed only from
  information available before each probe's first answer): the probe's
  cluster's recent study accuracy — the fraction of the most recent ≤ 20
  graded non-probe reviews of that cluster before the answer, Laplace
  (add-one) smoothed, `(correct + 1) / (n_reviews + 2)`; a never-studied
  cluster predicts the 3-choice chance rate **1/3** exactly.
- `temperature` is the single scalar T fit by golden-section search on
  logits (`p' = sigmoid(logit(p) / T)`) minimizing log-loss on those
  pairs. `brier`/`log_loss` in the record are the **post-temperature**
  (calibrated) scores on the same pairs; the harness report carries the
  raw before-scores too. With one fitted scalar on a small n these
  in-sample scores are mildly optimistic — disclosed here and in the
  report, per the honesty contract.
- The default MPS band the readiness map uses is `[0.68, 0.75]`
  (`speedrun:passBand`, `{"low", "high"}`); the harness never touches it.

## Honesty invariants (why this bank can be trusted)

1. **Held-out hygiene.** Probe notes never feed Memory or coverage:
   `rslib/src/stats/mastery.rs` excludes `probe::held_out` notes from
   TopicMastery, and `readiness/mod.rs` subtracts probe answers from the
   graded-study-review gate. Probes are measurement, not study.
2. **Hand-authored.** Every record's provenance is
   `{"author": "hand", ...}`; no LLM drafted, critiqued or solved these
   items, so no generator prompt can leak them and they carry no `aig::*`
   tag (the `aig::ungraded` readiness exclusion is about _generated_
   items; probes are excluded from mastery by `probe::held_out` instead).
3. **Leakage-walled both directions** (see bank invariants above), so the
   probes cannot be paraphrases of the grounding corpus that generated
   study items, and no future generator input quotes a probe.
4. **Delay is measured, never asserted.** The harness reports the actual
   study→probe lag distribution from the revlog; never-studied clusters
   are labelled as such rather than given a fake lag.
5. **No circularity.** Estimation (performance pool) and calibration
   (calibration pool) are concept-disjoint by the deterministic rule
   above.

## Commands

From `desktop/` after a build:

```
# validate + leakage scan + self-test (CI default; stdlib only, no pylib)
python3 tools/speedrun/probe_harness.py

# real outcomes, bridge proof, calibration from a collection (read-only)
python3 tools/speedrun/probe_harness.py --collection path/to/collection.anki2

# additionally write speedrun:readinessCalibration into the collection
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/probe_harness.py \
    --collection path/to/collection.anki2 --apply

# build the deck (pylib required)
PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_probe_deck.py
```

Reports: `tools/speedrun/eval/probe_harness_report.json` (+ `.md` human
summary).
