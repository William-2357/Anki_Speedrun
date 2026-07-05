# Probe harness report

Generated: 2026-07-04T13:12:17+00:00 — modes: validation, leakage-scan, self-test

## Bank validation

PASS — 70 records, 35 concepts x 2 variants, 50 performance / 20 calibration (concept-disjoint), all 10 topics covered.

Variant divergence: max stem Jaccard 0.5294 (concept c33) < 0.7 threshold.

## Leakage scan (8-gram wall, both directions)

CLEAN — 70 probes vs 7 sources (reference PDF included).

## Self-test (synthetic, seeded)

Seed 20260704; 8 internal checks passed.

- readiness inputs: x=29 of n=50 delayed performance outcomes
- bridge gap (memory − delayed performance): 0.2128
- calibration: n=16, raw log-loss 0.811411 → calibrated 0.677617 at T=4.335954

## Honesty notes

- Outcomes use the FIRST graded answer per probe card; delayed means >= 7 days after the cluster's last non-probe study touch; never-studied clusters count as delayed and carry no fabricated lag.
- Only the performance pool feeds Readiness; calibration is fit on the disjoint calibration pool (no circularity).
- Calibration abstains below 10 delayed outcomes; the record's scores are in-sample post-temperature (disclosed).
