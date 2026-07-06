# Anki Speedrun — User Test Report: "Vera" (8-year Anki power user, CFA Level I candidate)

Test date: 2026-07-05 · Instance: `bases/veteran`, API 40102, CDP 9302 · Anki engine 26.05b1
Artifacts: `usertest/artifacts/veteran_01..31_*.png` · Launch log: `/tmp/usertest_veteran2.log`

---

## 1. Persona & journey

I am Vera: 8 years on vanilla Anki, FSRS user, keyboard-driven reviewer (1/2/3/4, space, `u`),
deck-options tinkerer, evaluating whether this CFA fork is worth switching to. Everything below
was run against the **real desktop app** (headed Qt, driven over QtWebEngine CDP with
`usertest/cdp.js`), not a mock.

Exact path exercised:

1. **Seed (app closed):** `bases/veteran/User 1/collection.anki2` was empty on inspection
   (`decks: ['Default']`, 0 cards), so I imported all three `.apkg`s directly with pylib
   (`col.import_anki_package`, FSRS enabled, new/day=100): 72 + 66 + 70 notes → decks
   "CFA Level 1 Speedrun" (191 cards) + "CFA Probes" (70).
2. **Launch:** `launch_instance.sh bases/veteran 40102 9302 vera`; CDP answered on 9302;
   deck list verified (veteran_01).
3. **Real study sessions through the reviewer UI** (Show Answer → grade buttons on the bottom
   toolbar): 45 cards (mix of Good/Again/Hard/Easy per a fixed plan), then 34, then 50, then 77
   until the daily-limit congrats screen — **207 graded study reviews + 1 probe review = 208 total**.
   Grade-button ETAs, latency, and card quality assessed along the way (veteran_03–08, 21, 24, 29).
4. **Keyboard test:** synthetic Space / "3" / "u" over CDP on both webviews; verified the
   on-screen shortcut affordances instead (see the UX section).
5. **Power surfaces:** Browse (editor webview, veteran_09), Stats (veteran_10–11), deck options
   (veteran_12–14, 22), undo attempt, Sync button present (not exercised — would hit AnkiWeb).
6. **Speedrun deck-options additions:** found all three sections (Contrast Scheduling, Fade
   Ladder, Readiness Allocation); flipped **contrast scheduling ON via the UI** and saved;
   later enabled **fade ladder** the same way (veteran_14, 22).
7. **Contrast verification at the engine level:** A/B/C queue-order experiment on a scratch
   collection (mktemp) with the same pylib/rslib build the app runs.
8. **Fade ladder:** set exam date 2026-10-03 via the dashboard's `EXAM DATE` input (persisted to
   `speedrun:exam_date`; verified re-read + the "fade ladder is disabled" notice cleared,
   veteran_19); observed rung gating across the subsequent 127 new-card introductions.
9. **Dashboard** (top-toolbar `speedrun_dashboard` link): three gauges + abstaining Readiness
   at 46, 131, and 208 reviews (veteran_16–18, 28, 31). **Concept map**: loaded the same
   `concept-graph/0` SvelteKit route the ConceptMap dialog embeds (86 nodes / 467 edges,
   veteran_20) — the deck-gear menu entry itself is a native Qt menu, unreachable over CDP.
10. **Probe deck:** studied 1 held-out probe card end-to-end (veteran_25–27) and watched the
    probe-status line update ("1 too recent (excluded)").
11. **Console + log sweep:** `enableConsole()`/`consoleMessages()` on every page visited; full
    grep of the launch log (4 Python tracebacks, 2 distinct JS errors).

Not exercised (honest list): Sync/AnkiWeb, add-on ecosystem compatibility, the AI assistant
features (verified default-OFF collapsed section only), the deck-gear native menu ("Concept map",
"Prepare for Speedrun" entries), real keyboard input (CDP limitation), Readiness Allocation
toggle, `?readinessTest=1` dev mode.

---

## 2. Findings on the headline features (verified behavior)

**Contrast scheduling — the claim survives testing, with a catch.** UI toggle saved cleanly
(veteran_13/14; sub-fields `cluster::` / `confusable::high` appear only when enabled). Because my
live queue was dominated by intraday-learning cards by then (contrast reorders only the merged
new/review queue), I verified the reorder at the engine level with the exact pylib/rslib build the
app runs, on identical content (both apkgs, new/day=500, `get_queued_cards(fetch_limit=200)`):

- **OFF:** duration-cluster cards alternate singly (positions 1,3,5,…; run lengths all 1).
- **ON + default marker:** `cluster::fi::duration` (the one marker-carrying cluster) forms runs
  of exactly 4 (positions 1–4, 9–12, 17–20, …, matching `CONTRAST_CHUNK` in `contrast.rs:53`),
  while `qm::tvm` and `ethics::standards` stay vanilla-spaced — the R18 gate working as documented.
- **ON + empty marker (legacy ungated):** all clusters form runs of 4.

So the mechanism is real and surgical — but on the shipped sample content the default
gate makes it invisible. Cards are only reordered, never added/dropped: counts matched throughout.

**Fade ladder — behaved as documented in a live session.** With the ladder OFF (sessions 1–2),
day-1 new cards included `rung::solve` MCQs and `rung::faded` cloze steps freely. After enabling
the ladder + exam date (2026-10-03): across 127 subsequent new-card introductions I saw **zero new
solve MCQs**; `qm::tvm` and `fsa::inventory` clusters served only their `rung::worked` cards
(faded/solve withheld). The `fi::duration` cluster — which already had graded faded+solve history
from before the toggle — was _not_ re-locked to worked; it served exactly one new faded card
("Fill in the duration relations…"), which matches `fade.rs:413–422` (a rung with graded history
is never re-locked below) plus the hysteresis fall-back (`fade.rs:437–442`: young cards' predicted
R at a 90-day horizon < 0.8 → fall back from solve to faded) and R15's progressive one-new-faded-
sibling introduction. Learning cards were never gated (per design). `rung::compare` cards flowed
freely — `Rung::parse` only knows worked/faded/solve (`fade.rs:86–94`), so compare cards are
outside the ladder; worth documenting. Counts stayed consistent; the congrats screen was the
stock daily-limit one (veteran_29).

**Readiness honesty contract — held everywhere I poked it.** At 46, 130/131, and 207/208 graded
reviews the Readiness gauge showed "no score — Not enough data — and this app does not guess",
naming each missing input with real numbers and thresholds: "Only 46/130/207 graded study reviews;
need at least 300", "Topic coverage is 32%; need at least 70%. Not studied yet: Financial
Statement Analysis, …" (this line correctly _disappeared_ once coverage hit 82%), "Only 0 delayed
held-out probe outcomes; need at least 50. **1 more were answered too soon after study and are
excluded (≥7-day rule)**" (appeared right after my probe answer — the exclusion works), and the
band-width abstention. No pass % anywhere while abstaining. Probe cards are disclosed as excluded
from Memory/coverage ("70 held-out probe cards are excluded… the measurement instrument never
feeds the gauges it tests"), and 119 `aig::ungraded` ladder cards are excluded from all gauges
with an on-page note. Performance gauge is loudly labelled UNCALIBRATED ESTIMATE with the ±0.15
widening explained. This is the most honest study dashboard I have seen.

**Concept map** (veteran_20): renders 86 nodes / 467 edges force-directed, clustered under the 10
CFA topic headings, with an honest grey "no data yet" legend and difficulty/FSRS-recall colour
toggles. One 500 in console. Real issue: scaffolding tags (`worked`, `faded`, `solve`,
`ungraded`, `high`, `low`, `compare`) get their own nodes — pure noise in a knowledge map.

**Performance:** median next-card latency through the full CDP round-trip was 134 ms (min 110,
max 188 over 44 cards), answer reveal ~4 ms as measured by button-state polling; subjectively
indistinguishable from my vanilla setup, on a machine concurrently running three other app
instances. Not a rigorous benchmark, but no regression signal.

---

## 3. Formatting / UX observations

- **Grade buttons & ETAs** (veteran_05/07): stock layout, `Again<1m / Hard<6m / Good<10m / Easy 6d`
  (FSRS-adjusted, e.g. `Easy 8d` after prior Good) — identical to vanilla; muscle memory intact.
- **Speedrun deck-options sections** are cleanly integrated (own titled containers, help
  modals with per-setting carousel docs, conditional sub-fields). The help-modal "manual" link
  points at the GitHub repo root rather than a real manual page.
- **Preset blast radius:** the Speedrun toggles live on the _preset_ ("Default (used by 3 decks)"),
  so enabling contrast for the CFA deck silently also enables it for CFA Probes and Default.
  Stock-Anki semantics, but these new toggles make the footgun bigger; a per-deck hint would help.
- **Dashboard subtitle:** "Weights: CFA Institute, Level I exam topic weight ranges (2025-2026
  curriculum) (2026)." — the dangling "(2026)" reads like a citation bug.
- **Exam-date row:** Save greys out after saving and the "fade ladder is disabled" warning clears
  (good), but there's no positive confirmation ("Exam in 90 days" would be better).
- **Facts strip vs gauge duplication:** "EXAM COVERAGE (STUDIED) 32% / COVERAGE IN DECK 100% /
  GRADED REVIEWS / DELAYED PROBES / BEST NEXT" is a good power-user surface.
- **Topic table**: sorted by weighted gap with unstudied topics first and honest "no data" cells —
  exactly what a CFA candidate wants; Performance* footnote explains the asterisk properly.
- **Concept map**: needs a tag filter (scaffold tags as nodes) and node labels truncate awkwardly
  at small zoom; topic headings overlap nodes at default zoom (veteran_20 top-left).
- **Stats page**: full stock graph suite including FSRS stability/difficulty panels — nothing
  removed (veteran_10/11). True-Retention panel not checked in detail.
- **Browse/editor**: loads fine; hierarchical tags shown abbreviated (`…∷worked`) as in stock.
- **Congrats screen**: stock wording (veteran_29); fine.
- **Probe cards** (veteran_26/27) are genuinely exam-style vignettes with a proper
  "Correct answer: B / Why B is correct…" back — better item quality than the flashcards.

---

## 4. Learning experience vs. traditional Anki

**What's genuinely better for a CFA candidate:**

- The **honest Readiness gauge**: named, quantified abstention beats every "you're 87% ready!"
  dashboard on the market. The give-up rule being enforced in the Rust engine (and surfaced
  verbatim on the page) is the right architecture, and my session confirmed it in practice.
- The **fade ladder** is real pedagogy (worked→faded→solve), and it demonstrably stopped the
  day-1 "here's an MCQ about a formula you've seen once" firehose that vanilla Anki + a
  pre-built deck produces. This is something stock Anki simply cannot express.
- **Held-out probes** with the ≥7-day delay rule are a legitimately good measurement design; the
  probe items themselves are the best content in the bundle.
- **Contrast scheduling** works mechanically and is a real gap in stock Anki (which actively
  _spaces_ confusables apart). d=0.76-style claims aside, back-to-back FIFO/LIFO discrimination
  is how I'd want to drill those.
- Everything I rely on from stock Anki — reviewer flow, shortcuts (by affordance), FSRS, stats,
  browse, deck options — is still there and fast.

**What's worse / missing:**

- Polish: 500s and a scary "database inconsistent" log line on a fresh profile, an
  uncaught TypeError on every deck-options open, inconsistent counters,
  degenerate 100%–100% ranges. None data-destroying, all confidence-eroding.
- The headline contrast feature is **invisible on the shipped content** with default settings
  — a first-run user flips the switch and sees nothing.
- Config surface: ~13 new deck-config fields across three sections. Defaults are sensible and
  everything is default-OFF, but the fade ladder's five tunables (hysteresis bounds, floor,
  promotion sessions, order) are expert-only knobs sitting in every user's dialog.
- Ecosystem: my add-ons (FSRS Helper, Review Heatmap) and AnkiWeb sync are untested here; a
  veteran switching cold-turkey is betting on a fork keeping pace with upstream.

**Would Vera switch?** For CFA prep specifically: yes, as a dedicated profile — the ladder +
probes + honest dashboard are worth the rough edges, and nothing about core reviewing regressed
in what I could verify. For my general decks: no; vanilla Anki + my add-ons still wins, and the
Speedrun features are CFA-shaped anyway. The complexity pays off only because it's opt-in and
default-off; ship it with mined `confusable::high` tags on the main deck (or feedback in the UI)
or the flagship toggle will keep looking like a placebo.

---

## 5. Top fixes (ranked)

1. **Stop the 500s on unset `speedrun:*` config keys**: return a default/null instead of
   `NotFoundError`, and never print "Your database appears to be in an inconsistent state" for a
   missing optional key. Highest scare-per-byte in the product.
2. **Make contrast scheduling observable**: show "N clusters eligible / M gated on" next
   to the toggle (the queue builder already knows), and ship `confusable::high` markers on the
   sample deck — otherwise the headline feature is a silent no-op.
3. **Unify the graded-reviews counters**: one definition (exclude probe answers
   everywhere, matching the readiness gate) or explicit labels; the same number appearing twice
   with different values undermines the honesty brand.
4. **Fix the deck-options uncaught TypeError**: guard `subgraph_data[0]` in
   `ts/routes/graphs/simulator.ts:117`.
5. **Dashboard/map cosmetics**: collapse degenerate "100% – 100%" ranges,
   drop the dangling "(2026)", and filter `rung::`/`interactivity::`/`aig::` scaffold tags out of
   the concept map.
