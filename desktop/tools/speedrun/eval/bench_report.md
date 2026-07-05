# Speedrun benchmark report (§10 targets)

Generated: 2026-07-05T08:02:11+00:00

Machine: Apple M1 Max (10 cores), 32.0 GB RAM, macOS 26.5.1, Python 3.13.5 — read at run time, not hard-coded.

Deck: 50000 cards (1000 graded through the real v3 scheduler with FSRS on, 14000 spread over the next 28 days), built in 5.2s (reused cached build this run); per-day limits raised to 2000 new / 9999 review so queues never empty mid-benchmark.

## Results vs §10 targets

| action                | n  | p50 ms  | p95 ms  | worst ms | §10 target              | verdict |
| --------------------- | -- | ------- | ------- | -------- | ----------------------- | ------- |
| button_press_ack      | 60 | 0.579   | 0.707   | 0.962    | p95 < 50 ms             | PASS    |
| next_card_after_grade | 60 | 1.034   | 1.284   | 1.376    | p95 < 100 ms            | PASS    |
| dashboard_first_load  | 10 | 342.474 | 432.233 | 432.233  | p95 < 1 s               | PASS    |
| dashboard_refresh     | 40 | 329.708 | 369.457 | 376.685  | p95 < 500 ms            | PASS    |
| session_sync          | 5  | 12.69   | 17.368  | 17.368   | < 5 s (p95)             | PASS    |
| cold_start            | 5  | 170.341 | 178.518 | 178.518  | < 5 s                   | PASS    |
| peak_memory           | 1  | —       | —       | 93.9 MB  | < 256 MB (stated limit) | PASS    |

Percentile method: nearest-rank (rank = ceil(p/100·n) on the sorted samples); worst = max.

Peak memory: measured 93.9 MB (ru_maxrss = 98435072 bytes; on macOS ru_maxrss is bytes, on Linux KiB) vs stated limit 256 MB. The limit was stated AFTER first measuring (~2.7x headroom over the measured value, rounded up to a power of two) and covers the headless engine process only — the packaged GUI adds Qt/webview memory on top (not measured here). Workload: open + topic_mastery + get_readiness + queue build + 5 answers.

Cold start: child wall time (interpreter spawn + pylib import + collection open + first queue fetch) p95 178.518 ms; in-process portion p95 129.64 ms.

Sync: initial full upload took 621.6 ms (one-time, NOT counted); each sample = answer 20 cards, then one sync_collection() round-trip against the bundled local sync server at http://127.0.0.1:28701/.

## Definitions and honest disclosures

- **Engine data-path times, measured headlessly.** UI paint adds client-side time on top. The real app never blocks its UI thread on these calls: the dashboard page awaits topicMastery() / getReadiness() through the async @generated/backend POST bridge (see `ts/routes/dashboard/DashboardPage.svelte`), and the reviewer awaits the same scheduler RPCs. Numbers here are NOT paint times and are not claimed as such.
- **button_press_ack** = answer_card() round-trip alone; **next_card_after_grade** = answer_card() + get_queued_cards() measured back-to-back in one span (the full grade→next-card data path). The next card's presence is asserted every sample.
- **dashboard_first_load** opens a fresh backend + collection per sample (close+reopen). 'Cold' means a fresh process-level open, not a cold OS page cache — the file was just written/read, so true disk-cold first loads may be slower.
- **session_sync** talks to the bundled sync server over loopback; real-network sync adds latency/bandwidth on top. Samples assert the sync completed (required=NO_CHANGES).
- **cold_start** is the headless engine path. The packaged desktop app adds Qt/webview startup on top; there is no display in this environment, so that part is not measured (and not invented).
- **Screen-freeze (§10 'nothing freezes > 100 ms')** can only be PROXIED headlessly: the engine-side latencies above plus the fact that the app performs them off the UI thread (async POST bridge / background threads). No paint/frame timing was measured; this is a proxy, disclosed as such.
- **Phone targets**: §10 also lists phone-side timings. They require an instrumented device; they were NOT measured here and no phone number in this report is real — none is given.
- get_readiness() abstains on this synthetic deck (no held-out probe outcomes), but the RPC still executes its full SQL/aggregation pass — the measured cost is the real data path.
- Deck layout (topics/clusters/ratings/due days) is a pure function of the card index (seeded constants); note IDs and review timestamps use the wall clock, so a --rebuild produces an equivalent but not byte-identical deck. Measurements always run on a fresh copy of the cached build.
- Warmups discarded: 3 review iterations, 2 refresh iterations (disclosed; all other samples kept, including the slowest).

## How to re-run

    cd desktop && PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/bench.py

(`just bench` runs the same command.) Add `--rebuild` to rebuild the cached 50k deck from scratch.
