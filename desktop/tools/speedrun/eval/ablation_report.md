# Ablation report - Anki Speedrun Phase 3 M4 (SIMULATED learner - see ablation_real_report.md for the real-collection observational companion)

> **SIMULATION - READ THIS FIRST.** SIMULATION ONLY - every number below comes from a synthetic learner model, not from human study data.
> Learner model: single hand-specified exponential-forgetting model (n=1 learner model); see the module docstring of ablation.py for the exact equations.
> Descriptive, not inferential: no hypothesis tests; mean +/- SD across seeded replications of the same model.
> Content is derived from the real 72-card CFA sample deck's topic/cluster structure, scaled by synthetic paraphrase variants, plus synthetic homonym clusters and ladder items.
>
> seed=20260704, days=90, budget=40/day, replications=20. All cells are mean +/- SD across replications. Descriptive, not inferential.

## Pre-registered primary comparison (the main number, stated ahead)

Stated ahead of the run: the primary comparison is full_on vs vanilla on delayed-Performance (held-out probe accuracy >= 7 simulated days after last study). Every other number in this report is exploratory.

**full_on 0.839 +/- 0.036 vs vanilla 0.726 +/- 0.053 on delayed-Performance; paired delta = +0.113 +/- 0.062 (n=20 replications).**

Descriptive, not inferential: one simulated learner model (n=1), paired by shared per-replication content order; no hypothesis test is performed or implied.

## Arms (exploratory beyond the primary comparison)

| arm                   | role     | Memory (mean recall) | delayed-Performance | Readiness Brier | confusion-error rate | exam pass rate |
| --------------------- | -------- | -------------------- | ------------------- | --------------- | -------------------- | -------------- |
| vanilla               | named    | 0.859 +/- 0.025      | 0.726 +/- 0.053     | 0.369 +/- 0.336 | 0.112 +/- 0.028      | 0.55           |
| contrast_on           | named    | 0.873 +/- 0.022      | 0.786 +/- 0.052     | 0.244 +/- 0.231 | 0.047 +/- 0.018      | 1.00           |
| fade_on               | named    | 0.882 +/- 0.005      | 0.734 +/- 0.039     | 0.396 +/- 0.222 | 0.110 +/- 0.028      | 1.00           |
| full_on               | named    | 0.896 +/- 0.003      | 0.839 +/- 0.036     | 0.123 +/- 0.198 | 0.043 +/- 0.013      | 1.00           |
| cross_topic_leakage   | named    | 0.874 +/- 0.020      | 0.779 +/- 0.048     | 0.192 +/- 0.225 | 0.057 +/- 0.020      | 1.00           |
| allocation_on         | internal | 0.879 +/- 0.007      | 0.750 +/- 0.036     | 0.319 +/- 0.224 | 0.089 +/- 0.023      | 1.00           |
| full_minus_contrast   | internal | 0.889 +/- 0.005      | 0.791 +/- 0.047     | 0.230 +/- 0.201 | 0.087 +/- 0.029      | 1.00           |
| full_minus_fade       | internal | 0.884 +/- 0.008      | 0.827 +/- 0.035     | 0.160 +/- 0.203 | 0.032 +/- 0.020      | 1.00           |
| full_minus_allocation | internal | 0.895 +/- 0.004      | 0.824 +/- 0.040     | 0.043 +/- 0.048 | 0.044 +/- 0.023      | 1.00           |

Memory = plain mean recall probability over all items at the end of the horizon (blueprint-weighted variant in the JSON). delayed-Performance = held-out probe accuracy >= 7 days after last study. Readiness Brier = (final strict-gauge P(pass) - simulated outcome)^2, lower is better. Confusion-error rate = share of final probes answered with a confusable sibling.

## Per-SPOV marginal contributions (exploratory)

| feature    | vs vanilla: Memory | vs vanilla: delayed-Perf | within full_on: Memory | within full_on: delayed-Perf |
| ---------- | ------------------ | ------------------------ | ---------------------- | ---------------------------- |
| contrast   | +0.013             | +0.060                   | +0.007                 | +0.049                       |
| fade       | +0.023             | +0.008                   | +0.012                 | +0.012                       |
| allocation | +0.020             | +0.024                   | +0.001                 | +0.015                       |

"vs vanilla" = (single-feature arm) - vanilla; "within full_on" = full_on - (full_on minus that feature). Paired per-replication deltas; SDs in the JSON.

## Cross-topic leakage arm ([R8])

cross_topic_leakage spends adjacency slots on same-name pairs across topics (wasted pairs/replication: 303.4 vs contrast_on 11.3); the model grants those pairs no discrimination. Result: delayed-Performance 0.779 +/- 0.048 vs contrast_on 0.786 +/- 0.052; trained discrimination 0.650 +/- 0.018 vs 0.714 +/- 0.018.

## Abstention arm: lenient vs strict gate (the honesty cost)

Not a scheduling arm: for each arm's trajectory, what the retired lenient gate (>=15 reviews, >=1% coverage) would have emitted vs the strict [R1] gate (>=300 reviews, >=70% weighted coverage, >=50 delayed probes, half-width <= 0.20). overclaim_fraction = share of simulated days where lenient emitted a number while strict abstained; overclaim_brier = Brier of exactly those lenient emissions against the simulated exam outcome (the honesty cost of over-claiming).

| arm                   | lenient emit days | strict emit days | overclaim fraction | overclaim Brier | strict first emit day |
| --------------------- | ----------------- | ---------------- | ------------------ | --------------- | --------------------- |
| vanilla               | 89.0              | 61.0             | 0.311              | 0.484 +/- 0.444 | 29.0                  |
| contrast_on           | 89.0              | 61.0             | 0.311              | 0.876 +/- 0.011 | 29.0                  |
| fade_on               | 89.0              | 61.0             | 0.311              | 0.856 +/- 0.007 | 29.0                  |
| full_on               | 89.0              | 61.0             | 0.311              | 0.853 +/- 0.007 | 29.0                  |
| cross_topic_leakage   | 89.0              | 61.0             | 0.311              | 0.875 +/- 0.010 | 29.0                  |
| allocation_on         | 89.0              | 61.0             | 0.311              | 0.874 +/- 0.008 | 29.0                  |
| full_minus_contrast   | 89.0              | 61.0             | 0.311              | 0.859 +/- 0.006 | 29.0                  |
| full_minus_fade       | 89.0              | 61.0             | 0.311              | 0.872 +/- 0.007 | 29.0                  |
| full_minus_allocation | 89.0              | 61.0             | 0.311              | 0.852 +/- 0.006 | 29.0                  |

## Limitations (read before quoting any number)

- SIMULATION, not human data: all effects are properties of the documented learner model and its constants.
- n=1 learner model: a single set of forgetting/interference equations; real learners vary in ways this cannot capture.
- Descriptive, not inferential: replications share the model, so SDs describe seed noise, not population uncertainty; no significance claims are made.
- The exam ground truth is the model's own expected score against a fixed MPS center (0.715); CFA never publishes the MPS.
- Probe evidence uses a 45-day recency window (simulation-only choice, disclosed in config); the shipped gate counts all answered probes because its bank is answered once, near the exam.
- Probe items are simulated paraphrases of studied material; real held-out probes (the 30x2 set) are a separate milestone (M3).

Model equations, constants and content derivation: module docstring of `tools/speedrun/ablation.py`. Engine passes mirrored: `rslib/src/scheduler/queue/builder/{contrast,fade,allocation}.rs`.
