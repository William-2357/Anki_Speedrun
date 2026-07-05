# Real-collection observational report - Anki Speedrun Phase 3 (companion to the SIMULATED ablation)

> **REAL-COLLECTION OBSERVATIONAL REPORT - this is NOT an ablation: one learner, no counterfactual arm, no random assignment; n=1 with 638 graded reviews. It cannot estimate the feature effects the simulation estimates; it exists to ground the simulated claims in what real usage exists and to be re-run as real data accumulates.**
> One real learner, one realized history: counterfactual arms (what vanilla/full_on WOULD have produced on the same days) do not exist in this data and are not fabricated.
> Simulated companion: ablation_report.{json,md} carries the actual (simulated) ablation with equal-budget arms.
> Sections abstain with the reason where the data permits no number; nothing here is imputed or guessed.
>
> Collection: `out/speedrun_eval/real_collection.anki2` (sqlite3 URI mode=ro (read-only)); 3153 cards, 1582 notes, 638 revlog rows. Generated 2026-07-05T08:04:19+00:00.

## Speedrun feature state per deck-config preset

| preset                          | contrastScheduling | fadeEnabled | readinessAllocation | observational arm     | decks                | graded study reviews |
| ------------------------------- | ------------------ | ----------- | ------------------- | --------------------- | -------------------- | -------------------- |
| Default (id 1)                  | True               | False       | False               | contrast_on           | CFA Level 1, Default | 506                  |
| CFA Speedrun (id 1783015724534) | True               | True        | False               | full_minus_allocation | (none)               | 0                    |
| CFA Speedrun (id 1783065808803) | True               | False       | False               | contrast_on           | (none)               | 0                    |
| CFA Speedrun (id 1783099577024) | True               | False       | False               | contrast_on           | CFA Level 1 Speedrun | 65                   |

**Every preset with review history has the same feature state, so the real data observationally corresponds to the contrast_on arm of the simulation.**

Feature states are the deck-config values at read time; the collection stores no toggle history, so past reviews cannot be re-attributed to past states. 67 graded reviews sit on since-deleted cards and cannot be attributed to any preset.

## Memory-side outcomes (graded study reviews; probe answers excluded)

Overall: 609/638 correct - retention 0.9545, again-rate 0.0455 (n=638). 5 study days (2026-06-29 to 2026-07-04 UTC), 384 cards touched (335 still in the collection).

| topic               | n   | retention | again-rate | cards |
| ------------------- | --- | --------- | ---------- | ----- |
| fixed_income        | 65  | 0.9538    | 0.0462     | 20    |
| (card deleted)      | 67  | 0.9701    | 0.0299     | 49    |
| (no cfa::topic tag) | 506 | 0.9526    | 0.0474     | 315   |

Retention = share of graded study reviews answered above Again (ease > 1), the same correctness rule probe_harness applies. This is recognition-side memory, not delayed application.

## Delayed held-out probe outcomes ([R7], via probe_harness import)

ABSTAIN: 0 probe cards in the collection - the held-out probe deck has never been imported/answered; no real bridge measurement yet (probe cards: 0).

probe_harness.read_collection + compute_outcomes (imported, not reimplemented); delay rule mirrors rslib/src/readiness/probes.rs: first graded answer, delayed iff >= 7 days after the cluster's last non-probe study touch, never-studied counts as delayed.

## Readiness-calibration record

`speedrun:readinessCalibration`: ABSENT - no record - probe_harness --apply has never run (it refuses below 10 delayed calibration-pool outcomes).

## Contrast adjacency (observational)

Of 633 consecutive same-day study-review pairs, 62 were true contrast pairs (same cluster, different card; share 0.0979) and 0 were same-name-different-cluster (wasted) pairs. Contrast-enabled presets: Default (id 1), CFA Speedrun (id 1783015724534), CFA Speedrun (id 1783065808803), CFA Speedrun (id 1783099577024).

Mirrors the simulated report's adjacency notion: true = same-cluster different-card back-to-back within one day (trains discrimination, [R8]); wasted = same family name across different clusters. Current-state caveat above applies.

## What this report cannot say (read before quoting any number)

- OBSERVATIONAL, not causal: one real learner (n=1), one realized history; feature effects are NOT estimable from this data.
- Feature states are current-state only; Anki keeps no toggle history in the collection.
- Per-topic rows cover only notes tagged cfa::topic::*; untagged and since-deleted cards are reported in their own buckets, never guessed into topics.
- Retention here is study-side recognition accuracy (ease > 1 share), not the delayed application performance the held-out probe bank measures - the two must not be conflated.
- Power: 638 graded reviews over 5 study days; every number is descriptive and should be re-read as data accumulates.

Feature-state field names: `rslib/src/deckconfig/schema11.rs` / `proto/anki/deck_config.proto`. Outcome extraction: `tools/speedrun/probe_harness.py` (imported). The ablation itself (simulated): `ablation_report.md`.
