# Testing — Master Index

One place that catalogs **every automated test** in the repo and **summarizes the AI user-test
program**. These are synthesis/index documents — nothing was moved. Each real suite stays in its
home directory (moving test files would break imports, path assumptions, and CI); this folder
just tells you what exists, where it lives, what it covers, and how to run it.

Generated: 2026-07-05 · Repo: `Anki_Speedrun` · App: `26.05b1`

## Contents

| Doc | What's in it |
| --- | --- |
| [`AUTOMATED_TESTS.md`](./AUTOMATED_TESTS.md) | Full catalog of every automated suite: Speedrun-authored (Python/Rust/TS + measurement harnesses) **and** vendored upstream (Anki pylib/qt/rslib/ts + Android). Counts, locations, coverage, run commands. |
| [`AI_USER_TESTS.md`](./AI_USER_TESTS.md) | Synthesis of the five-persona AI user-test program: methodology, consensus defects, verdicts, top fixes, coverage matrix, links to the 5 reports + 123 artifacts. |
| [`ai_user_reviews/`](./ai_user_reviews/) | The **five individual persona reports** in full (novice, veteran, skeptic, ux, auditor) — verbatim copies of the `usertest/report_*.md` deliverables, plus a folder index. Read these for the per-persona detail behind the synthesis. |

## The whole test landscape in one table

| Layer | Suite | Where it lives | Size | Run-verified? |
| --- | --- | --- | ---: | --- |
| **Speedrun** | Python unit | `desktop/tools/speedrun/tests/` (23 files) | **570 tests** | ✅ 8.0 s, all pass |
| **Speedrun** | Rust unit (new engine modules) | `desktop/rslib/src/{readiness,scheduler/queue/builder,stats}/` | ~53 | static count |
| **Speedrun** | TypeScript/vitest | `desktop/ts/routes/{dashboard,concept-graph}/` | 28 | ✅ 15/15 + 13/13 |
| **Speedrun** | Measurement harnesses | `desktop/tools/speedrun/*.py` → `eval/*_report.*` | 17 tools | ✅ committed reports |
| **Speedrun** | **AI user tests** | `desktop/tools/speedrun/usertest/` | 5 personas, 123 artifacts | ✅ real app over CDP |
| Upstream | Python | `desktop/pylib/tests/`, `desktop/qt/tests/` | 125 | static count |
| Upstream | Rust | `desktop/rslib/` (incl. the 53 above) | ~386 | static count |
| Upstream | TypeScript/vitest | `desktop/ts/` | 44 | static count |
| Upstream | Android | `android/`, `android-backend/` | ~2121 `@Test`, 427 files | static count |

**Speedrun-authored automated tests: ~651** (570 Python + 53 Rust + 28 TS) plus 17 runnable
measurement harnesses and the 5-persona AI user-test program. Everything under "Upstream" is the
inherited regression safety net.

> Count provenance: **run-verified** = executed; **static** = ripgrep count of test
> declarations, reliable as suite size. Older docs cite "561"/"547" for the Speedrun Python
> suite; the current run-verified figure is **570**.

## Run everything (quick reference)

```bash
# Speedrun Python unit suite (570 tests)
cd desktop && PYTHONPATH=out/pylib out/pyenv/bin/python -m unittest discover -s tools/speedrun/tests

# Speedrun + upstream Rust
cd desktop && cargo test -p anki

# Speedrun TS (from ts/ so @generated resolves)
cd desktop/ts && ../node_modules/.bin/vitest run routes/dashboard routes/concept-graph

# Everything (upstream Python/TS/Rust via the build system)
cd desktop && ./check

# Android
cd android && ./gradlew test          # + ./gradlew connectedCheck for instrumented
```

See `AUTOMATED_TESTS.md` for per-suite commands and the individual measurement-harness
invocations, and `AI_USER_TESTS.md` for how to reproduce a persona run over CDP.

## Primary sources (not duplicated here)

- Speedrun tests & harnesses: `desktop/tools/speedrun/tests/`, `desktop/tools/speedrun/*.py`,
  reports in `desktop/tools/speedrun/eval/*_report.md`.
- AI user tests: `desktop/tools/speedrun/usertest/` — `PRIMER.md` (contract), `TEST_RUNS.md`
  (methodology + coverage matrix), `SYNTHESIS.md` (findings), `report_*.md` (5 personas),
  `artifacts/` (123 evidence files). The 5 `report_*.md` files are also mirrored verbatim
  into [`ai_user_reviews/`](./ai_user_reviews/) for convenience; the `usertest/` copies
  remain the source of truth (they sit next to the harness + artifacts they cite).
