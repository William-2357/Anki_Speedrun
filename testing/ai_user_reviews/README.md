# AI User Reviews — the five individual persona reports

The five-persona AI user-test reports, one file per persona, duplicated here so the
`testing/` folder is a self-contained review package. These are copies of the
authoritative reports in the program home, `desktop/tools/speedrun/usertest/`, **with the
`## 2. Bugs & errors` section (the per-agent bug table and its supporting notes) removed**
from each — see Provenance below. For the full bug detail, read the originals in
`usertest/`; for the cross-persona synthesis (consensus defects, top fixes, verdicts) see
[`../AI_USER_TESTS.md`](../AI_USER_TESTS.md).

App under test: Anki Speedrun desktop `26.05b1` — the real headed Qt build, driven over
CDP. Run date: 2026-07-05.

## The five reviews

| Review | Persona | Profile | Mission | Volume | Switch verdict |
| --- | --- | --- | --- | --- | --- |
| [`report_novice.md`](./report_novice.md) | **Nadia** | CFA candidate, never used Anki/SRS | First-15-minutes experience | 46 graded reviews | No, not on this build |
| [`report_veteran.md`](./report_veteran.md) | **Vera** | 8-yr Anki power user, FSRS, keyboard | Hunt regressions; verify features | 208 reviews + A/B/C contrast experiment | Yes (CFA prep specifically) |
| [`report_skeptic.md`](./report_skeptic.md) | **Sana** | Quant risk analyst | Make the app *lie* about a number | 33 reviews + adversarial engine grinds | Yes, cautiously |
| [`report_ux.md`](./report_ux.md) | **Uma** | Product designer / a11y reviewer | WCAG + formatting audit | 15 reviews + measurement pass | Yes, tentatively |
| [`report_auditor.md`](./report_auditor.md) | **Ada** | Data-skeptical engineer | Trust-the-instruments due diligence | 90 engine reviews; 570-test suite; 5 harnesses | Recommends with framing |

## Evidence (not copied here)

Each report cites screenshots and console/engine dumps by filename (e.g.
`novice_01_zerostate_top.png`, `auditor_unittest_output.txt`). Those 123 evidence files
are **not** duplicated into this folder — they stay in
`desktop/tools/speedrun/usertest/artifacts/`. Open a report's cited filename from there.

## Provenance

Copied on 2026-07-05 from `desktop/tools/speedrun/usertest/report_*.md`, then the
`## 2. Bugs & errors` section was stripped from each copy (heading, table, and the
notes that referenced it). After the removal the remaining sections were renumbered to
stay contiguous, the reports' own `§`-cross-references were shifted to match, and the
now-dead "Bug #N" pointers were scrubbed from the prose (external references such as
`PRIMER §1a` were left intact). For the full per-bug detail, read the `usertest/`
originals — they remain the source of truth (they sit alongside the harness, `bases/`,
and `artifacts/` those reports reference); if they change, re-copy and re-apply.
