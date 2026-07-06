# User Test Report — "Nadia" (novice persona)

**Persona:** Nadia, 27, CFA Level I candidate, exam in ~4 months. Never used Anki
or any spaced-repetition app. Mildly tech-savvy (Notion/Excel), not a programmer.
Judges an app in the first 15 minutes.

**Environment:** real desktop app (Anki 26.05b1 fork), driven over CDP
(`usertest/cdp.js`) against a live headed instance
(`launch_instance.sh bases/novice 40101 9301 novia`). All screenshots in
`artifacts/novice_*.png`. Tested 2026-07-05.

---

## 1. Persona & journey (what I actually ran)

1. **First launch, empty profile** — launched via `launch_instance.sh`, waited for
   CDP on :9301, captured deck list, toolbars, and the Dashboard zero state.
   Screenshots: `novice_01_zerostate_main.png`, `novice_02_dashboard_empty.png`,
   `novice_03_bottom_buttons.png`.
2. **Looked for CFA content in-app** (found none), then closed the
   app (`kill -TERM`) and pre-seeded `tools/speedrun/cfa_level1_sample.apkg` into
   `bases/novice/User 1/collection.anki2` with pylib
   (`import_anki_package`, 72 notes, deck "CFA Level 1 Speedrun"), leaving every
   setting at default (20 new/day, FSRS **off**) — the exact state a real user
   is in after File → Import. Relaunched.
3. **Studied through the real UI**: opened the deck, clicked _Study Now_, and
   graded cards via the actual reviewer buttons (Show Answer → Again/Hard/Good/Easy).
   Introduced all 20 new cards allowed by the daily limit and cleared the learning
   queue in the same sitting: **46 graded reviews total** (3 Again, 3 Hard,
   40 Good — verified against `revlog`: `[[1,3],[2,3],[3,40]]`).
   Screenshots: `novice_05_deck_overview.png`, `novice_06_first_answer.png`,
   `novice_07_grade_buttons.png`, `novice_09_after_session.png`, `novice_20_congrats.png`.
4. **Dashboard before/after FSRS**: read every abstention message, then followed
   "Enable FSRS in deck options" literally: deck → _Options_ button → scrolled to
   the FSRS section → clicked the toggle (`novice_13/14`) → Save → dashboard
   Refresh. Memory/Performance then showed numbers (`novice_15`, `novice_23`).
   Also set an exam date (2026-11-05) via the dashboard's EXAM DATE field and
   verified it persisted to `speedrun:exam_date` (`novice_27_examdate_grid.png`).
5. **Stats page** (toolbar → Stats, `novice_16_stats.png`) and **concept map**.
   The map's only entry point is the deck gear-menu → "Concept map"
   (`qt/aqt/deckbrowser.py:321-323`), a native menu CDP cannot click, so I loaded
   the same SvelteKit route the dialog hosts (`concept-graph/<deckId>`,
   `qt/aqt/speedrun_dashboard.py:109`) in a webview: `novice_17_conceptmap.png`.
6. **Add & Browse**: opened Add from the toolbar, filled Front/Back in the real
   editor (shadow-DOM contenteditables) (`novice_18/19`); the final **Add commit
   is a native Qt button** — Cmd/Ctrl+Enter dispatched over CDP never reaches
   Qt's shortcut handler (known harness limitation), so note creation is
   **not verified** (note count stayed 72). Browse opens, but its card table is
   native Qt — only its embedded editor pane is visible over CDP (not judgeable).
7. **Persistence**: killed the app, verified on disk (46 revlog rows, FSRS on),
   relaunched, and confirmed the UI shows "Studied 46 cards … today" with the
   deck at 0/0/0 (daily limit consumed) — `novice_21_decklist_relaunch.png`,
   `novice_22_overview_relaunch.png`.
8. **Console sweep on every page visited** (`page.enableConsole()`):
   - deck list / toolbars / stats / deck options / congrats: **clean**
   - reviewer: 2 benign MathJax warnings (`[tex]/noerrors`, `[tex]/mathtools`)
   - **dashboard: 2× HTTP 500 on every load; concept map: several more**

Shutdown: `pkill -f "usertest/bases/novice"` — confirmed no processes left.

---

## 2. Formatting / UX observations (novice lens)

- **Zero state is silent.** "Studied 0 cards in 0 seconds today (0s/card)" is the
  only text on screen besides an empty Default deck. `(0s/card)` on zero cards is
  0/0 arithmetic presented to the user. No hint that this is a CFA app at all —
  the word "CFA" appears nowhere on the first screen (verified
  `innerText.includes('CFA') === false`).
- **The Dashboard's honesty copy is genuinely good prose** ("Three separate
  questions, three separate answers — never one blended number", "A system that
  knows when it does not know beats a confident guess"). As a skeptical user I
  _liked_ being told "no score — and this app does not guess"; it reads as
  respect, not evasion. But the abstention bullets immediately spend that trust
  on jargon: _delayed held-out probe outcomes, probability band half-width 0.48,
  unpublished MPS, calibration pool, probe harness_. I understood maybe a third
  of it, and I'm the target customer.
- **Everything gated behind FSRS is invisible until you flip an expert toggle.**
  Deck options is 9 scrollscreens of settings; the three "(Speedrun)" sections
  (Contrast Scheduling, Fade Ladder, Readiness Allocation) appear _above_ FSRS
  with no explanation of what "Speedrun" means, all defaulted off. A novice has
  no way to know which switches matter. The FSRS "?" help icon exists; the
  section itself gives no first-timer summary.
- **Grade buttons** (`Again <1m · Hard <6m · Good <10m · Easy 3d`) are standard
  Anki but unexplained; Nadia guesses "Good" and moves on — acceptable, and the
  interval preview is genuinely helpful once you notice it.
- **Card quality (20 unique cards studied, all cited in study log): good.**
  Fronts are real questions, backs answer exactly that question, one fact per
  card, no typos or formatting glitches seen. Examples: "Standard III of the CFA
  Code of Standards covers what?" → "Duties to Clients (loyalty/prudence/care,
  fair dealing, suitability, performance presentation, confidentiality)."; "Money-weighted
  return is equivalent to what familiar quantity?" → "The internal rate of return
  (IRR) of the portfolio's cash flows." Minor gripes: a few telegraphic fronts
  ("Holding period return formula?") and one awkward phrasing ("What are the six
  components of the CFA Institute Code of Ethics about, in one line?"). Nothing
  absurdly long/short; nothing unanswerable.
- **Concept map renders and is honest** (grey = "no data yet"; green nodes only
  where I answered well — matched my session). But node labels are tiny, the
  layout is spread thin at default zoom (`novice_17_conceptmap.png`), and a
  novice reaching it at all is unlikely (gear menu only). The blurb's tag syntax
  (`cfa::topic::*`) is dev-speak.
- **Stats page** is stock Anki and worked; "Card Stability" / retrievability
  sections quietly assume FSRS knowledge, consistent with upstream.
- **Persistence UX is correct and reassuring**: after relaunch the deck list
  showed "Studied 46 cards … today" and the deck at 0/0/0 with the congrats
  screen explaining the daily limit and pointing to custom study — that message
  is one of the few beginner-friendly texts in the app.
- Night mode was on by default in this environment; contrast was fine everywhere.

---

## 3. Learning experience vs. traditional Anki (+ shared deck)

**What's genuinely better than vanilla Anki + a downloaded CFA deck:**

- The bundled deck is well-tagged and topic-mapped out of the box, so the topic
  table and coverage-by-exam-weight actually mean something — vanilla Anki has
  nothing like "you've covered 32% of exam weight, FSA is your biggest gap".
- The abstaining Readiness gauge is a real differentiator vs. the fake "% ready"
  widgets in commercial QBanks; the give-up rule is enforced by the backend
  (I could not make it print a number it hadn't earned, including on an empty
  profile and in every state I reached).
- Concept map and per-topic Memory/Performance columns give a study-planning
  view Anki simply lacks.

**What's worse or missing for a novice:**

- Getting started. Vanilla Anki at least funnels you to "Get Shared"; this fork
  claims to be a CFA app but ships its CFA deck as a repo file with no in-app
  path to it. My first 15 minutes as Nadia would have ended at the
  empty Default deck or at a dashboard telling me to enable something I've never
  heard of.
- The measurement layer only works after expert setup (FSRS toggle buried in
  deck options) and months of discipline (300 reviews, 70% coverage,
  50 delayed probes, plus a probe deck that isn't even importable from the UI).
  Until then the flagship dashboard is a wall of "no".
- When it finally does show a number, the first number a novice sees is
  "Memory 100%" after one cram session — the exact overconfidence the
  app exists to prevent, created by presentation rather than math.

**Would Nadia switch (from Kaplan/Schweser QBank)?** Not on this build. QBank
shows imperfect-but-instant per-topic feedback; this app deliberately withholds
judgment for months and requires her to sideload content and flip scheduler
internals first. The honesty philosophy is the app's best asset and I believe
some grinders would love it — but for a first-time spaced-repetition user the
CFA packaging is currently hidden behind expert knobs, so she gets vanilla
Anki's learning curve without vanilla Anki's ecosystem. Fix the first-run path
and the FSRS one-click, and the calculus changes: the study loop itself was
fast, pleasant, and the cards are exam-relevant.

---

## 4. Top fixes (ranked)

1. **First-run CFA onboarding:** on empty profile, offer "Start CFA
   Level I" that imports the bundled sample deck (and probe bank) in one click,
   with a 3-line explanation of the loop (study → dashboard → trust the gates).
2. **Make the FSRS instruction executable:** "Enable FSRS" button or
   deep link on the dashboard's abstention bullet + one plain-English sentence
   ("the scheduler that estimates recall; needed for all gauges").
3. **Stop 500ing on missing config keys:** return a default instead of
   `NotFoundError`; never log "your database is inconsistent" for a key that
   simply hasn't been written yet.
4. **One "what next" recommendation** — reconcile BEST NEXT TOPIC vs
   BEST NEXT THING TO STUDY, and fix "-0%".
5. **Novice-safe Memory framing right after study:** e.g. cap the
   headline or add "fresh in mind — check back tomorrow" when all evidence is
   same-day, so the first number the app ever shows isn't a misleading 100%.
