# Crash + network-off test report

Generated: 2026-07-05T05:53:02+00:00 — seed 20260704, collection `/Users/william/Anki_Speedrun/desktop/out/speedrun_eval/crash/crash_collection.anki2`

Invocation: `PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/crash_test.py`

## Crash section (SIGKILL mid-review)

**CORRUPTED COLLECTIONS: 0 OF 20** — 20 iterations, 14148 answers committed through the real v3 scheduler, 7 in-flight answer(s) rolled back (correct SQLite behaviour, not corruption), 4 committed-but-unlogged (kill landed between commit and log write).

| iter | kill delay s | attempted | committed | revlog delta | rolled back | integrity | quick | engine check | corrupted |
| ---- | ------------ | --------- | --------- | ------------ | ----------- | --------- | ----- | ------------ | --------- |
| 1    | 1.314        | 1327      | 1327      | 1327         | 0           | ok        | ok    | ok           | no        |
| 2    | 0.394        | 399       | 398       | 398          | 1           | ok        | ok    | ok           | no        |
| 3    | 0.923        | 920       | 920       | 920          | 0           | ok        | ok    | ok           | no        |
| 4    | 0.939        | 938       | 937       | 938          | 0           | ok        | ok    | ok           | no        |
| 5    | 0.366        | 328       | 327       | 328          | 0           | ok        | ok    | ok           | no        |
| 6    | 0.541        | 442       | 441       | 441          | 1           | ok        | ok    | ok           | no        |
| 7    | 0.249        | 221       | 221       | 221          | 0           | ok        | ok    | ok           | no        |
| 8    | 0.671        | 666       | 666       | 666          | 0           | ok        | ok    | ok           | no        |
| 9    | 0.404        | 355       | 354       | 354          | 1           | ok        | ok    | ok           | no        |
| 10   | 0.797        | 797       | 796       | 796          | 1           | ok        | ok    | ok           | no        |
| 11   | 0.769        | 762       | 761       | 761          | 1           | ok        | ok    | ok           | no        |
| 12   | 0.622        | 591       | 590       | 591          | 0           | ok        | ok    | ok           | no        |
| 13   | 1.203        | 1096      | 1096      | 1096         | 0           | ok        | ok    | ok           | no        |
| 14   | 0.413        | 414       | 414       | 414          | 0           | ok        | ok    | ok           | no        |
| 15   | 1.467        | 1452      | 1452      | 1452         | 0           | ok        | ok    | ok           | no        |
| 16   | 0.223        | 211       | 210       | 211          | 0           | ok        | ok    | ok           | no        |
| 17   | 1.169        | 1143      | 1142      | 1142         | 1           | ok        | ok    | ok           | no        |
| 18   | 1.213        | 1198      | 1197      | 1197         | 1           | ok        | ok    | ok           | no        |
| 19   | 0.55         | 544       | 544       | 544          | 0           | ok        | ok    | ok           | no        |
| 20   | 0.404        | 355       | 355       | 355          | 0           | ok        | ok    | ok           | no        |

## Network-off section (AI enabled, endpoint unreachable)

- PASS: AI flags default to OFF — flags={'speedrun:aiAssist': False, 'speedrun:coachEnabled': False, 'speedrun:debriefEnabled': False}
- PASS: AI flags enabled on collection config (read back) — speedrun:aiBackend='openai-compatible'
- PASS: backend config names ONLY the dead loopback endpoint — base_url='http://127.0.0.1:51552'
- PASS: coach call abstains gracefully (no exception escapes) — reason='backend error or timeout' in 0.016s
- PASS: grounded_complete abstains on the dead endpoint — reason='backend error or timeout'
- PASS: offline mock fallback renders a deterministic plan — outcome='ok'
- PASS: Memory gauge computes (graded reviews + topic rows) — graded_reviews=14152, topics=5, first={'topic': 'economics', 'total': 82, 'studied': 4, 'high_recall': 4, 'avg_retrievability': 1.0}
- PASS: Readiness returns its designed abstention (kind=ABSTAIN, reasons named) — kind=0, missing=['Topic coverage is 56%; need at least 70%. Not studied yet: Financial Statement Analysis, Corporate Issuers, Derivatives, ….', 'Only 0 delayed held-out probe outcomes; need at least 50. The probe bank is not imported (tools/speedrun/build_probe_deck.py builds it).']
- PASS: gauge parity: AI off vs AI on-but-unreachable outputs identical — topic_mastery and get_readiness fingerprints match exactly

Dead endpoint used: `http://127.0.0.1:51552` (loopback port bound then closed; no external host is ever named in the configuration).

## Honesty notes

- SIGKILL of the child review process proves the ENGINE layer (rslib + SQLite, where every collection write lives) survives an abrupt process death mid-transaction. The desktop GUI wraps this same engine in-process, so its writes ride the same journal - but killing the full Qt app additionally exercises window/session teardown, which this script does not simulate. Recommended for the demo video: a manual whole-app `kill -9` spot check while the reviewer is open.
- The child grades with Again/Hard only, so cards recycle intraday forever and the queue never exhausts across 20 iterations. Every grade is the same transaction shape (card update + revlog append); Good/Easy differ only in the state written, not the crash surface.
- Committed answers are counted from the child's flushed COMMIT log lines; SQLite durability means committed-implies-survives. The one in-flight answer may roll back - that is documented SQLite behaviour and is counted separately, never as corruption.
- Opening a killed collection performs SQLite's standard WAL/journal recovery; the integrity checks then run on the recovered database. That recovery is the crash-safety mechanism under test, not a workaround.
- The raw integrity/quick checks run through Python's stdlib sqlite3 (independent of all Anki code), NOT the sqlite3 CLI: Anki's schema declares the custom 'unicase' collation that only rslib registers, so the plain CLI cannot even prepare the pragma. The checker registers a byte-compatible casefold stand-in; a collation divergence could only produce a FALSE index-order complaint, never mask real damage.
- The engine DB check ('fix_integrity') reports fixed problems; the unconditional 'rebuilt and optimized' trailer pylib appends is housekeeping. For a collection this tool built itself, any OTHER line (missing notes, invalid properties, ...) is treated as corruption-class and fails the run - nothing is softened.
- Network-off proof scope: the assistant backend configuration names ONLY the dead loopback endpoint and the call returned the abstain fallback; the gauges are local Rust computations with no network API. The wire was not sniffed - what is proven is that the code path has no other endpoint to talk to and degrades exactly as designed.
- Readiness ABSTAINING on this scratch collection is its designed output (no delayed held-out probe outcomes exist here): the Memory gauge returns computed numbers and Readiness returns its abstention with named reasons. 'Still returns a score' means exactly that pair, stated precisely.
- Android is NOT exercised by this script: android_crash_test.sh ships the equivalent device test and fails fast when no device is attached.
