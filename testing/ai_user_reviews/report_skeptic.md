# User-Test Report — "Sana" (skeptic persona)

**Persona:** Sana, 31, quant risk analyst prepping CFA Level I. Burned before by
apps that show fake "87% ready!" scores. Mission: make Anki Speedrun lie about a
number. Every number on screen was interrogated: _where did this come from, and
is it justified?_

**Date:** 2026-07-05 · **Instance:** isolated base
`usertest/bases/skeptic`, API port 40103, CDP port 9303 · all screenshots in
`usertest/artifacts/skeptic_*.png`.

---

## 1. Persona & journey

End-to-end path actually run (real desktop app over CDP + engine interrogation
via pylib; no repo source modified; no eval harness run):

1. **Seeded** the persona base with the app closed:
   `cfa_level1_sample.apkg` (72 cards) + `cfa_probes.apkg` (70 probe cards),
   `col.set_config("fsrs", True)` — via `out/pyenv/bin/python` + `out/pylib`.
2. **Launched** via `launch_instance.sh bases/skeptic 40103 9303 sana`;
   confirmed CDP up (`/json/version`).
3. **Studied 30 cards** in the real reviewer over CDP (open deck → Study Now →
   Show Answer → grade; mix of Again/Hard/Good/Easy = 4 Again, 26 pass)
   [skeptic_03_reviewer.png].
4. **Dashboard honesty audit** (toolbar → Dashboard): full-text dump +
   screenshots of all three gauges, Behind-the-Readiness panel, topic table
   [skeptic_04_dashboard_initial.png].
5. **Refresh test:** timestamp `updated 3:16:43 PM` → `3:18:12 PM` after
   clicking Refresh. ✔
6. **AI assistant:** verified default-OFF, enabled master switch + debrief +
   coach through UI clicks, backend = mock; exercised "Debrief my last session"
   and "What should I do today?" [skeptic_05–08].
7. **`?readinessTest=1` dev mode:** navigated dashboard webview to that URL,
   screenshotted, navigated back [skeptic_09_readinessTest.png].
8. **Probe mechanics:** studied 3 cards of the CFA Probes deck in the reviewer
   [skeptic_10/11], re-checked dashboard counters after [skeptic_12].
9. **Engine truth-check:** closed app / copied `collection.anki2` to mktemp;
   ran `col.get_readiness()`, `col.topic_mastery()`, `col.concept_graph()` and
   raw SQL on the copy; diffed against UI text (§2 below).
10. **Adversarial grind** (app closed, own base + scratch copies only):
    - 340 same-day Easy reviews on the live base → reopened app → dashboard.
    - "Blitz" and "liar" scenarios on scratch copies (`/tmp/skeptic_*/…`).
    - Max-gaming scenario on a fresh throwaway collection (§3 below).
11. **Concept-graph page** visited; console captured on every page.
12. Killed instance (`pkill -f "usertest/bases/skeptic"`).

---

## 2. Engine truth-check (UI vs RPC, same collection state)

`get_readiness()` on a copy of the live collection (30 UI reviews, 3 probe
answers not yet made), next to the UI text:

| RPC field (engine)              | Value                                                                                                                | UI text                                                     | Match                                                                                                                                           |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `kind`                          | `0` (ABSTAIN)                                                                                                        | "no score / Not enough data — and this app does not guess." | ✔                                                                                                                                               |
| `p_pass_low/high/center`        | `0.0 / 0.0 / 0.0`                                                                                                    | no number anywhere on the card                              | ✔ (zeroed-when-abstaining holds)                                                                                                                |
| `missing[0]`                    | "Only 30 graded study reviews; need at least 300."                                                                   | identical                                                   | ✔ verbatim                                                                                                                                      |
| `missing[1]`                    | "Topic coverage is 44%; need at least 70%. Not studied yet: Corporate Issuers, Equity Investments, Fixed Income, …." | identical                                                   | ✔ verbatim                                                                                                                                      |
| `missing[2]`                    | "Only 0 delayed held-out probe outcomes; need at least 50."                                                          | identical                                                   | ✔ verbatim                                                                                                                                      |
| `missing[3]`                    | "The probability band is too wide to be useful (half-width 0.48 > 0.2). …"                                           | identical                                                   | ✔ verbatim                                                                                                                                      |
| `evidence.graded_reviews`       | 30                                                                                                                   | "STUDY EVIDENCE 30 graded reviews"                          | ✔                                                                                                                                               |
| `evidence.coverage`             | 0.439024…                                                                                                            | "exam covered: 44%"                                         | ✔ (rounded; = 45/102.5 midpoint weight — the "of exam weight" phrasing hides the /102.5 normalisation, mids don't sum to 100)                   |
| `evidence.probe_unanswered`     | 50                                                                                                                   | "50 unanswered"                                             | ✔ (50 = performance pool; the other 20 of 70 probes are calibration pool — disclosed only in proto comments, the UI never explains why 70 ≠ 50) |
| `evidence.topics_studied/total` | 4/10                                                                                                                 | "4/10 topics studied"                                       | ✔                                                                                                                                               |
| `best_next_topic`               | "Fixed Income"                                                                                                       | "BEST NEXT TOPIC Fixed Income"                              | ✔ (but disagrees with the _other_ UI field)                                                                                             |
| `mps_low/high`                  | 0.68/0.75                                                                                                            | "PASS BAND (MPS PROXY) 68–75%"                              | ✔                                                                                                                                               |

`topic_mastery()`: per-topic `studied_cards` (8/6/7/9 over quant/FSA/econ/
ethics), `avg_retrievability` ≈ 0.9991 matched the table's "100%" cells and
8/8·6/6·7/7·9/9 high-recall counts. `concept_graph(deck_id=0)`: 78 nodes /
413 edges; page rendered clustered by topic with an honest grey
"no data yet" legend [skeptic_15].

**Verdict: the UI is a faithful projection of the RPC.** I found no number on
the dashboard that the engine did not produce, and abstention text is passed
through verbatim.

---

## 3. Trying to force a premature score

### a) Grind on my real base (the "honest-ish cheater")

340 same-day Easy reviews (engine loop, app closed), all 72 cards studied,
coverage 100%. Reopened the app:

- Readiness **still abstained**. Gates that held: **delayed-probe count**
  ("Only 0 delayed held-out probe outcomes; need at least 50. 3 more were
  answered too soon after study and are excluded (≥7-day rule)") and **band
  width** ("half-width 0.48 > 0.2"). The reviews gate (340 ≥ 300) and
  coverage gate (100% ≥ 70%) correctly disappeared from the missing list —
  the list is dynamic and truthful. [skeptic_14_dashboard_postgrind.png]
- The 3 probes I'd answered in the UI were correctly excluded as "too recent"
  with the reason stated. ✔

### b) Probe blitz on a scratch copy

Answering all 50 performance probes cold, same day, on top of the 30-review
state: 36 counted as delayed (never-studied clusters), 14 excluded as too
recent. Still abstained (36 < 50, reviews 30 < 300). Gates held. ✔

### c) Max-gaming a fresh collection (the app's worst day)

Fresh throwaway collection, all same-day: studied ONLY 10 cards (one per
topic, each chosen to share no `cluster::` tag with any probe), ground them to
310 Easy reviews, then answered all 50 performance probes "Good" without ever
studying their material:

```
kind: 1 (VALUE)   call: 'pass'   confidence: 0.85 (capped)
p_pass low/center/high: 0.90 / 1.00 / 0.98
graded_reviews: 310  coverage: 1.0  delayed: 50 (never_studied: 50)  correct: 50
missing: (none)
```

**The engine emitted a pass band on day one.** Every input was user-falsified
(Easy-grinding is a lie about recall; "Good" on an unseen MCQ is a lie about
correctness), so this is garbage-in-garbage-out rather than fabrication — and
the reasons[] honestly describe the method. But it demonstrates that the
README's "50 answered held-out probes **taken at least 7 days after the
material was last studied**" is not literally enforced: never-studied probes
waive the delay entirely (documented in `MODEL_DESCRIPTIONS.md:75`, absent
from the README summary). A determined self-deceiver can have his fake 90–98%
in ~20 minutes. Honest users are safe: honest grades on cold probes (~33%
guess rate on 3-choice MCQs) would produce a _low_ band and likely a "fail"
call — the instrument fails toward pessimism, not optimism, under honest use.

### d) Dev mode

`?readinessTest=1` is loud and unambiguous: red banner **"TEST MODE - give-up
gates relaxed; nothing on this page is a real prediction"**, red badge
**"TEST DATA — NOT A REAL PREDICTION"** on the Readiness card, a TEST-MODE
bullet inside the reasons, and the still-failing gates listed alongside.
36% with range 2%–98% — absurd on its face, as intended.
[skeptic_09_readinessTest.png] Not mistakable for real output. ✔

---

## 4. AI assistant honesty

- **Default state:** master switch and all three feature toggles unchecked;
  sub-toggles disabled until master is on. Verified in the DOM before
  touching anything: `[{checked:false},{checked:false,disabled:true},…]`.
  [skeptic_05_ai_settings_off.png] ✔
- Enabled everything through real UI clicks; backend set to "mock" via the
  UI select (no env var, no config file edit).
- **Debrief** (verbatim): _"This session's misses concentrated in economics. /
  Next step: Review economics: 1 lapses this session."_ + a session table
  (30 reviews, 4 misses — matches my actual session exactly) + footer
  _"AI-generated by the offline mock backend (no data leaves this machine)."_
  No score, no %. Grounded in real revlog counts. (But note:
  "concentrated" is false for a uniform 1/1/1/1 spread.)
- **Coach** (verbatim): _"Readiness is abstaining (Only 30 graded study
  reviews; … ) - no score is available, so prioritise by weighted gap: start
  with Equity Investments."_ — it echoed the abstention reasons and refused a
  number. [skeptic_08_coach_open.png]
- **Bait result: no pass-probability, no score, ever appeared in AI output
  while Readiness abstained.** ✔ (I could only use the buttons provided —
  there is no free-text prompt surface to attack, which is itself a good
  honesty containment decision.)

---

## 5. Marketing claims cross-check

| Claim (source)                                                                                                                          | Verdict                                                                                                                                                                                                                                 | Evidence              |
| --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| "Readiness abstains until … ≥300 graded reviews, 70% coverage, 50 delayed probe outcomes, band ≤0.2" (README:32, MODEL_DESCRIPTIONS:98) | **VERIFIED** — each gate individually observed holding (§3a/b); missing[] names each unmet gate with exact numbers                                                                                                                      | engine transcripts §3 |
| "an abstaining response carries zeroed numbers, so no display bug can leak an unearned probability" (README:48)                         | **VERIFIED** — `p_pass_low/high/center = 0.0/0.0/0.0`, `call=''` in every abstaining response                                                                                                                                           | §2 table              |
| "never shows a fabricated pass %" (README:26/PRIMER §0)                                                                                 | **VERIFIED with caveat** — no fabricated number ever appeared; but user-falsified inputs can mint a real-looking "pass 90–98%" same-day (§3c). The app never invents; it can be fed lies faster than the README's 7-day wording implies | §3c                   |
| "Probe cards are excluded from Memory and coverage … their answers never count as study reviews" (README:59, 244-245)                   | **VERIFIED at engine level** (coverage/Memory unchanged after probe answers; readiness study-review count subtracts probe answers) — **VIOLATED in one UI surface**: the meta "Graded reviews" item includes probe answers     | §2, [skeptic_12/14]   |
| "runtime AI assistant … default-OFF … never states a pass probability while Readiness is abstaining" (README:96-106)                    | **VERIFIED** — default-off in DOM; debrief/coach produced zero scores; coach explicitly defers to abstention                                                                                                                            | §4                    |
| "Three separate questions, three separate answers — never one blended number" (dashboard header, MODEL_DESCRIPTIONS:1-4)                | **VERIFIED** — three gauges, no composite anywhere                                                                                                                                                                                      | [skeptic_04]          |
| AI assistant "desktop-only" (README)                                                                                                    | **UNTESTABLE here** — no Android build exercised                                                                                                                                                                                        | —                     |

---

## 6. Formatting / UX observations

- Dashboard is text-dense but scannable in dark mode; the three-card layout
  reads well at 1280×900. No overflow with all 10 topics.
- The abstaining Readiness card uses a dashed border + big grey "no score" —
  visually unambiguous, cannot be misread as a gauge needle at some value.
  (There is no needle/arc graphic at all; "gauge" is metaphorical.)
- Topic table: studied rows break the Memory cell onto a second line
  ("100% (100%–100%)") while unstudied rows show "no data" inline — slightly
  ragged. Placeholders mix "no data" and "—" in adjacent columns.
- "exam covered: 44%" appears identically on all three cards — good
  consistency — but the number is normalized to a 102.5-point midpoint scale,
  which "44% of exam weight" doesn't quite say.
- Probe MCQ cards are actually pleasant: kicker title, A/B/C buttons with
  hover states, self-grade instruction, rationale on the back. Better looking
  than the plain study cards.
- Exam-date save worked and survived a full page reload (input value
  `2026-11-18` re-rendered); the "fade ladder disabled" hint disappeared.
  No "days to exam" indicator appears anywhere after setting it, which feels
  like a missed payoff.
- Concept map: honest grey "no data yet" legend; topic clustering matches
  studied state; hover/zoom hints present. Loads its own 3×500 console errors
 .
- First-run log prints a known-benign `profiles.py _loadMeta` traceback —
  cosmetically alarming in an otherwise clean startup.

---

## 7. Learning experience vs. traditional Anki

**Genuinely better for an evidence-minded CFA candidate:**

- The **missing[] list is the single best feature**: "need 300 reviews, you
  have 30; need 70% coverage, you have 44%; not studied yet: Corporate
  Issuers…" is a concrete study plan, not a score. Vanilla Anki gives raw
  counts and leaves the inference to you.
- Coverage-weighted topic accounting (blueprint midpoints) answers a question
  Anki simply can't: _how much of the exam have I even touched?_
- The probe-bank discipline (held-out, delayed, excluded from the gauges it
  feeds) is real measurement methodology; nothing in stock Anki resembles it.
- Abstention with reasons beats both a fake score AND silence. As the skeptic
  persona, I failed to make the _shipped, honest-input_ path lie.

**Worse / risks:**

- The **Performance gauge undercuts the story**: on day one it announces
  "77%" (range 62–92%) from hard-coded per-topic transfer factors — a stated
  assumption. It is labelled "UNCALIBRATED ESTIMATE", but it is the biggest
  number on the page next to "no score", and it is exactly the kind of
  authoritative-looking figure this app exists to refuse. A skeptic reads the
  label; everyone else reads "77%".
- Memory "100% (100%–100%)" minutes after study is technically-true noise; it
  teaches users that the Memory gauge is trivially maxable.
- Readiness will be a dead gauge for the first weeks of real use (300 reviews
  - 50 probes ≥7 days delayed is weeks away for an honest student), and near
    the pass boundary it may _never_ emit a band ("irreducible uncertainty" —
    stated). That is honest but reduces it to the missing[] checklist for most
    of the prep cycle. The checklist is good enough that I don't call this
    useless — but a "here's the date your evidence could first suffice"
    projection would help.
- Probe answers silently inflate ordinary "Studied N cards today" stats
 , and the double graded-reviews number is precisely the
  kind of unexplained discrepancy this audience will notice and distrust.

**Would Sana switch?** Yes, cautiously — the honesty architecture survives
adversarial use at the engine level, and everything on screen traces to an
RPC field I could reproduce. The two-number inconsistency and the
console 500s are the trust-erosion items to fix before she recommends it
to her study group.

---

## 8. Top fixes (ranked)

1. **Silence the unset-config 500s** (`speedrun:exam_date`,
   `speedrun:tagTopicMap` → return default instead of `NotFoundError`): every
   page load currently logs console errors and a "database inconsistent /
   Check Database" traceback on healthy profiles — maximally trust-eroding
   for exactly this app's audience.
2. **Reconcile the two graded-review counts** on the dashboard (subtract
   probe answers from the meta item, or label both precisely). One page must
   not show 343 and 340 for "graded reviews".
3. **Tighten the never-studied probe waiver** (e.g. require the collection to
   be ≥7 days old before never-studied outcomes count, or cap their share of
   the 50): closes the same-day fake-pass path and makes the engine match the
   README's "7 days after last studied" promise.
4. **Unify the "best next" recommendation** (engine vs frontend disagree:
   Fixed Income vs Equity Investments side by side).
5. Cosmetics batch: "-0%" coverage string, degenerate "100%–100%" range
   (suppress when width = 0), "(2025-2026 curriculum) (2026)" double year,
   mock-debrief "concentrated"/"1 lapses" wording.

---

## Artifacts

`usertest/artifacts/skeptic_01_decklist.png` … `skeptic_15_concept_graph.png`
(17 screenshots; each cited inline above). Engine transcripts inline in §2–3.
Scratch collections were confined to `mktemp` dirs; the repo working tree was
not modified beyond this report + artifacts. Instance killed
(`pkill -f "usertest/bases/skeptic"` → no survivors).
