# Card checker report (challenge 7f)

Generated: 2026-07-05T05:44:18+00:00 — command: `python3 card_check.py`

## Frozen cutoff (fixed BEFORE scoring; verbatim module constants)

Frozen on 2026-07-05 (tests pin these exact values):

> A card SHIPS only if classification == correct_useful AND its correctness is machine-verified (independent recomputation from the card's own generator metadata) or human-attested (hand-written gold card). wrong => BLOCKED (a wrong fact is worse than no card). bad_teaching => BLOCKED. A generated card whose correctness the machine cannot verify is unverifiable-by-machine => BLOCKED (generated cards must carry recomputation metadata to be shippable).

- `DUP_JACCARD = 0.55` — question+answer token-set
  Jaccard at/above this blocks as duplicate (vs batch and gold).
- `MIN_QUESTION_CHARS = 20`, `MIN_QUESTION_CONTENT_TOKENS = 3` — vagueness lint; plus no-question-mark/no-task-cue rule and the
  non-answer list; answer contained verbatim in question = trivial.
- `GOLD_MATCH_JACCARD = 0.75`, `ANSWER_AGREE_JACCARD = 0.2` — a card
  asking a gold question but answering differently is WRONG.
- `CONTRADICTION_OVERLAP = 0.35` — corpus
  negation/antonym flips need this much content overlap to fire.
- Classification priority: wrong beats bad_teaching; both block.

## Gold-set known-answer validation (confusion matrix)

50 hand-verified gold pairs + 15 seeded defects (gold/gold_set_v1.jsonl):

| seeded as | expected       | -> correct_useful | -> wrong | -> bad_teaching | blocked |
| --------- | -------------- | ----------------- | -------- | --------------- | ------- |
| gold      | correct_useful | 50                | 0        | 0               | 0       |
| wrong     | wrong          | 1                 | 4        | 0               | 4       |
| vague     | bad_teaching   | 0                 | 0        | 5               | 5       |
| duplicate | bad_teaching   | 0                 | 0        | 5               | 5       |

- Gold specificity: 50/50 hand-verified pairs classified correct_useful and shipped.
- Defect sensitivity (label-exact): wrong 4/5, vague 5/5, duplicate 5/5; 14/15 defects BLOCKED regardless of bucket.
- Misses (reported, not retuned):
  - `defect::wrong::05` expected wrong, got correct_useful (SHIPPED)

## The 50-card batch from ONE source (`duration.md`)

- Generators: param:mod_duration_from_mac_v1, param:duration_price_change_v1, param:macaulay_from_cashflows_v1 + 2 deterministic compare fixtures grounded in the same doc.
- Counts per generator/kind (parameter-space expansion beyond DEFAULT_COUNTS to reach 50 from one doc): duration_price_change/cloze=5, duration_price_change/mcq=6, duration_price_change/worked=5, macaulay_from_cashflows/cloze=5, macaulay_from_cashflows/mcq=6, macaulay_from_cashflows/worked=5, mod_duration_from_mac/cloze=5, mod_duration_from_mac/mcq=6, mod_duration_from_mac/worked=5
- Seed 20260705; mock/deterministic path; 50 of 50 generated items passed the standard pipeline gates (numeric, solve_check, rationale, leakage, grounding, schema) and form the checked batch.
- Retrieval attached an off-doc source on: duration.md/macaulay_from_cashflows/cloze/1, duration.md/macaulay_from_cashflows/cloze/2, duration.md/macaulay_from_cashflows/cloze/3, duration.md/macaulay_from_cashflows/cloze/4, duration.md/macaulay_from_cashflows/cloze/5, duration.md/macaulay_from_cashflows/mcq/1, duration.md/macaulay_from_cashflows/mcq/2, duration.md/macaulay_from_cashflows/mcq/3, duration.md/macaulay_from_cashflows/mcq/4, duration.md/macaulay_from_cashflows/mcq/5, duration.md/macaulay_from_cashflows/mcq/6 (BM25 imperfection, reported; declared generator grounding is duration.md for all cards).

### Headline counts (frozen cutoff applied)

- **correct-and-useful: 11**
- **wrong: 0**
- **correct-but-bad-teaching: 39**
- BLOCKED: 41 of 50 (all wrong + all bad_teaching + 2 generated-but-unverifiable); SHIPPED: 9.

### Per-card results

| id                                           | kind    | label          | verified     | shipped | reason (first)                                                                                   |
| -------------------------------------------- | ------- | -------------- | ------------ | ------- | ------------------------------------------------------------------------------------------------ |
| duration.md/mod_duration_from_mac/worked/1   | worked  | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/mod_duration_from_mac/worked/2   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.72 vs batch card duration.md/mod_duration_from_mac/worked/1                       |
| duration.md/mod_duration_from_mac/worked/3   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.74 vs batch card duration.md/mod_duration_from_mac/worked/1                       |
| duration.md/mod_duration_from_mac/worked/4   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.75 vs batch card duration.md/mod_duration_from_mac/worked/1                       |
| duration.md/mod_duration_from_mac/worked/5   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.77 vs batch card duration.md/mod_duration_from_mac/worked/1                       |
| duration.md/mod_duration_from_mac/cloze/1    | cloze   | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/mod_duration_from_mac/cloze/2    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.67 vs batch card duration.md/mod_duration_from_mac/cloze/1                        |
| duration.md/mod_duration_from_mac/cloze/3    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.61 vs batch card duration.md/mod_duration_from_mac/cloze/1                        |
| duration.md/mod_duration_from_mac/cloze/4    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.61 vs batch card duration.md/mod_duration_from_mac/cloze/1                        |
| duration.md/mod_duration_from_mac/cloze/5    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.61 vs batch card duration.md/mod_duration_from_mac/cloze/1                        |
| duration.md/mod_duration_from_mac/mcq/1      | mcq     | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/mod_duration_from_mac/mcq/2      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.68 vs batch card duration.md/mod_duration_from_mac/mcq/1                          |
| duration.md/mod_duration_from_mac/mcq/3      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.73 vs batch card duration.md/mod_duration_from_mac/mcq/1                          |
| duration.md/mod_duration_from_mac/mcq/4      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.68 vs batch card duration.md/mod_duration_from_mac/mcq/1                          |
| duration.md/mod_duration_from_mac/mcq/5      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.73 vs batch card duration.md/mod_duration_from_mac/mcq/1                          |
| duration.md/mod_duration_from_mac/mcq/6      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.68 vs batch card duration.md/mod_duration_from_mac/mcq/1                          |
| duration.md/duration_price_change/worked/1   | worked  | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/duration_price_change/worked/2   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.77 vs batch card duration.md/duration_price_change/worked/1                       |
| duration.md/duration_price_change/worked/3   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.74 vs batch card duration.md/duration_price_change/worked/1                       |
| duration.md/duration_price_change/worked/4   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.76 vs batch card duration.md/duration_price_change/worked/1                       |
| duration.md/duration_price_change/worked/5   | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.78 vs batch card duration.md/duration_price_change/worked/1                       |
| duration.md/duration_price_change/cloze/1    | cloze   | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/duration_price_change/cloze/2    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.63 vs batch card duration.md/duration_price_change/cloze/1                        |
| duration.md/duration_price_change/cloze/3    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.65 vs batch card duration.md/duration_price_change/cloze/1                        |
| duration.md/duration_price_change/cloze/4    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.65 vs batch card duration.md/duration_price_change/cloze/1                        |
| duration.md/duration_price_change/cloze/5    | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.57 vs batch card duration.md/duration_price_change/cloze/1                        |
| duration.md/duration_price_change/mcq/1      | mcq     | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/duration_price_change/mcq/2      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.70 vs batch card duration.md/duration_price_change/mcq/1                          |
| duration.md/duration_price_change/mcq/3      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.68 vs batch card duration.md/duration_price_change/mcq/1                          |
| duration.md/duration_price_change/mcq/4      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.72 vs batch card duration.md/duration_price_change/mcq/1                          |
| duration.md/duration_price_change/mcq/5      | mcq     | bad_teaching   | verified     | BLOCKED | trivial: answer contained verbatim in question                                                   |
| duration.md/duration_price_change/mcq/6      | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.72 vs batch card duration.md/duration_price_change/mcq/1                          |
| duration.md/macaulay_from_cashflows/worked/1 | worked  | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/macaulay_from_cashflows/worked/2 | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.56 vs batch card duration.md/macaulay_from_cashflows/worked/1                     |
| duration.md/macaulay_from_cashflows/worked/3 | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.61 vs batch card duration.md/macaulay_from_cashflows/worked/1                     |
| duration.md/macaulay_from_cashflows/worked/4 | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.61 vs batch card duration.md/macaulay_from_cashflows/worked/1                     |
| duration.md/macaulay_from_cashflows/worked/5 | worked  | bad_teaching   | verified     | BLOCKED | duplicate: J=0.59 vs batch card duration.md/macaulay_from_cashflows/worked/1                     |
| duration.md/macaulay_from_cashflows/cloze/1  | cloze   | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/macaulay_from_cashflows/cloze/2  | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.80 vs batch card duration.md/macaulay_from_cashflows/cloze/1                      |
| duration.md/macaulay_from_cashflows/cloze/3  | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.70 vs batch card duration.md/macaulay_from_cashflows/cloze/1                      |
| duration.md/macaulay_from_cashflows/cloze/4  | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.75 vs batch card duration.md/macaulay_from_cashflows/cloze/1                      |
| duration.md/macaulay_from_cashflows/cloze/5  | cloze   | bad_teaching   | verified     | BLOCKED | duplicate: J=0.67 vs batch card duration.md/macaulay_from_cashflows/cloze/1                      |
| duration.md/macaulay_from_cashflows/mcq/1    | mcq     | correct_useful | verified     | yes     |                                                                                                  |
| duration.md/macaulay_from_cashflows/mcq/2    | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.77 vs batch card duration.md/macaulay_from_cashflows/mcq/1                        |
| duration.md/macaulay_from_cashflows/mcq/3    | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.86 vs batch card duration.md/macaulay_from_cashflows/mcq/1                        |
| duration.md/macaulay_from_cashflows/mcq/4    | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.83 vs batch card duration.md/macaulay_from_cashflows/mcq/1                        |
| duration.md/macaulay_from_cashflows/mcq/5    | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.83 vs batch card duration.md/macaulay_from_cashflows/mcq/1                        |
| duration.md/macaulay_from_cashflows/mcq/6    | mcq     | bad_teaching   | verified     | BLOCKED | duplicate: J=0.83 vs batch card duration.md/macaulay_from_cashflows/mcq/1                        |
| duration.md/compare/1                        | compare | correct_useful | unverifiable | BLOCKED | unverifiable-by-machine: generated card without recomputation metadata (frozen cutoff blocks it) |
| duration.md/compare/2                        | compare | correct_useful | unverifiable | BLOCKED | unverifiable-by-machine: generated card without recomputation metadata (frozen cutoff blocks it) |

### Oddities observed (reported, not retuned)

- `duration.md/duration_price_change/mcq/5`: trivial: answer contained verbatim in question; duplicate: J=0.76 vs batch card duration.md/duration_price_change/mcq/1

These are frozen-rule hits inspected after the run: e.g. a 100 bp yield move makes the duration-only answer's magnitude equal the stem's stated modified duration, so the answer digits appear verbatim in the question and the triviality rule fires - a borderline but defensible block (the answer can be read off the stem without computing). Left as-is per the no-retune rule.

## The block is real

- Ship-gate run on the batch file: exit code 1 (non-zero because blocked cards exist); sidecar with blocked ids + reasons: `/Users/william/Anki_Speedrun/desktop/out/speedrun_eval/cardcheck/source_batch_50.jsonl.blocked.json`.
- Integration for the pipeline owners (files not owned by this workstream): in run_pipeline.py, before writing items JSONL, drop items whose `card_check.check_batch(...)` result is blocked — or equivalently run `python3 card_check.py --cards items/generated.jsonl` in the build and fail on non-zero exit. One line each; the checker deliberately does not modify those files.

## Honesty notes

- The backend is the mock/deterministic path: the 50 cards are parameterized-generator output (plus 2 hand-authored compare fixtures), so these numbers measure the generator+checker SYSTEM, not an LLM's error rate. The same command re-runs against a real backend via run_pipeline.py --backend; that path is unverified here.
- Wrong-fact residual risk: the checker verifies numeric cards by independent recomputation ONLY when generator metadata is present, and free-text facts only when they closely mirror a gold or corpus statement (same question / one-sided negation or antonym flip). defect::wrong::05 is seeded precisely to show the miss: a hand-written wrong number with no gold twin passes. That residual risk is why generated decks stay aig::ungraded and never feed Readiness, and why unverifiable GENERATED cards are blocked.
- The duplicate-heavy batch result is expected, not a tuning artifact: one corpus doc supports only 3 generators x 3 kinds + 2 compare fixtures = 11 distinct templates, so asking for 50 cards from one source forces numeric re-skins of the same templates. The checker correctly refuses the redundancy; thresholds were frozen before the run and NOT retuned afterward.
- Gold-set correctness is author-verified against standard Level I material (each quantitative answer re-derivable from its own rationale; tests re-compute a spot-check set); no licensed CFAI text was copied. Hand-written gold cards ship on human attestation - the machine records them as unverifiable, which the cutoff permits for human-origin cards only.
- Blocked cards are actually blocked: the ship-gate exit code is non-zero and the sidecar lists blocked ids; wiring it into the deck build (one line, owned by run_pipeline/build_ladder_deck) keeps them out of any emitted deck.
