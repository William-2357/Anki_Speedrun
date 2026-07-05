# Memory calibration report

Generated: 2026-07-05T05:28:46+00:00 — modes: self-test, collection

## Formula provenance

`R(t) = (1 + factor * t / S) ** (-decay)`, `factor = 0.9 ** (-1/decay) - 1`, t in fractional days (seconds / 86400).

- fsrs crate 5.2.0 `src/inference.rs:60-63` (`current_retrievability`) and `src/inference.rs:512-519` (`current_retrievability_seconds`, the seconds/86400 day arithmetic); same curve as `src/model.rs:52-56`.
- rslib uses exactly this path for its own retrievability displays: `rslib/src/stats/graphs/retrievability.rs:33-34`.
- stability/difficulty/decay come from the ENGINE (`Collection.compute_memory_state`, rslib `scheduler/fsrs/memory_state.rs:360`), recomputed from the truncated revlog — this tool never reimplements the memory-state update.
- FSRS-6 default decay 0.1542 (`src/inference.rs:25`), pinned by the unit tests.

## Collection run

Path: `out/speedrun_eval/memcal/real_collection_run.anki2` — 638 revlog rows, 638 eligible graded reviews.

### Split rule

chronological cutoff at the graded-review timestamp quantile 0.75 (last ~25% held out), stepped earlier by 0.05 (floor 0.5) only if needed to reach >= 50 held-out first-post-cutoff-per-card observations; observed recall = ease > 1; eligibility mirrors rslib has_rating_and_affects_scheduling (graded, non-manual, non-cramming).

- cutoff: 2026-07-03T19:44:19+00:00 (ms 1783107859215), quantile used 0.75 (adaptive; tried [{'quantile': 0.75, 'observations': 67}])
- train side: 478 reviews, recall rate 0.945607
- post-cutoff: 160 eligible rows on 101 cards; 34 cards skipped (first seen post-cutoff), 3 skipped (card deleted, orphan revlog) -> 64 held-out observations (first post-cutoff review per card)
- leakage guard: PASSED — 0 post-cutoff revlog rows, 0 cards with surviving FSRS state in the truncated copy (160 rows deleted, 25 cards stripped)
- params training on truncated copy: {'status': 'engine_kept_defaults', 'fsrs_items': 46, 'note': 'engine trained on 46 items but kept the current (default) parameters — optimized params did not beat them on training log-loss; a trained tier would duplicate the defaults tier'}

### Tier: fsrs6-defaults

Params: FSRS-6 default parameters (no user fit; calibration of the shipped default model)

- n = 64 held-out observations (0 skipped: engine returned no memory state)
- Brier 0.000673 (95% CI 0.000519–0.000826, 2000 seeded bootstrap resamples, seed 20260704)
- log-loss 0.020503 (predictions clamped to [1e-06, 1-1e-06])
- ECE 0.020159 (10 equal-width bins)
- observed recall 1.0 vs mean predicted 0.979841
- baseline constant p=0.945607 (train recall rate): Brier 0.002959, log-loss 0.055929
- baseline chance p=0.5: Brier 0.25, log-loss 0.693147
- CAVEAT: degenerate holdout: zero lapses among the held-out reviews, so the evidence is one-sided (miscalibration toward over-prediction cannot show up)
- CAVEAT: all predictions fall into a single bin; the reliability curve is a single point, not a curve

| bin        | n  | mean predicted | observed |
| ---------- | -- | -------------- | -------- |
| [0.0, 0.1) | 0  | -              | -        |
| [0.1, 0.2) | 0  | -              | -        |
| [0.2, 0.3) | 0  | -              | -        |
| [0.3, 0.4) | 0  | -              | -        |
| [0.4, 0.5) | 0  | -              | -        |
| [0.5, 0.6) | 0  | -              | -        |
| [0.6, 0.7) | 0  | -              | -        |
| [0.7, 0.8) | 0  | -              | -        |
| [0.8, 0.9) | 0  | -              | -        |
| [0.9, 1.0] | 64 | 0.979841       | 1.0      |

Chart: `memory_calibration_chart.svg` (reliability diagram + prediction histogram, same directory as this report).

## Self-test (synthetic, seeded)

Seed 20260704; 60 cards, 432 synthetic reviews; 9 internal checks passed.

- curve: R(0)=1, R(S)=0.9 for any decay, monotone decreasing
- brier/log-loss match hand-computed values
- binning and ECE match a hand-computed fixture
- bootstrap is deterministic under a fixed seed
- holdout: first-post-cutoff-per-card, manual/cram ignored, post-cutoff-only cards skipped
- leakage guard: zero post-cutoff rows in the truncated copy
- leakage guard: rejects a deliberately contaminated copy
- synthetic pipeline: finite metrics, predictions in [0,1], Brier beats chance on every tier
- chart: SVG renders with diagonal + histogram structure

- trained-on-past: n=50, Brier 0.233855 (chance 0.25), log-loss 0.718517, ECE 0.190286
- fsrs6-defaults: n=50, Brier 0.216227 (chance 0.25), log-loss 0.682301, ECE 0.163821

## Honesty notes

- Memory states and parameters come from the engine on a truncated copy; predictions never see post-cutoff data. The independent leakage guard re-checks the copy and fails the run on any hit.
- Only the FIRST post-cutoff review per card is scored; later reviews depend on held-out history.
- Elapsed time is revlog-to-revlog fractional days (seconds/86400), the engine's own display-path arithmetic; FSRS TRAINS on rollover-aware integer days, so same-day boundaries can differ slightly from the scheduler's internal day counting (disclosed approximation, matching Anki's shipped retrievability display).
- The trained tier refits params on pre-cutoff data only; user params from the source collection are never used (they saw the holdout). When training is impossible the defaults tier is the only row — a calibration test of the shipped default model.
- log-loss clamps predictions at epsilon 1e-06; Python f64 vs engine f32 differs by ~1e-6.
- Same-day learning steps are handled by the engine's FSRS-6 short-term memory path (memory states are engine-computed); no short-term reimplementation exists in this tool.
