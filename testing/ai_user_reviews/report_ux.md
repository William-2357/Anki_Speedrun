# User-test report — persona "Uma" (UX / accessibility designer, CFA Level I candidate)

Tester: Uma, 39 — product designer who runs accessibility reviews professionally and is studying for
CFA Level I herself. Focus: information hierarchy, wording, units, spacing, empty/error states,
colour contrast, keyboard access, and survival under awkward data. WCAG 2.1 criteria cited by number
where applicable. App exercised end-to-end over CDP against the real desktop build (night mode,
`#night`), plus component-source review with file:line citations for every visual finding.

---

## 1. Persona & journey

Environment: isolated instance via `tools/speedrun/usertest/launch_instance.sh`
(base `usertest/bases/ux`, API port 40104, CDP port 9304, user `uma`), driven with
`usertest/cdp.js` (raw CDP; `eval` + `.click()` per PRIMER §1b2). All screenshots in
`usertest/artifacts/ux_*.png` (index at the bottom).

Exact path run:

1. **Zero state** (fresh profile, empty collection): deck list, top/bottom toolbars, Dashboard
   (all three gauges abstaining), native Stats, concept map at `/concept-graph/1` (no tags).
   Console captured on each page.
2. **Seeding**: app closed cleanly (SIGTERM), `cfa_level1_sample.apkg` (72 cards) imported into
   `bases/ux/User 1/collection.anki2` with pylib; `fsrs=true` set; new/day raised to 100; relaunch.
3. **Study**: 15 cards graded through the real reviewer over CDP
   (sequence good×2, again, good, hard, good×3, again, good×2, hard, good×3), screenshots of card
   Q/A and the grade buttons.
4. **Partial-data pass**: Dashboard (Memory 100%, Performance 80%, Readiness abstaining),
   concept map at `/concept-graph/0` with 26 tag nodes, both colour modes.
5. **Dashboard deep audit**: contrast ratios computed in-page from `getComputedStyle`
   (WCAG relative-luminance formula), ARIA/heading/focusable audit, focus-visibility test,
   CSSOM rule dump, `Emulation.setDeviceMetricsOverride` at 800×600 and 500×800.
6. **Reviewer UX**: typography probe on `#qa`, bottom-toolbar DOM dump (accessible names,
   `title` attrs), 500 px-wide toolbar overflow check.
7. **Concept map audit**: effective label sizes measured from viewBox scale, d3 wheel-zoom test via
   CDP `Input.dispatchMouseEvent`, keyboard-access audit, source review
   (`ConceptGraphPage.svelte`, `graph.ts`, `grouping.ts`).
8. **Deck options**: followed the gauges' own instruction "Enable FSRS in deck options" literally at
   `/deck-options/<did>`; screenshotted where it leads.
9. **Wording sweep** across all of the above + `ftl/core/deck-config.ftl`, `topics.ts`,
   `cfa_weights_2026.json`.
10. **Scoped vitest**: `routes/dashboard` (15 tests) and `routes/concept-graph` (13 tests).
11. Cleanup: instance killed (`pkill -f "usertest/bases/ux"`).

Not verified (stated per PRIMER rigor bar):

- The `SpeedrunConceptMap` **dialog** via the deck gear menu (native Qt menu, not reachable over
  CDP). Audited the same Svelte route by direct navigation instead, as my persona brief allows.
- OS-level keyboard Tab traversal & Qt shortcuts (CDP synthetic keys do not reach Qt's shortcut
  handler — "not testable over CDP", _not_ "broken"). DOM accessible names and CSS focus rules were
  audited instead.
- Node labels with tags longer than 28 chars (sample deck's longest is
  `financial_statement_analysis`); by code review `graph.ts:61` renders the last `::` segment with
  no truncation, so a 60-char segment would render at full width — unverified live.
- Non-US locale rendering of `toLocaleTimeString()`.

---

## 2. Formatting / UX observations

**Dashboard information hierarchy (the flagship screen).**

- The three-question framing ("Can you recall… / Would you answer… / What is the probability…") is
  genuinely good design — it names the constructs and keeps them separate (`DashboardPage.svelte:291-311`).
  But each card then repeats identical meta ("exam covered: 24% · updated 3:24:23 PM" ×3,
  `GaugeCard.svelte:64-72`), and the same numbers reappear in the meta strip and again in the
  Readiness bullets. Redundancy without reinforcement: same fact, four labels.
- There is **no gauge graphic at all** — the "gauges" are big numbers. While abstaining nothing
  renders a fake needle position, which honours the honesty contract (the abstain state is a dashed
  border + "no score", `GaugeCard.svelte:20,86-88,129-133`). Correct behaviour; my only critique is
  hierarchy, not honesty.
- "no score" typography: 1.6 rem grey-on-grey (3.27:1) — as _the most important state of the most
  important gauge_, it under-signals. The missing-items list below it is full-contrast white
  (11.78:1, `GaugeCard.svelte:176-178`), so the page pops the _reasons_ but mutes the _verdict_ —
  inverted emphasis.
- "Pass band (MPS proxy) 68–75% (unpublished; carried as a band)" — as a CFA candidate I know MPS ≈
  minimum passing score; as a designer I know nobody else will. Jargon needs a gloss.
- Timestamp `updated 3:22:37 PM` (`toLocaleTimeString()`, `GaugeCard.svelte:70`,
  `DashboardPage.svelte:426`): seconds precision suggests a live feed the page doesn't have; no date
  means "updated 3:22 PM _which day_?" after leaving it open overnight. "Refresh" button exists and
  works (good affordance, `:275`), but nothing communicates staleness.
- Exam-date row: empty-state hint "No exam date set — the fade ladder is disabled." is a good
  pattern (states consequence). But "fade ladder" is unexplained on this screen, and the free-text
  error path ("Enter the date as YYYY-MM-DD.", `DashboardPage.svelte:256`) is dead UI on desktop —
  the `<input type=date>` refuses non-date text at the DOM level (verified: setting `not-a-date`
  yields `""`), so the message can effectively never appear. Untestable code path posing as UX.
- AI assistant block: genuinely well-handled default-off state. The `<details>` summary announces
  "(all default off)", the body explains read-only scope and data egress ("the facts shown to you
  are sent to that model"), per-feature toggles are disabled until the master switch is on
  (`DashboardPage.svelte:654-728`). This is the clearest disclosure copy in the app. Nit: summary
  text is 3.78:1 muted and the backend dropdown option "(from environment)" is developer-speak.
- Narrow widths behave well (verified): gauges stack via `repeat(auto-fit, minmax(16rem,1fr))`
  (`DashboardPage.svelte:924-928`); at 800×600 and 500×800 there is **no page-level horizontal
  scroll** (`scrollWidth == clientWidth == 788/488`); the subject table scrolls inside
  `.table-section { overflow-x: auto }` (`:1004-1006`) — screenshots `ux_16`–`ux_19`. The table's
  own scrollbar at 800 px hides the Performance column offscreen with no scroll cue beyond the OS
  scrollbar, but it does not break layout.

**Reviewer.**

- Card typography is clean: 20 px/30 px Arial, centered, 11.8:1 contrast (`#qa` probe). Grade
  buttons carry interval annotations above them; the annotations are tiny grey `stattxt` and the
  concatenated accessible names will read oddly in a screen reader ("Again less than one m").
- The bottom toolbar is a legacy `<table>`/`<center>` layout but does not overflow at 500 px
  (verified `sw=cw=500`, `ux_23`).
- Keyboard: number keys / Space are Qt-level shortcuts I cannot verify over CDP; the DOM buttons are
  real `<button>`s (focusable) but their focus ring is destroyed by `outline:none !important`.

**Concept map.**

- The map itself is honest (grey = no data, stated in the header) and the two-mode colouring is a
  good idea; the "scroll to zoom · drag to pan · hover a node for details" hint is welcome but
  hover-dependent details exclude keyboard/touch. Wheel-zoom verified working (d3 transform
  changed, `ux_24`).
- Default fit shows the whole graph so small it's wallpaper: 5 px labels (measured), and the
  10 uppercase topic headings at 8.3 px in 3.78:1 grey. First-open impression is "specks and lines".
- Legend swatches are 0.7 rem colour dots differing only in hue red/green — the exact pair
  affected by the most common colour-vision deficiency (WCAG 1.4.1); the FSRS-recall mode is a
  continuous red→green ramp (`graph.ts:91-96`) with no redundant channel at all.
- Empty state ("No tags found. Import a tagged deck (e.g. the CFA sample deck)…",
  `ConceptGraphPage.svelte:213-217`) is good: names the fix. Screenshot `ux_06`.

**Deck options (instruction accuracy).**

- The gauges say "Enable FSRS in deck options". Followed literally: `/deck-options/<did>` has an
  "FSRS" section whose first row is a switch labelled exactly "FSRS" (`ux_21`) — the instruction is
  **accurate** (and FSRS was already on in my seeded profile, shown by the blue toggle). The
  Speedrun-specific sections read "Contrast Scheduling (Speedrun)", "Fade Ladder (Speedrun)",
  "Readiness Allocation (Speedrun)" (`ftl/core/deck-config.ftl:634,660,726`) — clear, though
  "(Speedrun)" as a suffix convention is branding no first-time user will parse.

**Stats (vanilla screen, zero state).** Fine: every empty graph says "NO DATA" and Retention shows
N/A rows (`ux_05`). No Speedrun branding here at all — the dashboard and native stats never
cross-reference each other, a missed navigation link both ways.

**Console hygiene.** Summary of everything captured: dashboard 4× 500s per load; concept map 3×;
deck options 2×; reviewer MathJax version warnings; zero Svelte dev warnings observed;
top/bottom toolbars and stats clean.

---

## 3. Learning experience vs. traditional Anki

**Genuinely better than vanilla Anki for a CFA candidate:**

- The three-gauge separation (recall now / exam-style transfer / pass probability) with an enforced
  abstain state is a _category_ improvement over Anki's stats page, which happily draws precise
  retention curves with no epistemic humility. "Not enough data — and this app does not guess" is
  the single best sentence in the product.
- Topic table sorted by weighted gap, with official weight ranges attached, answers the question
  vanilla Anki cannot: _where should the next hour go relative to the exam blueprint_ — undermined
  right now by the contradictory second recommendation.
- The AI block's default-off, read-only, disclosure-first design is how this should be done
  everywhere.

**Worse or missing:**

- Trust erodes exactly where the app is trying hardest: `-0%`, two "best next"s, four
  names for coverage, day-one "100% (100%–100%)", console full of 500s. For a product
  whose whole pitch is _calibrated honesty_, each of these reads as sloppiness in the honesty layer
  itself.
- Accessibility is below vanilla Anki's (already mediocre) bar on the new surfaces: the flagship
  dashboard's muted text fails AA broadly, the concept map is effectively mouse-only,
  and the reviewer's focus ring is suppressed (this one is inherited Anki sass, but Speedrun
  ships it).
- The measurement pipeline (probes, delayed outcomes, calibration) is invisible until you fail its
  gates, and the error copy speaks maintainer ("build_probe_deck.py"), not student.

**Would Uma switch?** For study: yes, tentatively — FSRS + the honest dashboard beats my Anki+
spreadsheet workflow, and nothing here fabricates numbers. For recommending it to my study group:
not until the contradiction and the contrast/keyboard basics are fixed; I'd be
embarrassed to demo a readiness product that gives two different answers to "what next?".

**Does the complexity pay off?** The three-gauge model and probe gating: yes. The concept map in its
current form: no — at default zoom it is decoration, and it duplicates what the topic table already
says more legibly.

**Screen most in need of a redesign:** the **Readiness dashboard**. It has the best bones and the
most rot: inverted emphasis on the abstain verdict, 4× repeated meta, four coverage labels, two
conflicting recommendations, jargon bullets, and sub-AA muted text throughout. One information-design
pass (single source of truth per fact, one recommendation, AA-compliant muted colour, jargon gloss)
would transform the product's core promise. (Runner-up: concept map, but there the fix is
accessibility plumbing, not redesign.)

---

## 4. Top fixes (ranked)

1. **Reconcile the two "best next" recommendations** — one algorithm, one label, or an explicit
   "why these differ" note. This is the highest-trust-damage-per-pixel defect in the app
   (`metrics.ts:266-267` vs `readiness/mod.rs:401-431`).
2. **Accessibility floor**: raise `--fg-subtle` night-mode value to ≥4.5:1 against
   `#2c2c2c/#363636`; delete `outline: none !important` (`buttons.scss:26`) in favour of the
   `:focus-visible` pattern the SvelteKit pages already use; set `document.title`.
3. **Stop the per-load 500s**: return empty-value 200s for unset `speedrun:*` config keys (or
   probe with a list call), and never surface "use the Check Database action" for a key that was
   simply never written.
4. **Concept map legibility + input**: minimum on-screen label size (or hide labels below a
   zoom threshold and show on hover _and_ focus), keyboard zoom controls, aria-labels on
   `role="img"` nodes, and a second visual channel in the legend (shape or pattern, not hue alone).
5. **Coverage naming + degenerate numbers**: one coverage term used everywhere, fix
   `-0%`, and treat day-one retrievability specially ("just studied — check back tomorrow" instead
   of "100% (100%–100%)").

---

## Screenshot index

All files under `desktop/tools/speedrun/usertest/artifacts/`.

| File                                 | What it shows                                                                                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `ux_01_zero_decklist_main.png`       | Zero state: deck list with only "Default", night mode                                                                                |
| `ux_02_zero_top_toolbar.png`         | Top toolbar: Decks · Add · Browse · Stats · Dashboard · Sync                                                                         |
| `ux_03_zero_bottom_toolbar.png`      | Bottom toolbar zero state (Get Shared / Create Deck / Import File)                                                                   |
| `ux_04_zero_dashboard_full.png`      | Dashboard, empty profile: all three gauges abstaining; "-0%" bullet; FSRS line ×3                                                    |
| `ux_05_zero_stats.png`               | Native stats zero state ("NO DATA" panels)                                                                                           |
| `ux_06_zero_conceptmap.png`          | Concept map empty state + legend + colour-mode buttons                                                                               |
| `ux_07_decklist_seeded.png`          | Deck list after import: "CFA Level 1 Speedrun", 72 new                                                                               |
| `ux_08_deck_overview.png`            | Deck overview (New 72 / Learning 0 / To Review 0, Study Now)                                                                         |
| `ux_09_reviewer_answer.png`          | Card back (ethics card), typography                                                                                                  |
| `ux_10_reviewer_grade_buttons.png`   | Grade buttons with interval annotations `Again<1m … Easy 6d`                                                                         |
| `ux_11_dashboard_partial_top.png`    | Dashboard after 15 reviews: Memory 100%, Performance 80% (UNCALIBRATED badge), Readiness abstaining; "Best next topic: Fixed Income" |
| `ux_12_dashboard_partial_mid.png`    | Meta strip: coverage 24% vs deck 100%; "Best next thing to study: Financial Statement Analysis" (contradiction)                      |
| `ux_13_dashboard_partial_bottom.png` | Subject table: dimmed unstudied rows, 100% (100%–100%) bars, footnote                                                                |
| `ux_14_conceptmap_tags.png`          | Concept map, 26 nodes, difficulty mode — 5 px labels at default fit                                                                  |
| `ux_15_conceptmap_fsrs_mode.png`     | Concept map, FSRS-recall mode + its legend                                                                                           |
| `ux_16_dashboard_800x600.png`        | Dashboard at 800×600 (2-column gauges, no clipping)                                                                                  |
| `ux_17_dashboard_800_table.png`      | Subject table at 800 px: internal horizontal scrollbar                                                                               |
| `ux_18_dashboard_500x800_top.png`    | Dashboard at 500×800 (single-column stack, no clipping)                                                                              |
| `ux_19_dashboard_500_table.png`      | Subject table at 500 px, scrolled                                                                                                    |
| `ux_20_deck_options_top.png`         | Deck options top (Daily Limits; preset "Default (used by 2 decks)")                                                                  |
| `ux_21_deck_options_fsrs.png`        | FSRS section — the switch the gauges' instruction points to (toggle ON)                                                              |
| `ux_22_reviewer_question.png`        | Card front (quant Bayes card)                                                                                                        |
| `ux_23_bottombar_500w_graded.png`    | Grade buttons at 500 px width (no overflow)                                                                                          |
| `ux_24_conceptmap_zoomed.png`        | Concept map after wheel-zoom over CDP (d3 zoom works)                                                                                |
| `ux_25_ai_settings_open.png`         | AI assistant settings expanded: master switch + dependent toggles + backend select                                                   |

---

_Method note: contrast ratios computed from live `getComputedStyle` colours with the WCAG 2.x
relative-luminance formula; they are estimates on solid backgrounds (no overlays were present on the
measured elements). Vitest: `routes/dashboard` 15/15 and `routes/concept-graph` 13/13 pass when run
from `desktop/ts/`; the PRIMER's from-`desktop/` invocation fails to resolve `@generated`._
