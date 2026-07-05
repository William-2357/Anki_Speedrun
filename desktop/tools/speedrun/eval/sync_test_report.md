# Sync test report (two clients, offline reviews, conflict rule)

Generated: 2026-07-05T05:48:57+00:00 — server port 28711, seed 20260704

Invocation: `PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/sync_test.py`

## Setup

- client A created with 40 cards; first sync required=4 -> full upload
- client B cloned by full download (required=3); card ids match A: True

## Offline reviews -> reconnect -> union

- A answered 10 cards offline; B answered 10 DIFFERENT cards (server process stopped during the phase: True)

| side | expected (A's 10 + B's 10) | found | lost | duplicated |
| ---- | -------------------------- | ----- | ---- | ---------- |
| A    | 20                         | 20    | 0    | 0          |
| B    | 20                         | 20    | 0    | 0          |

Union check: **PASS** — 20 expected entries, 0 lost, 0 duplicated, 0 id collisions, 0 cross-side id collisions.
Full revlog tables identical on both sides: True

## Conflict: same card answered on both clients offline

- card id 1783230540003: A answered ease 3 (revlog id 1783230550111), B answered ease 1 (revlog id 1783230551319) — 1 s apart
- both revlog entries present on A: True; on B: True (append-only history - two real reviews, neither double-counted)
- scheduling-state winner: **B** — client B's state won: its card.mod (1783230551) is newer, matching rslib's add_or_update_card_if_newer rule

## Honesty notes

- "Offline" here means no sync calls were made between the phases - honest for Anki's model, where sync is manual and explicit. The server process was additionally STOPPED during the offline phases, so any stray sync attempt would have failed loudly.
- Observed protocol hazard (found by an earlier run of this script, now guarded): revlog ids are taken-at epoch-MILLISECONDS and the append-only merge is INSERT OR IGNORE on that id, so two DIFFERENT reviews graded in the same millisecond on two different devices collide and the later-arriving entry is dropped. Unreachable at human review speed (reviews are seconds apart); this script now spaces scripted answers 50 ms apart to model that, and keeps a cross-side collision tripwire in the union check so the hazard is detected loudly rather than masked.
- The revlog id doubles as the taken-at epoch-ms and the table's primary key; the server merges revlog entries append-only (rslib sync/collection/chunks.rs merge_revlog -> INSERT OR IGNORE). Both conflict reviews therefore survive as two REAL distinct reviews; review counts add, nothing is double-counted.
- Card scheduling conflicts resolve by modification time: add_or_update_card_if_newer keeps the locally-modified copy unless the incoming one is newer. The test spaces the two answers >1 s apart so card.mod (seconds) orders them deterministically.
- The phone offline path is exercised by AnkiDroid using this same sync protocol against the same server implementation; this script does not re-prove the Android client itself.
- This script talks only to 127.0.0.1 on its own port (28711 by default; the bench agent owns 28701).
