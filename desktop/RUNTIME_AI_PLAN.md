# Runtime-AI Plan — the Assistant Layer (outside the review loop)

> Companion to `brainlift.md`, `PHASE2_PLAN_V2.md`, `PHASE3_PLAN_V2.md`, `DASHBOARD.md`,
> `MODEL_DESCRIPTIONS.md`, and `PRD.md`. This plan adds **three runtime-AI features that sit
> OUTSIDE the review loop**: (A) a post-session **error-pattern debrief**, (B) a **Study Coach**,
> and (C) an AI **tag→topic mapping suggester**.
>
> **BYO / imported-deck onboarding (formerly "Feature D") is deferred to Phase 3.** It now lives in
> `PHASE3_PLAN_V2.md` **M5 (Generalization / BYO decks)**, next to the AI edge-sourcing it depends on,
> and is **out of scope for this plan**.
>
> **Owner:** William. **Intended executor:** a Fable-powered agent (see `RUNTIME_AI_PLAN` handoff
> prompt). **Status:** PLANNED 2026-07-03.
>
> **Platform scope: desktop only.** AnkiDroid / Android is **out of scope** — no AI runs on mobile,
> and this plan modifies **no Android source files**. The single Android obligation is _graceful
> degradation_: features B and C add UI to the dashboard Svelte page, which is also served on Android
> from the `.aar` (`DASHBOARD.md`), so that page must **hide its AI affordances when the desktop host
> bridge is absent** — shipping no broken buttons, not adding mobile support.

---

## The one invariant this plan must not break

The project's load-bearing rule (from `PHASE2_PLAN_V2.md`) is that **the review loop is AI-free by
construction — no runtime AI calls, ever**. The deeper reason is honesty + a clean three-arm
ablation: AI must never _write_ to the three things that make the app trustworthy —

1. **grading** (the Again/Good self-grade),
2. **scheduling / gating** (FSRS, the fade ladder, contrast adjacency), and
3. the **Readiness** gauge (and, by [R24], anything ungraded must never feed it).

**All three features here only ever _read_ those and _narrate / suggest_.** None of them touches the
reviewer, the scheduler, or the Rust engine's answer path. Every feature is **default-OFF**, gated by
a synced `speedrun:` collection-config key, and **degrades to the existing deterministic view when AI
is off, offline, or erroring**. Where a feature's suggestion could influence a gauge input (C's topic
attribution; D's tags), a **human confirms before anything is persisted**, and generated study items
are tagged `aig::ungraded` so they can never feed Readiness ([R24]).

If any milestone below cannot be done without writing to grading/scheduling/Readiness, **stop and
re-scope** — that is the signal it belongs behind the review-loop wall, which is out of scope here.

---

## Why these three are safe (and on-thesis)

| # | Feature              | Reads                           | Writes (human-confirmed)                 | Touches engine? | Touches Readiness?                                     |
| - | -------------------- | ------------------------------- | ---------------------------------------- | --------------- | ------------------------------------------------------ |
| A | Post-session debrief | revlog + note tags              | nothing                                  | no              | no (read-only narration)                               |
| B | Study Coach          | the dashboard model             | nothing                                  | no              | no (defers to the gauge, never invents P(pass))        |
| C | Tag→topic suggester  | `unmapped_tags` + sample fronts | `speedrun:tagTopicMap` (after user Save) | no              | no (deterministic map unchanged; abstention preserved) |

A (debrief) is the most on-thesis: SPOV 3 is entirely about **discrimination among confusables**, and
the debrief narrates a confusion signal the repo **already computes** from the revlog
(`tools/speedrun/aig/confusability.py`). (The deferred BYO onboarding — formerly Feature D — is the
concrete realization of `PHASE3_PLAN_V2.md` **M5 — Generalization (BYO / untagged decks)**; see the
banner.)

---

## Executing this plan (methodology for the implementing agent)

This spec is deliberately outcome-oriented: it fixes the **what**, the **invariants**, and the
**acceptance criteria**, and leaves the **how** to you.

- **Act when you have enough; don't overplan.** Decide the implementation and start. When two
  approaches are equivalent, pick one and note it in a sentence rather than surveying alternatives.
- **Simplest thing that works.** Don't refactor neighbouring code, add abstractions, or handle cases
  that cannot occur. Validate only at real boundaries — user input, the model API, imported decks —
  and trust internal code and framework guarantees. This is greenfield, so no compatibility shims.
- **Build behind the wall.** Re-read _"The one invariant"_ before each feature. If a step seems to
  need the reviewer, scheduler, or Readiness write path, stop and flag it — that means it's
  mis-scoped for this plan.
- **Self-verify on a cadence, with fresh eyes.** After each milestone, check it against that
  milestone's **Acceptance** line using a **fresh-context verifier subagent** (a clean subagent
  catches more than self-critique) plus the concrete commands in _Build / verify_. Report only what a
  tool result from this session backs; if something failed or was skipped, say so with the output.
- **Use parallel subagents.** Once the shared infra (S1–S4) lands, features A–C are largely
  independent — delegate independent slices to subagents and keep working while they run; step in if
  one drifts or is missing context.
- **Keep a lessons file** at `desktop/RUNTIME_AI_NOTES.md`: one lesson per entry, a one-line summary
  first, recording corrections and confirmed approaches and why they mattered. Update rather than
  duplicate; delete what proves wrong; don't record what this spec or the repo already states.
- **Checkpoint sparingly.** Proceed autonomously on reversible work that follows from this plan.
  Pause only for a destructive or irreversible action, a real scope change, or input only the owner
  can provide.

---

## Shared infrastructure (build this first)

### S1 — AI backend adapter (reuse, do not reinvent)

Reuse the pluggable backends already in `tools/speedrun/aig/models.py`:

- `MockBackend` — deterministic, offline (the **default in every test**),
- `ClaudeCliBackend` — shells out to `claude -p --model <m>`,
- `OpenAICompatBackend` — POSTs to `$OPENAI_BASE_URL/chat/completions` with `$OPENAI_API_KEY`,
- `parse_json_reply()` — fence-tolerant JSON extraction.

A/B/C need only **single-call grounded completion**; the drafter/critic/solver trio (`make_llm_path`)
is **not** used here — it stays reserved for the deferred Phase 3 BYO onboarding.

Factor a small **`tools/speedrun/assistant/` package** (new) that:

- builds a backend from config/env (`make_backend(name)`), defaulting to `mock`;
- exposes `grounded_complete(system, facts: dict, *, schema) -> dict | None` that (1) passes the
  model **only facts already computed by the app**, (2) demands JSON, (3) **abstains (returns
  `None`) whenever the reply is unparseable, low-confidence, or asserts anything not present in
  `facts`** — grounded generation, mirroring the dashboard "abstain, don't invent" rule;
- has a hard timeout + returns `None` on any error (caller falls back to the deterministic view).

### S2 — Host bridge for the webview features (B, C)

The dashboard is an **API-enabled `AnkiWebView`** (`AnkiWebViewKind.CFA_DASHBOARD`, see
`DASHBOARD.md` → _Access_). The Coach/Suggester run from that page but their model call must **not**
go through the Rust backend. Add a **desktop host route** (a `mediasrv` POST handler or a `pycmd`
message handler registered from `qt/aqt/`) named e.g. `speedrun_assistant` that receives the
already-computed facts JSON from the page, calls S1, and returns text/JSON. **Desktop only.**
AnkiDroid has no `pycmd` / host-bridge parity and is **out of scope**; because the dashboard page is
also served on Android, it must feature-detect the bridge and **hide the AI affordances when absent**
so Android ships no broken buttons (graceful degradation, not an Android port).

### S3 — Toggles (synced collection config, default OFF)

Add keys alongside the existing `speedrun:tagTopicMap` / `speedrun:exam_date`
(`ts/routes/dashboard/config.ts`, `getConfigJson`/`setConfigJson`):

- `speedrun:aiAssist` — master switch (default `false`),
- `speedrun:debriefEnabled`, `speedrun:coachEnabled`, `speedrun:tagSuggestEnabled` — per-feature
  (default `false`),
- optional `speedrun:aiBackend` — `"mock" | "claude-cli" | "openai-compatible"` (else env).

Every feature checks its flag **and** the master flag; with either off, the AI affordance is not
rendered and the deterministic behavior is unchanged. Extend `config.ts` with typed getters/setters
following the existing pattern.

### S4 — Global honesty guardrails (apply to all three)

1. AI output is **read-only** w.r.t. grading, scheduling, Readiness.
2. **Grounded or abstain** — never state a number/fact not in the supplied facts; when unsure, say so.
3. **Disclose**: label AI output as AI-generated and disclose any network egress of user data.
4. **Human-in-the-loop** before persisting attribution (C) — AI only pre-fills; the user Saves.
5. **Offline / errors** → deterministic fallback; no feature becomes a hard dependency.

_(No A/B/C feature generates study cards, so the `aig::ungraded` / [R8] "zero transfer credit until
validated" rules apply only to the deferred Phase 3 BYO onboarding.)_

---

## Feature A — Post-session error-pattern debrief

**Goal.** After a session (or on demand), turn the session's mistakes into a short pattern narrative:
topics/clusters missed, **confusable pairs that co-occurred**, the specific **misconceptions** behind
missed MCQs, and the single best next action. Narration only — it states counts, never grades.

**Data (no new RPC).** A desktop add-on reads `mw.col` directly, exactly like
`confusability.load_revlog_sqlite` (`select id, cid, ease from revlog` joined to `cards`/`notes` for
tags). Filter to the **session window** (recent graded reviews). Roll up: per-`cfa::topic::` and
per-`cluster::` lapse counts; **confusable pairs via `confusability.mine_discrimination_need`**;
misconception histogram from the missed MCQ notes (the `misconceptions` map in the item / distractor
metadata, keyed by the chosen wrong letter).

**AI role.** `assistant.grounded_complete` turns the deterministic pattern table into 3–5 sentences +
one concrete next step. It may only reference rows in the table; **abstain when < N mistakes** (e.g.
< 3) — show the raw table instead.

**Milestones.**

- **A1** Session extractor: reuse the confusability loaders; add a time-window filter + a missed-MCQ
  misconception rollup. Pure function, unit-tested on a synthetic revlog (reuse
  `confusability.synthetic_revlog`).
- **A2** Deterministic pattern report (`{topics_missed, confusable_pairs, misconceptions, best_next}`),
  reusing `mine_discrimination_need`.
- **A3** AI narration (S1) with abstention + deterministic fallback.
- **A4** UI: a session-end panel and/or a **"Debrief"** card on the dashboard.
- **A5** `speedrun:debriefEnabled` toggle + pytest (mock backend, deterministic).

**Acceptance.** With AI off → the deterministic table renders. With mock backend → a fixed narrative.
No revlog write, no scheduling change, nothing feeds Readiness.

---

## Feature B — Study Coach

**Goal.** A natural-language "what should I do today?" grounded in the **already-computed dashboard
model** (`ts/routes/dashboard/metrics.ts` → `DashboardModel`: per-subject Memory/Performance,
coverage, `weightedGap`, `bestNext`) plus `speedrun:exam_date` → `days_to_exam`.

**AI role.** Prioritize and explain (e.g. "34 days out, Derivatives at 12% coverage and the largest
weighted gap → spend today there"). **It must defer to the gauge**: when Readiness abstains, the coach
**must not invent a P(pass)** — it prioritizes by `weightedGap`/`bestNext` (shown even while
abstaining) and echoes the abstention reasons verbatim.

**Milestones.**

- **B1** Serialize the `DashboardModel` (+ exam date / days-to-exam) to a facts dict for the bridge.
- **B2** Wire S2 host bridge (`speedrun_assistant`) + backend selection.
- **B3** Coach prompt: grounded, abstention-preserving, prioritization-only; never emits a score the
  gauge is withholding.
- **B4** A **"Coach"** panel in `DashboardPage.svelte` (collapsed by default; disclosure label).
- **B5** `speedrun:coachEnabled` toggle + tests (mock backend → fixed plan; abstaining model → coach
  refuses to state P(pass)).

**Acceptance.** Coach text references only model numbers; with Readiness abstaining it never prints a
pass probability. AI off → panel hidden, dashboard unchanged.

---

## Feature C — Tag→topic mapping suggester

**Goal.** Add an **"AI suggest"** action to the existing _Map tags_ editor (`DASHBOARD.md` M5): for
each **unmapped** raw tag (already returned in `TopicMasteryResponse.unmapped_tags`), propose one of
the 10 canonical topics (`ts/routes/dashboard/topics.ts`), `"ignore"`, or **"unsure"** (abstain). The
user reviews, edits, and **Saves** — only then is `speedrun:tagTopicMap` written. Storage,
determinism, and abstention semantics are unchanged; **AI never persists**.

> This is the only feature where AI touches something adjacent to a gauge input (topic attribution),
> so the human-confirm gate is mandatory and low-confidence suggestions must be left blank, not forced.

**AI role.** Classify `tag [+ up to K sample note fronts for that tag] → {topic|ignore|unsure,
confidence}`. Pre-fill the dropdowns; low confidence → leave blank.

**Milestones.**

- **C1** Suggestion function (S1) returning per-tag `{topic, confidence}` with an "unsure" abstain.
- **C2** Reuse the S2 bridge.
- **C3** Editor UI: a **"Suggest"** button that pre-fills dropdowns, shows confidence, and keeps
  one-click override; nothing auto-saves.
- **C4** `speedrun:tagSuggestEnabled` toggle + tests. The manual editor must work identically with AI
  off.

**Acceptance.** No write until the user clicks Save; unmapped tags stay surfaced; the mastery RPC and
its determinism are untouched.

---

## Feature D — BYO / imported-deck onboarding — **DEFERRED TO PHASE 3**

Moved out of this plan on 2026-07-03. BYO / imported-deck onboarding — the previewed, human-confirmed,
undoable **"Prepare this deck for Speedrun"** action that proposes `cfa::topic::` / `cluster::` /
`rung::` / `interactivity::` tags, computed confusability edges, and generated missing rungs — now
lives in **`PHASE3_PLAN_V2.md` M5 (Generalization / BYO decks)**, alongside the AI edge-sourcing and
delayed-held-out validation it depends on. It is **not implemented by this plan**.

---

## Deliverables

1. `tools/speedrun/assistant/` — the backend adapter (S1) reusing `aig/models.py`, with
   grounded-or-abstain semantics and a mock default.
2. A desktop **host bridge** (S2) for the webview features, engine-free.
3. New **`speedrun:` toggles** (S3) wired through `config.ts`, all default-OFF.
4. **A — debrief**: session extractor + deterministic pattern report (reusing the confusion miner) +
   AI narration + UI + tests.
5. **B — coach**: dashboard-model serializer + grounded coach + dashboard panel + tests.
6. **C — tag suggester**: per-tag suggestion + "Suggest" editor action (human-confirmed) + tests.
7. Docs: short sections in `DASHBOARD.md` (B/C); BYO onboarding is specified in `PHASE3_PLAN_V2.md`
   M5 (deferred).
8. `desktop/RUNTIME_AI_NOTES.md` — a running lessons file kept by the implementing agent.

## Touch points (verified against source)

| Concern                             | File / symbol                                                                                                                                                              |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AI backends (reuse)                 | `tools/speedrun/aig/models.py` (`MockBackend`, `ClaudeCliBackend`, `OpenAICompatBackend`, `parse_json_reply`)                                                              |
| Confusion mining (reuse, Feature A) | `tools/speedrun/aig/confusability.py` (`load_revlog_sqlite`, `mine_discrimination_need`, `compute`, `synthetic_revlog`)                                                    |
| Dashboard model (B)                 | `ts/routes/dashboard/metrics.ts` (`buildDashboardModel`, `DashboardModel`)                                                                                                 |
| Mastery data (C source)             | `StatsService.TopicMastery` — `proto/anki/stats.proto`, `rslib/src/stats/mastery.rs` (`unmapped_tags`, `graded_reviews`, `ungraded_aig_cards`)                             |
| Config toggles (S3)                 | `ts/routes/dashboard/config.ts` (`getConfigJson`/`setConfigJson`, `speedrun:*`); `rslib/src/backend/config.rs` (existing service)                                          |
| Dashboard page / launch             | `ts/routes/dashboard/{DashboardPage,+page}.svelte`; `qt/aqt/toolbar.py`; `AnkiWebViewKind.CFA_DASHBOARD`; Android `android/.../pages/{Dashboard.kt,PostRequestHandler.kt}` |

## Build / verify

- Python (tooling + add-on logic): `pytest` under `tools/speedrun/tests/` (Feature A reuses the
  confusability self-test: `python3 tools/speedrun/aig/confusability.py --self-test`). Type-check via
  `./tools/dmypy`.
- TypeScript/Svelte (B/C UI + config): `./ninja check:svelte`; vitest for `config.ts`/`metrics.ts`
  helpers (pattern: `ts/routes/dashboard/metrics.test.ts`).
- Rust: **no engine change is expected**; if any slips in (proto/config), run `./check` (a proto
  change needs a full build) and keep it exam-agnostic.
- Final: `./check` green before marking complete (per `desktop/CLAUDE.md`).

## Risks & decisions

- **Scope creep into the review loop** — the one hard line; if a feature seems to need it, stop.
- **Platform scope** — **desktop only; Android is out of scope.** The shared dashboard page must
  still degrade gracefully on Android (B/C affordances hidden when the S2 bridge is absent); no
  Android source is modified.
- **Latency / cost / privacy** — runtime AI = network; opt-in, disclosed, cached where possible,
  graceful offline fallback.
- **Hallucination without a runtime critic** — enforce grounded-or-abstain (S1); the debrief and coach
  may only restate numbers already supplied to them.
- **C touches attribution** — mandatory human-confirm; low confidence abstains; deterministic map
  semantics preserved.
