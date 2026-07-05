# AI note — what we built, why, and what we skipped

The single-page AI summary the Friday rubric asks for ("a short note on what
AI you built, why, and what you skipped"). It consolidates what is otherwise
spread across `RUNTIME_AI_PLAN.md`, `RUNTIME_AI_NOTES.md`, `PHASE3_PLAN_V2.md`
and `brainlift.md`. Exam: **CFA Level I** (pass/fail).

## The one rule that shapes everything

**The review loop is AI-free by construction — it makes no model calls,
ever.** Grading, scheduling, and the three gauges (Memory / Performance /
Readiness) are pure Rust/deterministic code. AI appears in exactly two
places, both _outside_ the loop, both **default-off**, and neither can write
to grading, scheduling, or Readiness. Nothing an AI produces feeds a score.

## What we built

**1. Authoring-time card generation (`tools/speedrun/aig/`).** An offline
pipeline that drafts CFA Level I practice items and bakes them into a deck
_before_ any student sees them. Two generator families:

- **Parameterized numeric generators** (the guaranteed, shipped content):
  templated TVM / duration / inventory items with misconception-grounded
  distractors and an _independent recomputation_ of every answer. No model
  call — deterministic and always available.
- **An optional LLM drafter → independent critic → k-sample solver-consensus
  path** (`aig/models.py`) behind pluggable backends (`mock` default,
  `claude-cli`, or any OpenAI-compatible endpoint). Every drafted item must
  clear the same machine gates as the numeric ones before emission.

Every emitted item carries a **named source** (a grounding passage attached
by `aig/retrieval.py`), is validated by machine gates
(`aig/gates.py`: recomputation, self-consistency solve-check, feedback
completeness, and an 8-gram **leakage wall**), and ships tagged
`aig::ungraded` — **studyable but never fed to Readiness**.

_Why:_ covering a huge fact base by hand is the bottleneck for CFA; generation
scales it, but only if each card is checked and quarantined from the score.

**2. An optional runtime assistant (`tools/speedrun/assistant/`, desktop-only).**
Three read-only helpers on the dashboard: a post-session **error-pattern
debrief**, a **study coach** ("what should I do today?"), and a **tag→topic
suggester** for the Map-tags editor. Each is **grounded-or-abstain**: it may
only restate numbers the app already computed, defers to the gauge (never
states a pass probability while Readiness abstains), and falls back to the
deterministic view when AI is off, offline, or unsure. On Android these
affordances don't render at all.

_Why:_ narration and triage help a studier act on the numbers, but they are
strictly a _view_ over app-computed facts — never a new source of truth.

**3. BYO-deck onboarding (`tools/speedrun/onboard.py`, desktop-only,
default-off).** For imported/untagged decks, an AI pass may _fill blanks the
deterministic lexicon abstained on_ (topic tags), always previewed, undoable,
and tags-only.

## Every AI output is sourced, checked, and beaten against a baseline

- **Named source:** every generated item stores its grounding passage; the
  card cites it. No card asserts a fact with no traceable origin.
- **Held-out check with a cutoff:** the gold-set checker
  (`card_check.py`, challenge 7f) scores generated cards correct-useful /
  wrong / bad-teaching against a **cutoff frozen before scoring** and blocks
  failures (`eval/card_check_report.md`).
- **Beats a simpler method:** the retrieval side-by-side
  (`eval/retrieval_eval.md`) pits the grounding retriever against keyword
  and vector baselines. Honestly, in the default stdlib environment tuned
  **BM25 wins** (P@1 0.500 vs a stdlib vector arm 0.182 and RRF 0.364), so
  the shipped grounding _is_ BM25; the neural-fusion win is quoted only from
  an archived full-stack run, clearly caveated.
- **Injection-resistant:** `injection_eval.py` runs hidden-text payloads
  through every model-facing surface and shows the app never trusts model
  output (`eval/injection_eval_report.md`).
- **Works with AI off:** both apps compute Memory and the abstaining
  Readiness gauge with AI disabled and the network pulled
  (`eval/crash_test_report.md`, network-off section).

## What we skipped (and why)

- **No AI in the review loop or in any score.** A deliberate architectural
  boundary, not an omission: generated cards stay `aig::ungraded`, and the
  assistant is read-only. This is the compensating control for accepting a
  fully-automated (no human sign-off) generation pipeline.
- **No IRT / PFA / LKT performance backbone; no conformal/Venn-Abers score
  calibration** (plan item C6). These are unidentifiable for a single sparse
  learner (n=1); the shipped Readiness backstop is the abstention gate plus a
  band-width floor, and the alternatives are cited as future work.
- **No same-family independence claim for the LLM critic.** The default
  `claude-cli` pairing (sonnet drafter / haiku critic) shares a model family,
  which _weakens_ the independence of the check; this is disclosed in
  `aig/models.py` and the honest fix (a cross-family critic) is noted.
- **Dense/neural retrieval is opt-in only** (`SPEEDRUN_DENSE=1`): the
  torch/sentence-transformers stack is ABI-fragile on this host, so the
  guaranteed path is stdlib BM25 and the full-stack run is archived.
- **The runtime assistant is desktop-only.** Porting the read-only helpers to
  Android was out of scope; the phone deliberately renders no AI UI.

## Where to look

Generation pipeline & gates: `tools/speedrun/aig/`, `ITEM_SCHEMA.md`.
Runtime assistant spec: `RUNTIME_AI_PLAN.md`. Model definitions & the give-up
rule: `MODEL_DESCRIPTIONS.md`. AI-safety evidence:
`tools/speedrun/eval/{card_check,retrieval_eval,injection_eval}_report.md`.
Learning-science grounding: `brainlift.md`.
