# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Crash + network-off robustness test (Anki Speedrun, challenge 7g).

One command, two proofs:

1. **Crash section** (default 20 iterations): builds a cached, seeded
   scratch collection under ``out/speedrun_eval/crash/`` with a few
   hundred due cards, then repeatedly spawns a CHILD process that opens
   the collection and grades due cards in a tight loop through the REAL
   v3 scheduler (``get_queued_cards`` -> ``build_answer`` ->
   ``answer_card``), writing an ``ATTEMPT``/``COMMIT`` line pair per
   answer to a log the parent reads. The PARENT waits a seeded random
   0.2-1.5 s after the child's first committed answer, then SIGKILLs it
   (kill -9, no cleanup) - mid-review, usually mid-SQLite-write. After
   every kill the parent verifies:

   * ``pragma integrity_check`` AND ``pragma quick_check`` both say
     ``ok`` (stdlib sqlite3 on the raw file - NOT through Anki code;
     Anki's schema declares the custom ``unicase`` collation, which only
     rslib registers, so the checker registers a byte-compatible Python
     equivalent - the plain sqlite3 CLI cannot even prepare the pragma.
     Opening the collection performs SQLite's standard WAL/journal crash
     recovery first; that recovery IS the mechanism under test),
   * pylib can reopen the collection and the engine's own check
     (``Collection.fix_integrity`` -> rslib ``check_database``) reports
     no problems (the unconditional "rebuilt and optimized" notice pylib
     appends is housekeeping, not a problem - see
     ``classify_db_check``),
   * review accounting is sane: the revlog count never decreases, every
     answer the child logged as committed is present, and at most ONE
     in-flight answer was rolled back (SQLite durability: committed
     means survives; the single in-flight transaction MAY roll back -
     that is correct behaviour, counted and reported separately).

2. **Network-off section** (runs by default): with the AI-assist flags
   enabled on the scratch collection and the ``openai-compatible``
   backend pointed at a provably dead local endpoint (a just-closed
   loopback port), the assistant adapter must abstain gracefully (no
   exception escapes; the caller falls back to its deterministic view)
   while the deterministic gauges (``col.topic_mastery()`` +
   ``col.get_readiness()``) still return: Memory computes numbers and
   Readiness abstains BY DESIGN on a collection with no delayed probe
   outcomes - abstention with named reasons IS its honest score. The
   same gauges run with AI fully OFF to show parity.

Reports: eval/crash_test_report.json + eval/crash_test_report.md.
Exit code is non-zero on ANY corruption, accounting anomaly, or
network-off assertion failure. Zero corruption is reported as
"corrupted collections: 0 of 20" only when it is actually true.

Usage (from desktop/, needs the built pylib):

    PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/crash_test.py

The module itself imports only the stdlib; pylib is imported lazily
inside the functions that need it, so the unit tests
(tests/test_crash_test.py) run under plain python3.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DESKTOP_DIR = HERE.parents[1]
DEFAULT_SCRATCH_DIR = DESKTOP_DIR / "out" / "speedrun_eval" / "crash"
DEFAULT_REPORT_DIR = HERE / "eval"

DEFAULT_ITERATIONS = 20
DEFAULT_CARDS = 400
DEFAULT_SEED = 20260704
KILL_DELAY_RANGE_S = (0.2, 1.5)
#: Child gives up after this much continuous queue emptiness (transient
#: empties happen while the learn-ahead cutoff advances; retries recover).
CHILD_EMPTY_GIVE_UP_S = 3.0
#: Safety cap: the parent kills long before this.
CHILD_MAX_SECONDS = 30.0
#: Parent waits at most this long for the child's first committed answer.
FIRST_COMMIT_TIMEOUT_S = 20.0

DECK_NAME = "CFA Crash Test"
PRESET_NAME = "CFA Crash Test"
SEED_MARKER_NAME = "seed_marker.json"
COLLECTION_NAME = "crash_collection.anki2"
CHILD_LOG_NAME = "child_answers.log"

#: The AI flags the dashboard uses (qt/aqt/speedrun_assistant.py).
AI_FLAG_KEYS = (
    "speedrun:aiAssist",
    "speedrun:coachEnabled",
    "speedrun:debriefEnabled",
)
AI_BACKEND_KEY = "speedrun:aiBackend"


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no pylib)
# ---------------------------------------------------------------------------


def parse_child_log(text: str) -> dict[str, int]:
    """Count complete ATTEMPT/COMMIT lines in a child answer log.

    The child writes ``ATTEMPT <seq> <cid>`` (flushed) immediately before
    calling ``answer_card`` and ``COMMIT <seq> <cid>`` (flushed) right
    after it returns. A SIGKILL can tear the final line mid-write, so a
    trailing fragment without a newline is dropped rather than guessed
    at.
    """
    if not text:
        return {"attempted": 0, "committed": 0, "queue_empty_exit": 0}
    lines = text.split("\n")
    if text and not text.endswith("\n"):
        lines = lines[:-1]  # torn trailing fragment: ignore
    attempted = 0
    committed = 0
    queue_empty = 0
    for line in lines:
        if line.startswith("ATTEMPT "):
            attempted += 1
        elif line.startswith("COMMIT "):
            committed += 1
        elif line.startswith("EXIT queue_empty"):
            queue_empty += 1
    return {
        "attempted": attempted,
        "committed": committed,
        "queue_empty_exit": queue_empty,
    }


def classify_iteration(
    revlog_before: int,
    revlog_after: int,
    attempted: int,
    committed: int,
) -> dict[str, Any]:
    """Committed-vs-in-flight accounting for one kill iteration.

    Valid (attempted - committed, revlog_delta - committed) pairs:

    * ``(0, 0)`` - the kill landed between answers; nothing in flight.
    * ``(1, 0)`` - one answer was in flight and ROLLED BACK. Correct
      SQLite behaviour, not corruption; counted separately.
    * ``(1, 1)`` - the in-flight answer COMMITTED but the child died
      before writing its COMMIT log line. Also correct.

    Anything else is reported: a delta below ``committed`` means a
    COMMITTED answer vanished (durability violation - corruption); a
    delta above ``committed + 1`` means phantom writes; a shrinking
    revlog is corruption outright.
    """
    delta = revlog_after - revlog_before
    in_flight = attempted - committed
    corruption: list[str] = []
    anomalies: list[str] = []
    rolled_back = 0
    committed_unlogged = 0

    if revlog_after < revlog_before:
        corruption.append(
            f"revlog count DECREASED across the kill: {revlog_before} -> {revlog_after}"
        )
    if delta < committed:
        corruption.append(
            f"durability violation: child logged {committed} committed "
            f"answers but only {delta} arrived in the revlog "
            f"({committed - delta} lost)"
        )
    elif delta > committed + 1:
        corruption.append(
            f"phantom revlog rows: delta {delta} exceeds logged commits "
            f"{committed} by more than the single allowed in-flight answer"
        )

    if in_flight not in (0, 1):
        anomalies.append(
            f"child log accounting anomaly: attempted={attempted} "
            f"committed={committed} (in-flight must be 0 or 1)"
        )
    elif in_flight == 1 and delta == committed:
        rolled_back = 1
    elif in_flight == 1 and delta == committed + 1:
        committed_unlogged = 1
    elif in_flight == 0 and delta == committed + 1:
        anomalies.append(
            "revlog gained a row with no in-flight answer "
            f"(attempted={attempted} committed={committed} delta={delta})"
        )

    return {
        "revlog_before": revlog_before,
        "revlog_after": revlog_after,
        "revlog_delta": delta,
        "attempted": attempted,
        "committed_logged": committed,
        "rolled_back_in_flight": rolled_back,
        "committed_unlogged": committed_unlogged,
        "corruption": corruption,
        "anomalies": anomalies,
    }


def parse_integrity_output(text: str) -> tuple[bool, str]:
    """(ok, detail) from a ``pragma integrity_check``/``quick_check``
    invocation: ok iff the output is exactly the single row ``ok``."""
    detail = (text or "").strip()
    return detail == "ok", detail


#: Substrings of pylib fix_integrity output that are unconditional
#: housekeeping, not damage. pylib appends the "rebuilt and optimized"
#: notice on EVERY successful check (collection.py fix_integrity), so its
#: presence carries no information about corruption.
HOUSEKEEPING_MARKERS = ("rebuilt and optimized",)


def classify_db_check(problems_text: str, ok: bool) -> dict[str, Any]:
    """Split the engine DB-check output into corruption-class findings vs
    housekeeping notices.

    ``ok`` comes straight from ``Collection.fix_integrity`` and is True
    only when rslib's ``check_database`` reported ZERO problems. This
    collection is built fresh by this tool, so every genuine problem
    string (missing notes, invalid properties, ...) after a SIGKILL would
    mean a torn write survived recovery - corruption-class. The only
    benign line is pylib's unconditional "rebuilt and optimized" trailer.
    """
    lines = [line.strip() for line in (problems_text or "").splitlines()]
    lines = [line for line in lines if line]
    housekeeping = [
        line
        for line in lines
        if any(marker in line.lower() for marker in HOUSEKEEPING_MARKERS)
    ]
    findings = [line for line in lines if line not in housekeeping]
    if ok:
        # ok=True means the backend reported nothing; anything left in
        # findings would be an unexpected pylib addition - keep it visible.
        return {"ok": True, "corruption_class": findings, "housekeeping": housekeeping}
    return {"ok": False, "corruption_class": findings, "housekeeping": housekeeping}


def headline(corrupted: int, total: int) -> str:
    return f"corrupted collections: {corrupted} of {total}"


def pick_dead_endpoint() -> str:
    """A loopback URL that is guaranteed unreachable: bind an ephemeral
    port, close the listener, and point at it. Nothing external is ever
    contacted; a connect() to this port is refused by the local kernel."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Report rendering (pure)
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        "# Crash + network-off test report",
        "",
        f"Generated: {meta['generated_at']} — seed {meta['seed']}, "
        f"collection `{meta['collection']}`",
        "",
        f"Invocation: `{meta['invocation']}`",
        "",
        "## Crash section (SIGKILL mid-review)",
        "",
    ]
    crash = report.get("crash")
    if crash:
        lines += [
            f"**{crash['headline'].upper()}** — {crash['iterations_run']} "
            f"iterations, {crash['totals']['committed']} answers committed "
            f"through the real v3 scheduler, "
            f"{crash['totals']['rolled_back_in_flight']} in-flight answer(s) "
            f"rolled back (correct SQLite behaviour, not corruption), "
            f"{crash['totals']['committed_unlogged']} committed-but-unlogged "
            "(kill landed between commit and log write).",
            "",
            "| iter | kill delay s | attempted | committed | revlog delta | "
            "rolled back | integrity | quick | engine check | corrupted |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in crash["iterations"]:
            lines.append(
                f"| {row['iteration']} | {row['kill_delay_s']} | "
                f"{row['attempted']} | {row['committed_logged']} | "
                f"{row['revlog_delta']} | {row['rolled_back_in_flight']} | "
                f"{'ok' if row['integrity_check_ok'] else 'FAIL'} | "
                f"{'ok' if row['quick_check_ok'] else 'FAIL'} | "
                f"{'ok' if row['db_check']['ok'] else 'FAIL'} | "
                f"{'YES' if row['corrupted'] else 'no'} |"
            )
        lines.append("")
        if crash["corrupted_iterations"]:
            lines += [
                "**CORRUPTION FOUND** in iteration(s) "
                + ", ".join(str(i) for i in crash["corrupted_iterations"])
                + ":",
                "",
            ]
            for row in crash["iterations"]:
                for message in row["corruption"] + row["anomalies"]:
                    lines.append(f"- iteration {row['iteration']}: {message}")
            lines.append("")
    network = report.get("network_off")
    lines += ["## Network-off section (AI enabled, endpoint unreachable)", ""]
    if network:
        for check in network["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            lines.append(f"- {status}: {check['name']} — {check['detail']}")
        lines += [
            "",
            f"Dead endpoint used: `{network['dead_endpoint']}` "
            "(loopback port bound then closed; no external host is ever "
            "named in the configuration).",
            "",
        ]
    lines += ["## Honesty notes", ""]
    lines += [f"- {note}" for note in report.get("honesty", [])]
    lines.append("")
    failures = report.get("failures", [])
    if failures:
        lines += ["## FAILURES", ""]
        lines += [f"- {failure}" for failure in failures]
        lines.append("")
    return "\n".join(lines)


HONESTY_NOTES = [
    "SIGKILL of the child review process proves the ENGINE layer "
    "(rslib + SQLite, where every collection write lives) survives an "
    "abrupt process death mid-transaction. The desktop GUI wraps this "
    "same engine in-process, so its writes ride the same journal - but "
    "killing the full Qt app additionally exercises window/session "
    "teardown, which this script does not simulate. Recommended for the "
    "demo video: a manual whole-app `kill -9` spot check while the "
    "reviewer is open.",
    "The child grades with Again/Hard only, so cards recycle intraday "
    "forever and the queue never exhausts across 20 iterations. Every "
    "grade is the same transaction shape (card update + revlog append); "
    "Good/Easy differ only in the state written, not the crash surface.",
    "Committed answers are counted from the child's flushed COMMIT log "
    "lines; SQLite durability means committed-implies-survives. The one "
    "in-flight answer may roll back - that is documented SQLite "
    "behaviour and is counted separately, never as corruption.",
    "Opening a killed collection performs SQLite's standard WAL/journal "
    "recovery; the integrity checks then run on the recovered database. "
    "That recovery is the crash-safety mechanism under test, not a "
    "workaround.",
    "The raw integrity/quick checks run through Python's stdlib sqlite3 "
    "(independent of all Anki code), NOT the sqlite3 CLI: Anki's schema "
    "declares the custom 'unicase' collation that only rslib registers, "
    "so the plain CLI cannot even prepare the pragma. The checker "
    "registers a byte-compatible casefold stand-in; a collation "
    "divergence could only produce a FALSE index-order complaint, never "
    "mask real damage.",
    "The engine DB check ('fix_integrity') reports fixed problems; the "
    "unconditional 'rebuilt and optimized' trailer pylib appends is "
    "housekeeping. For a collection this tool built itself, any OTHER "
    "line (missing notes, invalid properties, ...) is treated as "
    "corruption-class and fails the run - nothing is softened.",
    "Network-off proof scope: the assistant backend configuration names "
    "ONLY the dead loopback endpoint and the call returned the abstain "
    "fallback; the gauges are local Rust computations with no network "
    "API. The wire was not sniffed - what is proven is that the code "
    "path has no other endpoint to talk to and degrades exactly as "
    "designed.",
    "Readiness ABSTAINING on this scratch collection is its designed "
    "output (no delayed held-out probe outcomes exist here): the Memory "
    "gauge returns computed numbers and Readiness returns its abstention "
    "with named reasons. 'Still returns a score' means exactly that "
    "pair, stated precisely.",
    "Android is NOT exercised by this script: android_crash_test.sh "
    "ships the equivalent device test and fails fast when no device is "
    "attached.",
]


# ---------------------------------------------------------------------------
# Child mode (real scheduler loop; pylib imported lazily)
# ---------------------------------------------------------------------------


def run_child(
    collection_path: str, log_path: str, seed: int, max_seconds: float
) -> int:
    """Open the collection and answer due cards in a tight loop through
    the real v3 scheduler until killed (normal case), the queue stays
    empty for CHILD_EMPTY_GIVE_UP_S, or max_seconds elapses."""
    # anki.collection must load before anki.cards: importing cards first
    # trips the hooks_gen circular import in pylib.
    import anki.collection
    from anki.cards import Card
    from anki.scheduler.v3 import CardAnswer
    from anki.scheduler.v3 import Scheduler as V3Scheduler

    Collection = anki.collection.Collection

    rng = random.Random(seed)
    ratings = (CardAnswer.AGAIN, CardAnswer.HARD)
    weights = (0.6, 0.4)

    col = Collection(collection_path)
    assert isinstance(col.sched, V3Scheduler)  # narrows the sched union
    sequence = 0
    started = time.monotonic()
    empty_since: float | None = None
    with open(log_path, "a", encoding="utf-8") as log:
        while time.monotonic() - started < max_seconds:
            queued = col.sched.get_queued_cards(fetch_limit=1)
            if not queued.cards:
                now = time.monotonic()
                if empty_since is None:
                    empty_since = now
                elif now - empty_since > CHILD_EMPTY_GIVE_UP_S:
                    log.write("EXIT queue_empty\n")
                    log.flush()
                    break
                time.sleep(0.02)
                continue
            empty_since = None
            entry = queued.cards[0]
            card = Card(col)
            card._load_from_backend_card(entry.card)
            card.start_timer()
            rating = rng.choices(ratings, weights)[0]
            answer = col.sched.build_answer(
                card=card, states=entry.states, rating=rating
            )
            sequence += 1
            log.write(f"ATTEMPT {sequence} {card.id}\n")
            log.flush()
            col.sched.answer_card(answer)
            log.write(f"COMMIT {sequence} {card.id}\n")
            log.flush()
    col.close()
    return 0


# ---------------------------------------------------------------------------
# Scratch collection (cached, seeded; pylib imported lazily)
# ---------------------------------------------------------------------------

TOPIC_TAGS = (
    "cfa::topic::ethics",
    "cfa::topic::quantitative_methods",
    "cfa::topic::economics",
    "cfa::topic::fixed_income",
    "cfa::topic::equity_investments",
)


def ensure_scratch_collection(
    scratch_dir: Path, cards: int, seed: int, rebuild: bool
) -> tuple[Path, bool]:
    """Create (or reuse) the seeded scratch collection. Returns
    (collection_path, reused)."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    collection_path = scratch_dir / COLLECTION_NAME
    marker_path = scratch_dir / SEED_MARKER_NAME
    marker = {"cards": cards, "seed": seed, "version": 1}
    if (
        not rebuild
        and collection_path.exists()
        and marker_path.exists()
        and json.loads(marker_path.read_text(encoding="utf-8")) == marker
    ):
        return collection_path, True

    from anki.collection import Collection

    for stale in scratch_dir.glob(COLLECTION_NAME + "*"):
        stale.unlink()
    col = Collection(str(collection_path))
    try:
        deck_id = col.decks.id(DECK_NAME)
        conf = col.decks.add_config(PRESET_NAME)
        conf["new"]["perDay"] = 100_000
        conf["rev"]["perDay"] = 100_000
        col.decks.update_config(conf)
        deck = col.decks.get(deck_id)
        deck["conf"] = conf["id"]
        col.decks.save(deck)
        notetype = col.models.by_name("Basic")
        rng = random.Random(seed)
        for index in range(cards):
            note = col.new_note(notetype)
            note["Front"] = f"Crash-test question {index}"
            note["Back"] = f"Crash-test answer {index}"
            note.tags = [rng.choice(TOPIC_TAGS)]
            col.add_note(note, deck_id)
        col.decks.select(deck_id)
        # FSRS on so answered cards carry memory states and the Memory
        # gauge has real numbers for the network-off section.
        col.set_config("fsrs", True)
    finally:
        col.close()
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    return collection_path, False


# ---------------------------------------------------------------------------
# Parent-side checks
# ---------------------------------------------------------------------------


def _unicase_collation(a: str, b: str) -> int:
    """Stand-in for rslib's ``unicase`` collation (storage/sqlite.rs
    compares ``UniCase``-folded strings). Python's casefold is the same
    full Unicode case folding; for the ASCII content this tool seeds the
    orderings are identical. A divergence could only produce a FALSE
    corruption report (an index-order complaint), never mask real
    damage."""
    fa, fb = a.casefold(), b.casefold()
    return (fa > fb) - (fa < fb)


def _open_raw(collection_path: Path) -> sqlite3.Connection:
    """Open the collection with the stdlib sqlite3 module - independent
    of all Anki code. The open itself performs SQLite's standard
    WAL/journal crash recovery, which is the mechanism under test. The
    schema references the custom ``unicase`` collation, so a
    byte-compatible stand-in is registered (the plain sqlite3 CLI cannot
    even prepare these pragmas)."""
    con = sqlite3.connect(str(collection_path), timeout=60)
    con.create_collation("unicase", _unicase_collation)
    return con


def sqlite_pragma(collection_path: Path, pragma: str) -> tuple[bool, str]:
    con = _open_raw(collection_path)
    try:
        rows = con.execute(f"pragma {pragma}").fetchall()
    except sqlite3.Error as exc:
        return False, f"pragma {pragma} failed: {exc}"
    finally:
        con.close()
    return parse_integrity_output("\n".join(str(row[0]) for row in rows))


def sqlite_revlog_count(collection_path: Path) -> int:
    con = _open_raw(collection_path)
    try:
        return int(con.execute("select count(*) from revlog").fetchone()[0])
    finally:
        con.close()


def engine_check_and_count(collection_path: Path) -> tuple[dict[str, Any], int]:
    """Reopen with pylib, run the engine's own DB check, count revlog."""
    from anki.collection import Collection

    col = Collection(str(collection_path))
    try:
        count_before_fix = col.db.scalar("select count() from revlog")
        problems, ok = col.fix_integrity()
        count_after_fix = col.db.scalar("select count() from revlog")
    finally:
        col.close()
    verdict = classify_db_check(problems, ok)
    if count_after_fix != count_before_fix:
        verdict["corruption_class"].append(
            f"engine check CHANGED the revlog count: {count_before_fix} -> "
            f"{count_after_fix}"
        )
        verdict["ok"] = False
    return verdict, count_after_fix


def run_crash_section(args: argparse.Namespace) -> dict[str, Any]:
    scratch_dir = Path(args.scratch_dir)
    collection_path, reused = ensure_scratch_collection(
        scratch_dir, args.cards, args.seed, args.rebuild
    )
    log_path = scratch_dir / CHILD_LOG_NAME
    rng = random.Random(args.seed)
    revlog_before = sqlite_revlog_count(collection_path)

    iterations: list[dict[str, Any]] = []
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONPATH", str(DESKTOP_DIR / "out" / "pylib"))

    for iteration in range(1, args.iterations + 1):
        kill_delay = round(rng.uniform(*KILL_DELAY_RANGE_S), 3)
        log_path.write_text("", encoding="utf-8")
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--collection",
                str(collection_path),
                "--child-log",
                str(log_path),
                "--seed",
                str(args.seed + iteration),
                "--max-child-seconds",
                str(CHILD_MAX_SECONDS),
            ],
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Bias the kill window to be genuinely mid-review: wait for the
        # first committed answer, then start the seeded 0.2-1.5 s timer.
        first_commit_deadline = time.monotonic() + FIRST_COMMIT_TIMEOUT_S
        saw_commit = False
        while time.monotonic() < first_commit_deadline:
            if child.poll() is not None:
                break
            if "COMMIT " in log_path.read_text(encoding="utf-8"):
                saw_commit = True
                break
            time.sleep(0.01)
        killed = False
        exited_early = child.poll() is not None
        if not exited_early:
            time.sleep(kill_delay)
            if child.poll() is None:
                os.kill(child.pid, signal.SIGKILL)
                killed = True
        child.wait(timeout=30)
        stderr_tail = ""
        if child.stderr is not None:
            stderr_tail = child.stderr.read()[-500:]
            child.stderr.close()

        wal_path = collection_path.with_name(collection_path.name + "-wal")
        wal_present = wal_path.exists() and wal_path.stat().st_size > 0

        integrity_ok, integrity_detail = sqlite_pragma(
            collection_path, "integrity_check"
        )
        quick_ok, quick_detail = sqlite_pragma(collection_path, "quick_check")
        db_check, revlog_after = engine_check_and_count(collection_path)

        counts = parse_child_log(log_path.read_text(encoding="utf-8"))
        accounting = classify_iteration(
            revlog_before, revlog_after, counts["attempted"], counts["committed"]
        )
        row: dict[str, Any] = {
            "iteration": iteration,
            "kill_delay_s": kill_delay,
            "killed": killed,
            "saw_commit_before_kill": saw_commit,
            "child_exited_before_kill": exited_early,
            "queue_empty_exit": bool(counts["queue_empty_exit"]),
            "wal_present_after_kill": wal_present,
            "integrity_check_ok": integrity_ok,
            "quick_check_ok": quick_ok,
            "db_check": db_check,
            **accounting,
        }
        if not integrity_ok:
            row["corruption"].append(f"integrity_check: {integrity_detail[:300]}")
        if not quick_ok:
            row["corruption"].append(f"quick_check: {quick_detail[:300]}")
        if db_check["corruption_class"]:
            row["corruption"].extend(
                f"engine check: {line}" for line in db_check["corruption_class"]
            )
        if exited_early and not killed:
            row["anomalies"].append(
                "child exited before the kill "
                f"(queue_empty_exit={bool(counts['queue_empty_exit'])}, "
                f"stderr tail: {stderr_tail.strip()[:200]!r}) - iteration "
                "did not test a mid-review kill"
            )
        row["corrupted"] = bool(row["corruption"])
        iterations.append(row)
        revlog_before = revlog_after
        print(
            f"iteration {iteration:2d}: kill@{kill_delay:.3f}s "
            f"committed={row['committed_logged']} delta={row['revlog_delta']} "
            f"rolled_back={row['rolled_back_in_flight']} "
            f"integrity={'ok' if integrity_ok else 'FAIL'} "
            f"quick={'ok' if quick_ok else 'FAIL'} "
            f"engine={'ok' if db_check['ok'] else 'FAIL'}"
        )

    corrupted = [row["iteration"] for row in iterations if row["corrupted"]]
    return {
        "collection": str(collection_path),
        "collection_reused_from_cache": reused,
        "iterations_run": len(iterations),
        "iterations": iterations,
        "corrupted_iterations": corrupted,
        "corrupted_collections": len(corrupted),
        "headline": headline(len(corrupted), len(iterations)),
        "totals": {
            "committed": sum(row["committed_logged"] for row in iterations),
            "rolled_back_in_flight": sum(
                row["rolled_back_in_flight"] for row in iterations
            ),
            "committed_unlogged": sum(row["committed_unlogged"] for row in iterations),
            "anomalies": sum(len(row["anomalies"]) for row in iterations),
        },
    }


# ---------------------------------------------------------------------------
# Network-off section
# ---------------------------------------------------------------------------


def _mastery_fingerprint(mastery: Any) -> dict[str, Any]:
    return {
        "graded_reviews": int(mastery.graded_reviews),
        "total_cards": int(mastery.total_cards),
        "fsrs_enabled": bool(mastery.fsrs_enabled),
        "topics": [
            {
                "topic": row.topic,
                "total": int(row.total_cards),
                "studied": int(row.studied_cards),
                "high_recall": int(row.high_recall_cards),
                "avg_retrievability": round(float(row.average_retrievability), 6),
            }
            for row in mastery.topics
        ],
    }


def _readiness_fingerprint(readiness: Any) -> dict[str, Any]:
    return {
        "kind": int(readiness.kind),
        "p_pass_low": round(float(readiness.p_pass_low), 6),
        "p_pass_high": round(float(readiness.p_pass_high), 6),
        "missing": list(readiness.missing),
    }


def run_network_off_section(collection_path: Path) -> dict[str, Any]:
    """AI enabled + unreachable endpoint -> graceful abstention; gauges
    still return; parity with AI fully off."""
    from assistant import coach, core

    from anki.collection import Collection

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    dead_endpoint = pick_dead_endpoint()
    col = Collection(str(collection_path))
    saved_env = {
        key: os.environ.get(key) for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY")
    }
    try:
        # -- baseline: AI fully OFF (fresh default flags) --
        for key in AI_FLAG_KEYS:
            col.remove_config(key)
        col.remove_config(AI_BACKEND_KEY)
        flags_off = {key: col.get_config(key, default=False) for key in AI_FLAG_KEYS}
        check(
            "AI flags default to OFF",
            not any(flags_off.values()),
            f"flags={flags_off}",
        )
        mastery_off = _mastery_fingerprint(col.topic_mastery())
        readiness_off = _readiness_fingerprint(col.get_readiness())

        # -- enable AI, point the backend at the dead endpoint --
        for key in AI_FLAG_KEYS:
            col.set_config(key, True)
        col.set_config(AI_BACKEND_KEY, "openai-compatible")
        readback = all(col.get_config(key, default=False) for key in AI_FLAG_KEYS)
        check(
            "AI flags enabled on collection config (read back)",
            readback
            and col.get_config(AI_BACKEND_KEY, default="") == "openai-compatible",
            f"{AI_BACKEND_KEY}={col.get_config(AI_BACKEND_KEY, default='')!r}",
        )
        os.environ["OPENAI_BASE_URL"] = dead_endpoint
        os.environ["OPENAI_API_KEY"] = "dead-key-never-sent-anywhere"
        backend = core.make_backend("openai-compatible", timeout=5)
        check(
            "backend config names ONLY the dead loopback endpoint",
            getattr(backend, "base_url", "") == dead_endpoint,
            f"base_url={getattr(backend, 'base_url', '')!r}",
        )

        # -- (a) assistant call fails GRACEFULLY into abstention --
        facts = {
            "exam": "CFA Level I",
            "days_to_exam": 30,
            "subjects": [
                {
                    "name": row["topic"],
                    "memory": row["avg_retrievability"],
                    "weighted_gap": 0.0,
                }
                for row in mastery_off["topics"][:3]
            ],
            "readiness": {"kind": "abstain", "missing": readiness_off["missing"]},
            "best_next": (
                mastery_off["topics"][0]["topic"] if mastery_off["topics"] else "ethics"
            ),
        }
        diagnostics: dict[str, Any] = {}
        escaped: str | None = None
        started = time.monotonic()
        try:
            plan = coach.coach_plan(facts, backend, diagnostics=diagnostics)
        except Exception as exc:
            plan = None
            escaped = repr(exc)
        elapsed = round(time.monotonic() - started, 3)
        check(
            "coach call abstains gracefully (no exception escapes)",
            escaped is None
            and plan is None
            and diagnostics.get("outcome") == "abstained",
            f"reason={diagnostics.get('reason')!r} in {elapsed}s"
            + (f"; ESCAPED: {escaped}" if escaped else ""),
        )
        diagnostics_generic: dict[str, Any] = {}
        reply = core.grounded_complete(
            "You are a test narrator.",
            facts,
            schema={"text": "str"},
            backend=backend,
            task="generic",
            timeout=5,
            diagnostics=diagnostics_generic,
        )
        check(
            "grounded_complete abstains on the dead endpoint",
            reply is None and diagnostics_generic.get("outcome") == "abstained",
            f"reason={diagnostics_generic.get('reason')!r}",
        )
        # The offline mock is the shipped default backend: show the
        # deterministic fallback path still renders a grounded reply.
        mock_diag: dict[str, Any] = {}
        mock_plan = coach.coach_plan(
            facts, core.make_backend("mock"), diagnostics=mock_diag
        )
        check(
            "offline mock fallback renders a deterministic plan",
            mock_plan is not None and bool(mock_plan.get("summary")),
            f"outcome={mock_diag.get('outcome')!r}",
        )

        # -- (b) gauges still return with AI enabled-but-unreachable --
        mastery_on = _mastery_fingerprint(col.topic_mastery())
        readiness_on = _readiness_fingerprint(col.get_readiness())
        check(
            "Memory gauge computes (graded reviews + topic rows)",
            mastery_on["graded_reviews"] > 0 and len(mastery_on["topics"]) > 0,
            f"graded_reviews={mastery_on['graded_reviews']}, "
            f"topics={len(mastery_on['topics'])}, first="
            f"{mastery_on['topics'][0] if mastery_on['topics'] else None}",
        )
        check(
            "Readiness returns its designed abstention (kind=ABSTAIN, reasons named)",
            readiness_on["kind"] == 0 and len(readiness_on["missing"]) > 0,
            f"kind={readiness_on['kind']}, missing={readiness_on['missing'][:2]}",
        )
        # -- (c) parity: identical gauge output with AI off vs on --
        check(
            "gauge parity: AI off vs AI on-but-unreachable outputs identical",
            mastery_on == mastery_off and readiness_on == readiness_off,
            "topic_mastery and get_readiness fingerprints match exactly",
        )
        return {
            "dead_endpoint": dead_endpoint,
            "checks": checks,
            "passed": all(check["passed"] for check in checks),
            "gauges_with_ai_enabled_unreachable": {
                "topic_mastery": mastery_on,
                "readiness": readiness_on,
            },
        }
    finally:
        for key in AI_FLAG_KEYS:
            col.remove_config(key)
        col.remove_config(AI_BACKEND_KEY)
        col.close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="kill iterations (default: %(default)s)",
    )
    parser.add_argument(
        "--cards",
        type=int,
        default=DEFAULT_CARDS,
        help="cards in the scratch collection (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="RNG seed for kill delays and gradings (default: %(default)s)",
    )
    parser.add_argument(
        "--scratch-dir",
        default=str(DEFAULT_SCRATCH_DIR),
        help="scratch directory (default: %(default)s)",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="where to write crash_test_report.{json,md} (default: %(default)s)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild the scratch collection even if the cached one matches",
    )
    parser.add_argument(
        "--skip-network-off",
        action="store_true",
        help="skip the network-off AI section (it runs by default)",
    )
    parser.add_argument(
        "--skip-crash",
        action="store_true",
        help="skip the 20-kill crash section (network-off only)",
    )
    # internal child mode
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--collection", help=argparse.SUPPRESS)
    parser.add_argument("--child-log", help=argparse.SUPPRESS)
    parser.add_argument(
        "--max-child-seconds",
        type=float,
        default=CHILD_MAX_SECONDS,
        help=argparse.SUPPRESS,
    )
    return parser


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "crash_test_report.json"
    md_path = report_dir / "crash_test_report.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.child:
        if not (args.collection and args.child_log):
            print("--child requires --collection and --child-log", file=sys.stderr)
            return 2
        return run_child(
            args.collection, args.child_log, args.seed, args.max_child_seconds
        )

    failures: list[str] = []
    report: dict[str, Any] = {
        "meta": {
            "tool": "crash_test",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "seed": args.seed,
            "iterations": args.iterations,
            "cards": args.cards,
            "collection": str(Path(args.scratch_dir) / COLLECTION_NAME),
            "python": sys.version.split()[0],
            "invocation": (
                "PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/crash_test.py"
            ),
        },
        "honesty": HONESTY_NOTES,
    }

    if not args.skip_crash:
        crash = run_crash_section(args)
        report["crash"] = crash
        print(crash["headline"])
        if crash["corrupted_collections"]:
            failures.append(
                f"CORRUPTION: {crash['headline']} "
                f"(iterations {crash['corrupted_iterations']})"
            )
        if crash["totals"]["anomalies"]:
            failures.append(
                f"{crash['totals']['anomalies']} accounting anomal(ies) - "
                "see iterations table"
            )

    if not args.skip_network_off:
        if args.skip_crash:
            # network-off needs the scratch collection to exist
            collection_path, _ = ensure_scratch_collection(
                Path(args.scratch_dir), args.cards, args.seed, args.rebuild
            )
        else:
            collection_path = Path(report["crash"]["collection"])
        network = run_network_off_section(collection_path)
        report["network_off"] = network
        for check in network["checks"]:
            print(
                f"network-off {'PASS' if check['passed'] else 'FAIL'}: {check['name']}"
            )
        if not network["passed"]:
            failed = [c["name"] for c in network["checks"] if not c["passed"]]
            failures.append(f"network-off checks failed: {failed}")

    report["failures"] = failures
    report["exit_code"] = 1 if failures else 0
    json_path, md_path = write_reports(report, Path(args.report_dir))
    print(f"reports: {json_path} + {md_path.name}")
    if failures:
        print("FAILURES: " + "; ".join(failures), file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
