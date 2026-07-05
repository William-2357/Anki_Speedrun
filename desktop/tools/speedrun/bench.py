# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The one-command §10 performance benchmark (Anki Speedrun, challenge 7h).

One run does everything: deterministically build (or reuse) a 50,000-card
collection, measure every §10 action against the real engine, and write
eval/bench_report.{json,md} with a p50 / p95 / worst vs target table.

    cd desktop && PYTHONPATH=out/pylib out/pyenv/bin/python \
        tools/speedrun/bench.py            # or simply: just bench

What is measured (all headless, engine data path — see honesty rules):

* button_press_ack        answer_card() RPC round-trip alone
* next_card_after_grade   answer_card() + get_queued_cards() back-to-back
* dashboard_first_load    fresh Collection open + first topic_mastery()
                          + get_readiness() (close+reopen per sample)
* dashboard_refresh       warm repeated topic_mastery() + get_readiness()
* session_sync            answer ~20 cards, then sync_collection() against
                          the bundled local sync server (full upload first,
                          uncounted — it is one-time)
* cold_start              fresh child interpreter: import pylib, open the
                          50k collection, first get_queued_cards(); the
                          parent also records the child's full wall time
* peak_memory             child process ru_maxrss after dashboard queries
                          + queue build + a few answers

Honesty rules (repeated in the report):

* Every number comes from a call actually made in this run; nothing is
  fabricated, cached numbers are never reused across runs.
* These are engine data-path times. UI paint adds client-side time on
  top; the real app awaits these calls asynchronously (the dashboard
  page calls topicMastery/getReadiness through the @generated/backend
  POST bridge), so the UI thread is not blocked while they run.
* "Nothing freezes the screen > 100 ms" can only be proxied headlessly;
  the proxy and its limits are disclosed, not claimed as paint timing.
* Phone-side §10 timings require an instrumented device and are NOT
  measured here (and not invented).
* A §10 target miss is a RESULT, printed loudly, exit code still 0.
  Non-zero exit is reserved for real errors (build/sync/child failure).

The deck build is cached at out/speedrun_eval/bench/bench50k.anki2 and
reused unless --rebuild (or a spec change) forces a rebuild. Measurements
always run against a fresh working COPY, so the cache stays pristine.

stdlib only; pylib is imported lazily inside functions so the unit tests
(tools/speedrun/tests/test_bench.py) run under plain python3.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import math
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DESKTOP_DIR = HERE.parents[1]

# ---------------------------------------------------------------------------
# Deck spec (pure functions of the card index -> fully deterministic layout)
# ---------------------------------------------------------------------------

BENCH_SEED = 20260704  # recorded in the meta file; layout is index-pure
BUILD_SCHEMA = 1  # bump to invalidate cached decks after a spec change
DEFAULT_CARD_COUNT = 50_000
DECK_NAME = "CFA Level I"

#: The fork's taxonomy (identical slugs to probe_harness.TOPICS).
TOPICS = (
    "ethics",
    "quantitative_methods",
    "economics",
    "financial_statement_analysis",
    "corporate_issuers",
    "equity_investments",
    "fixed_income",
    "derivatives",
    "alternative_investments",
    "portfolio_management",
)
TOPIC_TAG_PREFIX = "cfa::topic::"
CLUSTER_TAG_PREFIX = "cluster::"
FAMILIES_PER_TOPIC = 40  # 400 clusters over 10 topics

#: Cards graded through the REAL v3 scheduler at build time (revlog +
#: FSRS memory states), and new cards spread as reviews over coming weeks.
GRADED_AT_BUILD = 1_000
DUE_SPREAD_CARDS = 14_000
DUE_SPREAD_DAYS = 28  # chunk c -> due day c % 28 (deterministic)
DUE_SPREAD_CHUNK = 500
NEW_PER_DAY = 2_000  # raised so queues never empty mid-benchmark
REV_PER_DAY = 9_999

#: Fixed grading mix: 10% Again, 15% Hard, 65% Good, 10% Easy
#: (CardAnswer.Rating values: AGAIN=0 HARD=1 GOOD=2 EASY=3).
RATING_PATTERN = (2, 2, 0, 2, 1, 2, 3, 2, 2, 2, 1, 2, 2, 3, 2, 0, 2, 1, 2, 2)

ADD_BATCH = 1_000


def topic_for_index(index: int) -> str:
    """Card index -> blueprint topic (round-robin; all 10 covered)."""
    return TOPICS[index % len(TOPICS)]


def family_for_index(index: int) -> int:
    """Card index -> cluster family number within its topic."""
    return (index // len(TOPICS)) % FAMILIES_PER_TOPIC


def cluster_for_index(index: int) -> str:
    """Cluster tag suffix, `<topic>::f<NN>` (builder prepends cluster::)."""
    return f"{topic_for_index(index)}::f{family_for_index(index):02d}"


def tags_for_index(index: int) -> list[str]:
    """The two taxonomy tags the dashboard aggregation keys off."""
    return [
        f"{TOPIC_TAG_PREFIX}{topic_for_index(index)}",
        f"{CLUSTER_TAG_PREFIX}{cluster_for_index(index)}",
    ]


def fields_for_index(index: int) -> tuple[str, str]:
    """Deterministic front/back text; unique per index."""
    topic = topic_for_index(index)
    front = (
        f"Q{index:05d}: which statement about {topic.replace('_', ' ')} "
        f"concept {family_for_index(index):02d}-{index % 7} is most accurate?"
    )
    back = f"A{index:05d}: benchmark answer for {cluster_for_index(index)}"
    return front, back


def rating_for_index(index: int) -> int:
    """Deterministic grading mix (CardAnswer.Rating int value)."""
    return RATING_PATTERN[index % len(RATING_PATTERN)]


def deck_spec(cards: int) -> dict[str, Any]:
    """Everything that defines the cached deck; meta mismatch => rebuild."""
    return {
        "schema": BUILD_SCHEMA,
        "seed": BENCH_SEED,
        "cards": cards,
        "graded": min(GRADED_AT_BUILD, max(cards // 5, 1)),
        "due_spread": min(DUE_SPREAD_CARDS, max(cards // 3, 1)),
        "due_spread_days": DUE_SPREAD_DAYS,
        "families_per_topic": FAMILIES_PER_TOPIC,
        "new_per_day": NEW_PER_DAY,
        "rev_per_day": REV_PER_DAY,
    }


# ---------------------------------------------------------------------------
# Percentiles + §10 targets
# ---------------------------------------------------------------------------

#: Percentile method used everywhere in this file (and asserted in tests):
#: nearest-rank on the sorted samples, rank = ceil(p/100 * n), 1-indexed.
PERCENTILE_METHOD = "nearest-rank"


def percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile; pct in (0, 100]. p100 == max."""
    if not samples:
        raise ValueError("percentile of empty sample list")
    if not 0.0 < pct <= 100.0:
        raise ValueError(f"pct must be in (0, 100], got {pct}")
    ordered = sorted(samples)
    rank = math.ceil(pct / 100.0 * len(ordered))
    return ordered[max(rank, 1) - 1]


def summarize(samples: list[float]) -> dict[str, float | int]:
    """n / min / p50 / p95 / worst / mean, all in the samples' unit (ms)."""
    return {
        "n": len(samples),
        "min": round(min(samples), 3),
        "p50": round(percentile(samples, 50), 3),
        "p95": round(percentile(samples, 95), 3),
        "worst": round(max(samples), 3),
        "mean": round(statistics.fmean(samples), 3),
    }


#: Stated memory limit for §10 "memory use on 50,000 cards": measured
#: first (94.6 MB ru_maxrss for the headless engine child on the
#: reference Apple M1 Max run), then stated with ~2.7x headroom, rounded
#: up to a power of two. The report always shows measured vs stated, so
#: the claim stays checkable; the packaged GUI's Qt/webview overhead is
#: NOT covered by this engine-process limit (disclosed in the report).
MEMORY_LIMIT_MB = 256

#: §10 targets. metric "p95" compares stats["p95"] (strictly <) against
#: target_ms; peak_memory compares measured MB against MEMORY_LIMIT_MB.
TARGETS: dict[str, dict[str, Any]] = {
    "button_press_ack": {
        "label": "Button press acknowledged (engine ack)",
        "metric": "p95",
        "target_ms": 50.0,
        "target_text": "p95 < 50 ms",
    },
    "next_card_after_grade": {
        "label": "Next card appears after grading (engine path)",
        "metric": "p95",
        "target_ms": 100.0,
        "target_text": "p95 < 100 ms",
    },
    "dashboard_first_load": {
        "label": "Dashboard first load",
        "metric": "p95",
        "target_ms": 1_000.0,
        "target_text": "p95 < 1 s",
    },
    "dashboard_refresh": {
        "label": "Dashboard refresh",
        "metric": "p95",
        "target_ms": 500.0,
        "target_text": "p95 < 500 ms",
    },
    "session_sync": {
        "label": "Sync of a normal session",
        "metric": "p95",
        "target_ms": 5_000.0,
        "target_text": "< 5 s (p95)",
    },
    "cold_start": {
        "label": "App cold start (headless engine)",
        "metric": "p95",
        "target_ms": 5_000.0,
        "target_text": "< 5 s",
    },
    "peak_memory": {
        "label": "Memory use on 50,000 cards",
        "metric": "value_mb",
        "target_ms": float(MEMORY_LIMIT_MB),
        "target_text": f"< {MEMORY_LIMIT_MB} MB (stated limit)",
    },
}


def evaluate_targets(
    action_stats: dict[str, dict[str, Any]],
    memory_mb: float,
) -> list[dict[str, Any]]:
    """One row per §10 action: measured metric vs target, strict <."""
    rows: list[dict[str, Any]] = []
    for action, target in TARGETS.items():
        row: dict[str, Any] = {
            "action": action,
            "label": target["label"],
            "target_text": target["target_text"],
            "metric": target["metric"],
        }
        if action == "peak_memory":
            row["measured"] = round(memory_mb, 1)
            row["passed"] = memory_mb < target["target_ms"]
        else:
            stats = action_stats[action]
            row["n"] = stats["n"]
            row["p50"] = stats["p50"]
            row["p95"] = stats["p95"]
            row["worst"] = stats["worst"]
            row["measured"] = stats[target["metric"]]
            row["passed"] = stats[target["metric"]] < target["target_ms"]
        rows.append(row)
    return rows


def format_table(rows: list[dict[str, Any]]) -> str:
    """Plain-text results table for stdout (same data as the md table)."""
    header = (
        f"{'action':<24} {'n':>4} {'p50 ms':>10} {'p95 ms':>10} "
        f"{'worst ms':>10}   {'target':<22} verdict"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        verdict = "PASS" if row["passed"] else "FAIL"
        if row["action"] == "peak_memory":
            lines.append(
                f"{row['action']:<24} {1:>4} "
                f"{'—':>10} {'—':>10} {row['measured']:>8.1f}MB   "
                f"{row['target_text']:<22} {verdict}"
            )
        else:
            lines.append(
                f"{row['action']:<24} {row['n']:>4} {row['p50']:>10.2f} "
                f"{row['p95']:>10.2f} {row['worst']:>10.2f}   "
                f"{row['target_text']:<22} {verdict}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Machine info (read honestly at run time)
# ---------------------------------------------------------------------------


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def machine_info() -> dict[str, Any]:
    chip = _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown"
    mem_bytes = _sysctl("hw.memsize")
    info: dict[str, Any] = {
        "chip": chip,
        "ram_gb": round(int(mem_bytes) / 2**30, 1) if mem_bytes else None,
        "cpu_count": os.cpu_count(),
        "os": f"macOS {platform.mac_ver()[0]}"
        if sys.platform == "darwin"
        else f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }
    return info


# ---------------------------------------------------------------------------
# Deck build (cached; pylib imported lazily)
# ---------------------------------------------------------------------------


def _progress(message: str) -> None:
    print(message, flush=True)


def _wipe_collection_files(path: Path) -> None:
    """Remove a collection and its sidecar files (media dir/db, wal/shm)."""
    stem = path.name.removesuffix(".anki2")
    for sibling in path.parent.glob(f"{stem}*"):
        if sibling.is_dir():
            shutil.rmtree(sibling)
        else:
            sibling.unlink()


def _answer_queued_card(col: Any, rating: int) -> bool:
    """Grade the next queued card through the real v3 path; False = empty."""
    from anki.cards import Card

    queued = col.sched.get_queued_cards(fetch_limit=1)
    if not queued.cards:
        return False
    item = queued.cards[0]
    card = Card(col)
    card._load_from_backend_card(item.card)
    card.start_timer()
    answer = col.sched.build_answer(card=card, states=item.states, rating=rating)
    col.sched.answer_card(answer)
    return True


def build_deck(
    collection_path: Path, spec: dict[str, Any], rebuild: bool
) -> dict[str, Any]:
    """Build (or reuse) the benchmark deck; returns the meta record."""
    meta_path = collection_path.with_suffix(".meta.json")
    if not rebuild and collection_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = None
        if meta and meta.get("spec") == spec:
            _progress(
                f"deck: reusing cached {collection_path.name} "
                f"({spec['cards']} cards; --rebuild to force)"
            )
            meta["cached"] = True
            return meta

    from anki.collection import AddNoteRequest, Collection

    _progress(
        f"deck: building {spec['cards']} cards at {collection_path} "
        "(this can take minutes)"
    )
    build_start = time.perf_counter()
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    _wipe_collection_files(collection_path)

    col = Collection(str(collection_path))
    try:
        # FSRS on BEFORE any grading so answer_card writes memory states.
        col.set_config("fsrs", True)
        if col.get_config("fsrs") is not True:
            raise RuntimeError("failed to enable FSRS in the bench deck")

        deck_id = col.decks.id(DECK_NAME)
        assert deck_id is not None
        col.decks.set_current(deck_id)
        conf = col.decks.config_dict_for_deck_id(deck_id)
        conf["new"]["perDay"] = spec["new_per_day"]
        conf["rev"]["perDay"] = spec["rev_per_day"]
        col.decks.update_config(conf)

        notetype = col.models.by_name("Basic")
        if notetype is None:
            raise RuntimeError("stock Basic notetype not found")

        added = 0
        while added < spec["cards"]:
            batch_size = min(ADD_BATCH, spec["cards"] - added)
            requests = []
            for offset in range(batch_size):
                index = added + offset
                note = col.new_note(notetype)
                front, back = fields_for_index(index)
                note["Front"] = front
                note["Back"] = back
                note.tags = tags_for_index(index)
                requests.append(AddNoteRequest(note=note, deck_id=deck_id))
            col.add_notes(requests)
            added += batch_size
            if added % 5_000 == 0 or added == spec["cards"]:
                _progress(
                    f"deck: notes {added}/{spec['cards']} "
                    f"({time.perf_counter() - build_start:.1f}s elapsed)"
                )

        graded = 0
        for index in range(spec["graded"]):
            if not _answer_queued_card(col, rating_for_index(index)):
                break
            graded += 1
            if graded % 250 == 0:
                _progress(f"deck: graded {graded}/{spec['graded']} via v3 scheduler")
        if graded != spec["graded"]:
            raise RuntimeError(
                f"expected to grade {spec['graded']} cards, queue ran dry at {graded}"
            )

        new_card_ids = col.db.list(
            "select id from cards where queue = 0 order by id limit ?",
            spec["due_spread"],
        )
        for chunk_index in range(0, len(new_card_ids), DUE_SPREAD_CHUNK):
            chunk = new_card_ids[chunk_index : chunk_index + DUE_SPREAD_CHUNK]
            day = (chunk_index // DUE_SPREAD_CHUNK) % spec["due_spread_days"]
            col.sched.set_due_date(chunk, str(day))
        _progress(
            f"deck: spread {len(new_card_ids)} cards over days "
            f"0..{spec['due_spread_days'] - 1}"
        )

        card_count = col.db.scalar("select count() from cards")
        revlog_count = col.db.scalar("select count() from revlog")
    finally:
        col.close()

    build_wall_s = round(time.perf_counter() - build_start, 1)
    meta = {
        "spec": spec,
        "cached": False,
        "card_count": card_count,
        "revlog_count": revlog_count,
        "graded": graded,
        "build_wall_s": build_wall_s,
        "built_at": _now_iso(),
    }
    meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    _progress(f"deck: built {card_count} cards in {build_wall_s}s")
    return meta


def make_working_copy(cached: Path, work: Path) -> None:
    """Fresh mutable copy per run; the cached build stays pristine."""
    _wipe_collection_files(work)
    shutil.copyfile(cached, work)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(cached) + suffix)
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(str(work) + suffix))


# ---------------------------------------------------------------------------
# Measurements (parent process)
# ---------------------------------------------------------------------------

N_REVIEW_SAMPLES = 60
REVIEW_WARMUP = 3
N_REFRESH_SAMPLES = 40
REFRESH_WARMUP = 2
N_FIRST_LOAD_SAMPLES = 10
N_SYNC_SAMPLES = 5
SYNC_SESSION_CARDS = 20
N_COLD_START_SAMPLES = 5

SYNC_PORT = 28701
SYNC_USER = "bench"
SYNC_PASS = "bench"


def _ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1e6


def _pylib_env() -> dict[str, str]:
    """Child env replicating the documented PYTHONPATH=out/pylib run mode.

    `anki` is a namespace package spanning out/pylib/anki (generated) and
    pylib/anki (source), so put every portion's parent on PYTHONPATH.
    """
    import anki

    parents: list[str] = []
    for portion in anki.__path__:
        parent = str(Path(portion).resolve().parent)
        if parent not in parents:
            parents.append(parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(parents)
    return env


def measure_review_loop(col: Any) -> dict[str, Any]:
    """button_press_ack + next_card_after_grade from the same grade events."""
    from anki.cards import Card

    ack: list[float] = []
    nxt: list[float] = []
    for index in range(REVIEW_WARMUP + N_REVIEW_SAMPLES):
        queued = col.sched.get_queued_cards(fetch_limit=1)
        if not queued.cards:
            raise RuntimeError("review queue exhausted during measurement")
        item = queued.cards[0]
        card = Card(col)
        card._load_from_backend_card(item.card)
        card.start_timer()
        answer = col.sched.build_answer(
            card=card, states=item.states, rating=rating_for_index(index)
        )
        t0 = time.perf_counter_ns()
        col.sched.answer_card(answer)
        t1 = time.perf_counter_ns()
        follow = col.sched.get_queued_cards(fetch_limit=1)
        t2 = time.perf_counter_ns()
        if not follow.cards:
            raise RuntimeError("no next card returned after grading")
        if index >= REVIEW_WARMUP:
            ack.append(_ms(t0, t1))
            nxt.append(_ms(t0, t2))
    return {
        "button_press_ack": {"samples_ms": ack, "stats": summarize(ack)},
        "next_card_after_grade": {"samples_ms": nxt, "stats": summarize(nxt)},
        "warmup_discarded": REVIEW_WARMUP,
    }


def measure_dashboard_first_load(collection_path: Path) -> dict[str, Any]:
    """Cold per sample: fresh backend + collection open + both queries."""
    from anki.collection import Collection

    samples: list[float] = []
    segments: list[dict[str, float]] = []
    for _ in range(N_FIRST_LOAD_SAMPLES):
        t0 = time.perf_counter_ns()
        col = Collection(str(collection_path))
        t1 = time.perf_counter_ns()
        col.topic_mastery()
        t2 = time.perf_counter_ns()
        col.get_readiness()
        t3 = time.perf_counter_ns()
        col.close()
        del col
        samples.append(_ms(t0, t3))
        segments.append(
            {
                "open_ms": round(_ms(t0, t1), 3),
                "topic_mastery_ms": round(_ms(t1, t2), 3),
                "get_readiness_ms": round(_ms(t2, t3), 3),
            }
        )
    return {"samples_ms": samples, "stats": summarize(samples), "segments": segments}


def measure_dashboard_refresh(col: Any) -> dict[str, Any]:
    """Warm repeats of the dashboard data path on an open collection."""
    samples: list[float] = []
    for index in range(REFRESH_WARMUP + N_REFRESH_SAMPLES):
        t0 = time.perf_counter_ns()
        col.topic_mastery()
        col.get_readiness()
        t1 = time.perf_counter_ns()
        if index >= REFRESH_WARMUP:
            samples.append(_ms(t0, t1))
    return {
        "samples_ms": samples,
        "stats": summarize(samples),
        "warmup_discarded": REFRESH_WARMUP,
    }


def _wait_for_port(port: int, proc: subprocess.Popen, deadline_s: float = 30.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if proc.poll() is not None:
            raise RuntimeError(f"sync server exited early with code {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"sync server did not open port {port} in {deadline_s}s")


def measure_session_sync(collection_path: Path, scratch: Path) -> dict[str, Any]:
    """Initial full upload (uncounted), then N normal-session syncs."""
    from anki.collection import Collection
    from anki.sync_pb2 import SyncAuth

    sync_base = scratch / "syncbase"
    if sync_base.exists():
        shutil.rmtree(sync_base)
    sync_base.mkdir(parents=True)
    log_path = scratch / "syncserver.log"
    endpoint = f"http://127.0.0.1:{SYNC_PORT}/"

    env = _pylib_env()
    env.update(
        {
            "SYNC_BASE": str(sync_base),
            "SYNC_HOST": "127.0.0.1",
            "SYNC_PORT": str(SYNC_PORT),
            "SYNC_USER1": f"{SYNC_USER}:{SYNC_PASS}",
            "RUST_LOG": "error",
        }
    )
    log_handle = open(log_path, "w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "anki.syncserver"],
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=str(DESKTOP_DIR),
    )
    col = None
    try:
        _wait_for_port(SYNC_PORT, server)
        col = Collection(str(collection_path))
        raw_auth = col.sync_login(SYNC_USER, SYNC_PASS, endpoint)
        auth = SyncAuth(hkey=raw_auth.hkey, endpoint=endpoint)

        first = col.sync_collection(auth, False)
        upload_ms = None
        if first.required == first.FULL_UPLOAD:
            col.close_for_full_sync()
            t0 = time.perf_counter_ns()
            col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
            upload_ms = round(_ms(t0, time.perf_counter_ns()), 1)
            col.reopen(after_full_sync=True)
        elif first.required != first.NO_CHANGES:
            raise RuntimeError(
                f"unexpected first-sync state {first.required} against a "
                "fresh server (wipe out/speedrun_eval/bench/syncbase)"
            )

        samples: list[float] = []
        for sample_index in range(N_SYNC_SAMPLES):
            for review_index in range(SYNC_SESSION_CARDS):
                if not _answer_queued_card(col, rating_for_index(review_index)):
                    raise RuntimeError("queue exhausted while preparing sync session")
            t0 = time.perf_counter_ns()
            out = col.sync_collection(auth, False)
            t1 = time.perf_counter_ns()
            if out.required != out.NO_CHANGES:
                raise RuntimeError(
                    f"sync sample {sample_index} did not complete normally: "
                    f"required={out.required}"
                )
            samples.append(_ms(t0, t1))
        return {
            "samples_ms": samples,
            "stats": summarize(samples),
            "session_cards_per_sample": SYNC_SESSION_CARDS,
            "initial_full_upload_ms_uncounted": upload_ms,
            "endpoint": endpoint,
        }
    finally:
        if col is not None:
            col.close()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log_handle.close()


#: Child snippet: cold interpreter -> pylib import -> open 50k -> first
#: queue fetch. Prints one JSON line with per-phase timings.
COLD_START_CHILD = """
import json, sys, time
t0 = time.perf_counter_ns()
from anki.collection import Collection
t1 = time.perf_counter_ns()
col = Collection(sys.argv[1])
t2 = time.perf_counter_ns()
queued = col.sched.get_queued_cards(fetch_limit=1)
t3 = time.perf_counter_ns()
col.close()
print(json.dumps({
    "import_ms": (t1 - t0) / 1e6,
    "open_ms": (t2 - t1) / 1e6,
    "first_queue_ms": (t3 - t2) / 1e6,
    "in_process_total_ms": (t3 - t0) / 1e6,
    "queued": len(queued.cards),
}))
"""


def measure_cold_start(collection_path: Path) -> dict[str, Any]:
    """Child wall time (interpreter + import + open + first queue fetch)."""
    env = _pylib_env()
    wall: list[float] = []
    in_process: list[float] = []
    phases: list[dict[str, float]] = []
    for _ in range(N_COLD_START_SAMPLES):
        t0 = time.perf_counter_ns()
        proc = subprocess.run(
            [sys.executable, "-c", COLD_START_CHILD, str(collection_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(DESKTOP_DIR),
            check=False,
        )
        elapsed = _ms(t0, time.perf_counter_ns())
        if proc.returncode != 0:
            raise RuntimeError(f"cold-start child failed: {proc.stderr.strip()}")
        child = json.loads(proc.stdout.strip().splitlines()[-1])
        wall.append(elapsed)
        in_process.append(child["in_process_total_ms"])
        phases.append(
            {key: round(value, 1) for key, value in child.items() if key != "queued"}
        )
    return {
        "samples_ms": wall,
        "stats": summarize(wall),
        "in_process_stats": summarize(in_process),
        "child_phases": phases,
    }


#: Child snippet: dashboard queries + queue build + a few real answers,
#: then ru_maxrss. On macOS ru_maxrss is BYTES; on Linux it is KiB.
PEAK_MEMORY_CHILD = """
import json, resource, sys
from anki.collection import Collection
from anki.cards import Card
from anki.scheduler.v3 import CardAnswer
col = Collection(sys.argv[1])
mastery = col.topic_mastery()
col.get_readiness()
col.sched.get_queued_cards(fetch_limit=50)
answered = 0
for _ in range(5):
    queued = col.sched.get_queued_cards(fetch_limit=1)
    if not queued.cards:
        break
    item = queued.cards[0]
    card = Card(col)
    card._load_from_backend_card(item.card)
    card.start_timer()
    col.sched.answer_card(
        col.sched.build_answer(card=card, states=item.states, rating=CardAnswer.GOOD)
    )
    answered += 1
col.close()
print(json.dumps({
    "ru_maxrss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    "platform": sys.platform,
    "answered": answered,
    "topics": len(mastery.topics),
}))
"""


def measure_peak_memory(collection_path: Path) -> dict[str, Any]:
    """One child sample (deterministic-ish); returns measured MB."""
    env = _pylib_env()
    proc = subprocess.run(
        [sys.executable, "-c", PEAK_MEMORY_CHILD, str(collection_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(DESKTOP_DIR),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"peak-memory child failed: {proc.stderr.strip()}")
    child = json.loads(proc.stdout.strip().splitlines()[-1])
    raw = child["ru_maxrss_raw"]
    # macOS reports ru_maxrss in bytes; Linux in KiB (documented quirk).
    measured_mb = raw / 2**20 if child["platform"] == "darwin" else raw / 2**10
    return {
        "measured_mb": round(measured_mb, 1),
        "stated_limit_mb": MEMORY_LIMIT_MB,
        "ru_maxrss_raw": raw,
        "ru_maxrss_unit": "bytes" if child["platform"] == "darwin" else "KiB",
        "workload": "open + topic_mastery + get_readiness + queue build + 5 answers",
        "child_detail": child,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


RERUN_COMMAND = (
    "cd desktop && PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/bench.py"
)


def render_markdown(report: dict[str, Any]) -> str:
    machine = report["meta"]["machine"]
    deck = report["deck"]
    ram = f"{machine['ram_gb']} GB RAM" if machine.get("ram_gb") else "RAM unknown"
    lines = [
        "# Speedrun benchmark report (§10 targets)",
        "",
        f"Generated: {report['meta']['generated_at']}",
        "",
        f"Machine: {machine['chip']} ({machine['cpu_count']} cores), {ram}, "
        f"{machine['os']}, Python {machine['python']} — read at run time, "
        "not hard-coded.",
        "",
        f"Deck: {deck['card_count']} cards ({deck['spec']['graded']} graded "
        f"through the real v3 scheduler with FSRS on, "
        f"{deck['spec']['due_spread']} spread over the next "
        f"{deck['spec']['due_spread_days']} days), built in "
        f"{deck['build_wall_s']}s"
        + (" (reused cached build this run)" if deck.get("cached") else "")
        + f"; per-day limits raised to {deck['spec']['new_per_day']} new / "
        f"{deck['spec']['rev_per_day']} review so queues never empty "
        "mid-benchmark.",
        "",
    ]
    if deck["spec"]["cards"] != DEFAULT_CARD_COUNT:
        lines += [
            f"**WARNING: {deck['spec']['cards']} cards, not the official "
            f"{DEFAULT_CARD_COUNT}-card benchmark. Dev run only.**",
            "",
        ]
    lines += [
        "## Results vs §10 targets",
        "",
        "| action | n | p50 ms | p95 ms | worst ms | §10 target | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["targets_table"]:
        verdict = "PASS" if row["passed"] else "**FAIL**"
        if row["action"] == "peak_memory":
            lines.append(
                f"| {row['action']} | 1 | — | — | {row['measured']} MB | "
                f"{row['target_text']} | {verdict} |"
            )
        else:
            lines.append(
                f"| {row['action']} | {row['n']} | {row['p50']} | {row['p95']} "
                f"| {row['worst']} | {row['target_text']} | {verdict} |"
            )
    memory = report["actions"]["peak_memory"]
    sync = report["actions"]["session_sync"]
    cold = report["actions"]["cold_start"]
    lines += [
        "",
        f"Percentile method: {PERCENTILE_METHOD} (rank = ceil(p/100·n) on the "
        "sorted samples); worst = max.",
        "",
        f"Peak memory: measured {memory['measured_mb']} MB "
        f"(ru_maxrss = {memory['ru_maxrss_raw']} {memory['ru_maxrss_unit']}; "
        "on macOS ru_maxrss is bytes, on Linux KiB) vs stated limit "
        f"{memory['stated_limit_mb']} MB. The limit was stated AFTER first "
        "measuring (~2.7x headroom over the measured value, rounded up to "
        "a power of two) and covers the headless engine process only — "
        "the packaged GUI adds Qt/webview memory on top (not measured "
        f"here). Workload: {memory['workload']}.",
        "",
        f"Cold start: child wall time (interpreter spawn + pylib import + "
        f"collection open + first queue fetch) p95 "
        f"{cold['stats']['p95']} ms; in-process portion p95 "
        f"{cold['in_process_stats']['p95']} ms.",
        "",
        f"Sync: initial full upload took "
        f"{sync['initial_full_upload_ms_uncounted']} ms (one-time, NOT "
        f"counted); each sample = answer {sync['session_cards_per_sample']} "
        "cards, then one sync_collection() round-trip against the bundled "
        f"local sync server at {sync['endpoint']}.",
        "",
        "## Definitions and honest disclosures",
        "",
        "- **Engine data-path times, measured headlessly.** UI paint adds "
        "client-side time on top. The real app never blocks its UI thread "
        "on these calls: the dashboard page awaits topicMastery() / "
        "getReadiness() through the async @generated/backend POST bridge "
        "(see `ts/routes/dashboard/DashboardPage.svelte`), and the reviewer "
        "awaits the same scheduler RPCs. Numbers here are NOT paint times "
        "and are not claimed as such.",
        "- **button_press_ack** = answer_card() round-trip alone; "
        "**next_card_after_grade** = answer_card() + get_queued_cards() "
        "measured back-to-back in one span (the full grade→next-card data "
        "path). The next card's presence is asserted every sample.",
        "- **dashboard_first_load** opens a fresh backend + collection per "
        "sample (close+reopen). 'Cold' means a fresh process-level open, "
        "not a cold OS page cache — the file was just written/read, so "
        "true disk-cold first loads may be slower.",
        "- **session_sync** talks to the bundled sync server over loopback; "
        "real-network sync adds latency/bandwidth on top. Samples assert "
        "the sync completed (required=NO_CHANGES).",
        "- **cold_start** is the headless engine path. The packaged desktop "
        "app adds Qt/webview startup on top; there is no display in this "
        "environment, so that part is not measured (and not invented).",
        "- **Screen-freeze (§10 'nothing freezes > 100 ms')** can only be "
        "PROXIED headlessly: the engine-side latencies above plus the fact "
        "that the app performs them off the UI thread (async POST bridge / "
        "background threads). No paint/frame timing was measured; this is "
        "a proxy, disclosed as such.",
        "- **Phone targets**: §10 also lists phone-side timings. They "
        "require an instrumented device; they were NOT measured here and "
        "no phone number in this report is real — none is given.",
        "- get_readiness() abstains on this synthetic deck (no held-out "
        "probe outcomes), but the RPC still executes its full SQL/"
        "aggregation pass — the measured cost is the real data path.",
        "- Deck layout (topics/clusters/ratings/due days) is a pure "
        "function of the card index (seeded constants); note IDs and "
        "review timestamps use the wall clock, so a --rebuild produces an "
        "equivalent but not byte-identical deck. Measurements always run "
        "on a fresh copy of the cached build.",
        f"- Warmups discarded: {REVIEW_WARMUP} review iterations, "
        f"{REFRESH_WARMUP} refresh iterations (disclosed; all other "
        "samples kept, including the slowest).",
        "",
        "## How to re-run",
        "",
        f"    {RERUN_COMMAND}",
        "",
        "(`just bench` runs the same command.) Add `--rebuild` to rebuild "
        "the cached 50k deck from scratch.",
        "",
    ]
    return "\n".join(lines)


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "bench_report.json"
    md_path = report_dir / "bench_report.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark on a worker thread.

    pylib prints a "blocked main thread" stack trace for any backend call
    that takes > 200 ms on the MAIN thread — a diagnostic for GUI code.
    This tool is headless and long calls are the point, so the work runs
    on a worker thread (which is also how the real app issues these RPCs:
    taskman / the async POST bridge, never the UI thread). Timing is
    unaffected; the guard simply never fires.
    """
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="bench"
    ) as pool:
        return pool.submit(_bench_main, argv).result()


def _bench_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--cards",
        type=int,
        default=DEFAULT_CARD_COUNT,
        help="deck size (default %(default)s; non-default runs are marked "
        "as dev runs in the report)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="force a rebuild of the cached benchmark deck",
    )
    parser.add_argument(
        "--scratch-dir",
        default=str(DESKTOP_DIR / "out" / "speedrun_eval" / "bench"),
        help="deck cache + working copies + sync base (default: %(default)s)",
    )
    parser.add_argument(
        "--report-dir",
        default=str(HERE / "eval"),
        help="where to write bench_report.{json,md} (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    scratch = Path(args.scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    spec = deck_spec(args.cards)
    if args.cards == DEFAULT_CARD_COUNT:
        cache_name = "bench50k.anki2"
    else:
        cache_name = f"bench_{args.cards}.anki2"
    cache_path = scratch / cache_name
    work_path = scratch / ("work_" + cache_name)

    machine = machine_info()
    print(
        f"bench: {machine['chip']} / {machine['ram_gb']} GB / {machine['os']} "
        f"/ Python {machine['python']}",
        flush=True,
    )

    run_start = time.perf_counter()
    deck_meta = build_deck(cache_path, spec, rebuild=args.rebuild)

    _progress("bench: creating fresh working copy")
    make_working_copy(cache_path, work_path)

    _progress(f"bench: dashboard_first_load x{N_FIRST_LOAD_SAMPLES} (cold opens)")
    first_load = measure_dashboard_first_load(work_path)

    from anki.collection import Collection

    _progress(
        f"bench: dashboard_refresh x{N_REFRESH_SAMPLES} + review loop "
        f"x{N_REVIEW_SAMPLES}"
    )
    col = Collection(str(work_path))
    try:
        refresh = measure_dashboard_refresh(col)
        review = measure_review_loop(col)
    finally:
        col.close()

    _progress(
        f"bench: session_sync x{N_SYNC_SAMPLES} "
        f"({SYNC_SESSION_CARDS} answers per session; local server "
        f"on port {SYNC_PORT})"
    )
    sync = measure_session_sync(work_path, scratch)

    _progress(f"bench: cold_start x{N_COLD_START_SAMPLES} (child interpreters)")
    cold = measure_cold_start(work_path)

    _progress("bench: peak_memory (one child process)")
    memory = measure_peak_memory(work_path)

    actions: dict[str, Any] = {
        "button_press_ack": review["button_press_ack"],
        "next_card_after_grade": review["next_card_after_grade"],
        "dashboard_first_load": first_load,
        "dashboard_refresh": refresh,
        "session_sync": sync,
        "cold_start": cold,
        "peak_memory": memory,
    }
    action_stats = {
        name: data["stats"] for name, data in actions.items() if "stats" in data
    }
    table = evaluate_targets(action_stats, memory["measured_mb"])

    report: dict[str, Any] = {
        "meta": {
            "tool": "bench",
            "generated_at": _now_iso(),
            "machine": machine,
            "percentile_method": PERCENTILE_METHOD,
            "rerun": RERUN_COMMAND,
            "bench_wall_s": round(time.perf_counter() - run_start, 1),
        },
        "deck": deck_meta,
        "actions": actions,
        "targets_table": table,
        "target_failures": [row["action"] for row in table if not row["passed"]],
        "exit_code": 0,
    }
    json_path, md_path = write_reports(report, Path(args.report_dir))

    print()
    print(format_table(table))
    print()
    failures = report["target_failures"]
    if failures:
        print("=" * 72)
        print(f"TARGET FAILURES (results, not errors): {', '.join(failures)}")
        print("A missed §10 target is reported loudly but does NOT make the")
        print("benchmark exit non-zero; see the table above and the report.")
        print("=" * 72)
    else:
        print("all §10 targets PASS on this machine (see disclosures in report)")
    print(f"reports: {json_path} + {md_path.name}")
    print(f"total bench wall time: {report['meta']['bench_wall_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
