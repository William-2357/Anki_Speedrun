# AI User Tests — Synthesis

Program home: `desktop/tools/speedrun/usertest/` · Run date: 2026-07-05 (~15:00–16:35 CDT)
App under test: Anki Speedrun desktop `26.05b1` — the **real headed Qt build**, driven over CDP
(not mocks, not the Vite dev server).

This is a condensed synthesis with pointers to the primary sources. The authoritative documents
are:

- `usertest/PRIMER.md` — the shared test contract every persona followed (verified recipes,
  isolation rules, required report format, rigor bar).
- `usertest/TEST_RUNS.md` — how the runs were executed: environments, journeys, the full
  coverage matrix, provenance & incidents.
- `usertest/SYNTHESIS.md` — the full cross-persona findings synthesis (source for §2–§5 here).
- `usertest/report_{novice,veteran,skeptic,ux,auditor}.md` — the five full persona reports.
  These are mirrored verbatim into [`ai_user_reviews/`](./ai_user_reviews/) alongside this
  doc; the `usertest/` copies remain the source of truth.
- `usertest/artifacts/` — **123 evidence files** (screenshots + console/engine dumps), cited
  per finding.
- `usertest/cdp.js`, `usertest/launch_instance.sh`, `usertest/bases/` — the harness.

---

## What this is

Five independent **AI agent "users"**, each given a distinct persona and mission, used the app
end-to-end to hunt for real bugs (technical, formatting, UX, honesty) and to judge the learning
experience vs. traditional Anki. Each agent drove the real app through QtWebEngine remote
debugging (real DOM clicks on the deck list / reviewer / toolbars / dashboard), captured
screenshots and JS console on every page, and — where relevant — dropped to the engine
(`PYTHONPATH=out/pylib`) for RPC truth-checks and adversarial scenarios. Every claim had to be
reproduced or explicitly marked "not verified."

### The five personas

| Persona | Profile | Mission | Volume | Report |
| --- | --- | --- | --- | --- |
| **Novice** (Nadia) | CFA candidate, never used Anki/SRS | First-15-minutes experience | 46 graded reviews | [`report_novice.md`](./ai_user_reviews/report_novice.md) |
| **Veteran** (Vera) | 8-yr Anki power user, FSRS, keyboard | Hunt regressions; verify features | 208 reviews + A/B/C contrast experiment | [`report_veteran.md`](./ai_user_reviews/report_veteran.md) |
| **Skeptic** (Sana) | Quant risk analyst | Make the app *lie* about a number | 33 reviews + adversarial engine grinds | [`report_skeptic.md`](./ai_user_reviews/report_skeptic.md) |
| **UX** (Uma) | Product designer / a11y reviewer | WCAG + formatting audit | 15 reviews + measurement pass | [`report_ux.md`](./ai_user_reviews/report_ux.md) |
| **Auditor** (Ada) | Data-skeptical engineer | Trust-the-instruments due diligence | 90 engine reviews; 561 unit tests (570 today); 5 harnesses re-run | [`report_auditor.md`](./ai_user_reviews/report_auditor.md) |

Isolation: one app instance per persona (own `bases/<persona>` dir, own ports, own `/tmp` log),
four GUI instances concurrent plus the auditor at engine level; no repo source modified; the
auditor restored `eval/` byte-identical afterward. Known harness limit: synthetic CDP key events
don't reach Qt's shortcut handler and native dialogs aren't scriptable, so keyboard/native flows
are reported via affordances + code citations, never claimed as broken.

---

## Overall verdict

**The core promise — honest, evidence-gated readiness measurement — is real and survived both
adversarial and mechanical verification.** Abstention gates are literal Rust engine constants;
an abstaining response zeroes every number in the response constructor, so no display layer can
leak an unearned pass %. The skeptic could not make the shipped honest-input path lie; the
auditor's 26/26 mechanical honesty-contract checks passed with zero tracebacks, and every
re-runnable harness reproduced its committed numbers (561/561 unit tests green at audit time;
the suite has since grown to **570/570**, re-verified 2026-07-05).

**What drags the product down is polish and approachability, not architecture.** All four GUI
personas independently hit the same trust-eroding dashboard defects, and the novice found the app
effectively dead-on-arrival for a first-time user (no in-app CFA content or onboarding).
Accessibility on the new surfaces is below stock Anki's already-mediocre bar.

Switch verdicts: veteran **yes** (CFA prep specifically) · skeptic **yes, cautiously** ·
UX **yes tentatively** · auditor **recommends with framing** · novice **no, not on this build**.

---

## Consensus defects (found independently by 3–5 personas)

Full detail + evidence in `SYNTHESIS.md §2`. Severity in the app's own terms.

| # | Sev | Finding | Root cause (file:line) |
| --- | --- | --- | --- |
| **C1** | Major | Every dashboard/concept-map load fires **HTTP 500s** for never-set config keys (`speedrun:exam_date`, `speedrun:tagTopicMap`) and logs a scary "database inconsistent" traceback on healthy fresh profiles. | `getConfigJson` raises `NotFoundError` for unset optional keys (`qt/aqt/mediasrv.py:793,812`); client swallows it but the 500 + log still fire. **Unanimous top fix.** |
| **C2** | Major | **Two contradictory "what should I study next?" answers on one screen** (engine "Best next topic" vs frontend "Best next thing to study"). | Two algorithms: `rslib/.../readiness/mod.rs:401-431` vs `ts/routes/dashboard/metrics.ts:266-267`; proto comment for `best_next_topic` is inverted. |
| **C3** | Major | **Two "graded reviews" counters disagree** once a probe is answered (gate excludes probe answers, meta strip includes them). | `metrics.ts:198` uses all-ease revlog; `readiness/mod.rs:175-177` subtracts probe reviews. |
| **C4** | Minor | **"Topic coverage is -0%"** (negative zero) in the flagship abstention message on fresh collections — a signed zero on the "we never show a wrong number" surface. | Empty-iterator `f32` sum → `-0.0` (`readiness/mod.rs:153-163`); one-line fix. |
| **C5** | Minor | **Memory gauge reads "100% (100%–100%)"** minutes after a first cram under a "CONFIDENCE: LOW" badge — manufactures the exact overconfidence the app exists to prevent. | Zero-width interval not suppressed (`metrics.ts:334-353`, `GaugeCard.svelte:38-45`). |
| **C6** | Nit | Header citation renders a **double year**: "…(2025-2026 curriculum) (2026)." | `DashboardPage.svelte:270-273` + `topics.ts:70-71` + `cfa_weights_2026.json:2-3`. |
| **C7** | Nit | **MathJax warnings on every card render** (`[tex]/noerrors`, `[tex]/mathtools`). | Reviewer console, all four GUI sessions. |
| **C8** | Minor | **First launch prints a crash-looking traceback** (`profiles.py:435 _loadMeta`) before recovering. | Known-benign first-run path; cost the UX tester one failed launch. |

Also observed by two personas: UI copy leaks developer artifacts (abstention bullet names
`tools/speedrun/build_probe_deck.py`, "half-width 0.48 > 0.2", "MPS proxy", unexplained "FSRS").
The auditor notes these strings are truthful/actionable — the fix is phrasing, not removal.

---

## Notable single-persona findings

- **Novice — Blocker:** a brand-new user cannot find any CFA content or guidance in-app
  (zero state is a bare "Default" deck; the word "CFA" appears nowhere on the first screen).
  The dashboard's "Enable FSRS in deck options" instruction isn't actionable (toggle buried in a
  9-screen expert dialog).
- **Skeptic — Major:** a same-day fake "pass 90–98%" is achievable by deliberate gaming (the
  ≥7-day probe-delay rule is waived for never-studied clusters). Garbage-in, not fabrication —
  every input was user-falsified — but it contradicts the README's 7-day wording. Honest use
  fails toward pessimism.
- **UX — Major:** `button { outline: none !important }` destroys the keyboard focus ring on the
  entire study loop (WCAG 2.4.7); muted text fails AA contrast across the dashboard (measured
  3.27:1); the concept map is mouse-only and illegible at default zoom (~5px labels).
- **Veteran — Minor (headline-relevant):** contrast scheduling is a **silent no-op on the
  shipped sample deck** — it carries `cluster::*` tags but zero `confusable::high` markers, so
  the flagship toggle changes nothing with no feedback. The mechanism itself was verified real at
  the engine level (A/B/C queue-order experiment).
- **Auditor — zero Blocker/Major.** Unit suite passes (561/561 at audit time; **570/570** today
  after a later "Phase 3 model tests" commit); 26/26 mechanical honesty checks, zero tracebacks;
  every re-runnable harness reproduced its committed numbers exactly. Remaining items are
  doc/contract drift (stale test counts, inverted proto comments, "30x2" vs "35×2").

---

## What was verified good (cross-checked)

- **Honesty contract holds under honest use** — adversarially (skeptic), mechanically (auditor
  26/26), and observationally (veteran at 46/131/208 reviews; novice/ux zero states). No
  fabricated pass % appeared in any session.
- **The instrument can't feed itself** — probe cards excluded from Memory/coverage; probe answers
  don't count as study reviews (engine level); AI-generated cards quarantined.
- **The AI assistant is honesty-contained** — default-OFF (DOM-verified), server-side re-check,
  read-only, no free-text attack surface, refused to state scores while abstaining, injection
  eval ALL PASS.
- **Headline features are real** — contrast reorders as documented (engine A/B/C); the fade
  ladder gated 127 live introductions correctly; the concept map renders honest no-data states.
- **Core Anki is intact** — reviewer flow, ETAs, stats, browse, persistence, ~134 ms median
  next-card latency under 4-instance load; responsive at 800–500 px.

---

## Consolidated top fixes (ranked by cross-report weight)

1. **Return defaults for unset `speedrun:*` config keys** — kill the per-load 500s and the
   "database inconsistent" log (C1; unanimous).
2. **One "what next" and one review count** — unify engine/frontend best-next; reconcile the two
   graded-review counters; fix the inverted/stale proto comments (C2 + C3).
3. **First-run CFA onboarding + actionable FSRS** — one-click "Start CFA Level I" import and a
   deep-linked plain-English FSRS enable (novice Blocker).
4. **Accessibility floor on new surfaces** — AA-contrast muted text, restore `:focus-visible`,
   concept-map keyboard access + accessible names + a second colour channel, set `document.title`.
5. **Make contrast scheduling observable** — ship `confusable::high` markers on the sample deck
   and show "N clusters eligible" next to the toggle (veteran).
6. **Tighten the never-studied probe waiver** — require minimum collection age to match the
   README's 7-day promise (skeptic).
7. **Cosmetics + drift batch** — `-0%` clamp, degenerate "100%–100%" ranges, double year,
   deck-options TypeError guard, probe badge, doc refreshes (547/561 → 570, 30x2 → 35×2).

---

## Coverage matrix (condensed)

Full matrix with per-persona ✔/◐/✗ in `TEST_RUNS.md §3`. Exercised **live** across personas:
first-run/zero state, deck list, reviewer study loop (real grading), grade ETAs/latency, Stats,
deck options incl. Speedrun sections, contrast scheduling (UI + engine), fade ladder (live ×127),
exam date persist, dashboard gauges + abstention, readiness honesty contract (adversarial +
mechanical), held-out probes + ≥7-day rule, concept map, AI assistant (mock backend),
`?readinessTest=1` dev mode, engine RPCs (6 phases), the 570-test unit suite + 5 harnesses,
docs-vs-code-vs-runtime audit, responsive layout, persistence across relaunch.

**Not covered by anyone (honest gaps):** real keyboard input & undo (CDP limitation); native Qt
surfaces (menu bar, gear menu, file-picker import, Add commit, Browse card table); AnkiWeb sync;
add-on ecosystem; the Android app; installer artifacts (hashes re-verified, not installed);
non-US locales.

---

## How to reproduce a run

From `PRIMER.md` (verified recipe):

```bash
# 1. launch one instance for a persona (your own base dir + ports)
desktop/tools/speedrun/usertest/launch_instance.sh \
  desktop/tools/speedrun/usertest/bases/<persona> <API_PORT> <CDP_PORT> <fake_user> \
  > /tmp/usertest_<persona>.log 2>&1 &

# 2. drive it with the zero-dep CDP client (from usertest/)
#    const { findPage } = require("./cdp");
#    const main = await findPage(<CDP_PORT>, /main webview/);
#    await main.screenshot("/tmp/x.png");

# 3. tear down
kill -TERM <pid>; pkill -f "usertest/bases/<persona>"
```

Do **not** use Playwright's `connectOverCDP` — QtWebEngine lacks
`Browser.setDownloadBehavior` and Playwright dies; use the bundled `cdp.js`.
