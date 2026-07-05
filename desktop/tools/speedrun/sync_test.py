# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Two-client offline sync test against the bundled sync server (rubric 7b).

One command proves, with real syncs against the real bundled server
(``python -m anki.syncserver``, the same protocol AnkiWeb and AnkiDroid
speak):

1. **Setup**: start the server on a dedicated port (default 28711 - the
   bench agent owns 28701), create client A with ~40 cards, full-upload
   it, then clone client B by full-downloading into a second collection
   directory. The call order mirrors qt/aqt/sync.py: ``sync_login`` ->
   ``sync_collection`` -> (full sync required) ``close_for_full_sync`` ->
   ``full_upload_or_download`` -> ``reopen(after_full_sync=True)``.
2. **Offline phase**: with the server process STOPPED, answer 10 distinct
   due cards on A and 10 DIFFERENT cards on B through the real v3
   scheduler. "Offline" is honest for Anki's model: sync is manual and
   explicit, so offline means no sync calls are made - and this script
   additionally stops the server so any accidental call would fail.
3. **Reconnect**: restart the server, sync A then B then A. Assert both
   sides converge and the union revlog contains exactly A's 10 + B's 10
   entries for the target cards - none lost, none double-counted
   (deduped by revlog primary key AND by (cid, taken-at-ms) pairs; the
   revlog id IS the taken-at epoch-ms).
4. **Conflict**: ONE card is answered on BOTH clients while offline
   (different eases, >1 s apart so the card modification times differ
   deterministically). After syncing both ways until stable, assert
   Anki's documented rule: the revlog is append-only so BOTH review
   entries survive on both sides (they are two real, distinct reviews -
   nothing is double-counted), and the card's final scheduling state is
   the copy with the NEWER modification time (rslib
   sync/collection/chunks.rs ``add_or_update_card_if_newer``).

Reports: eval/sync_test_report.json + eval/sync_test_report.md.
Exit code is non-zero on any lost/duplicated review, failed convergence,
or an indeterminate conflict outcome.

Usage (from desktop/, needs the built pylib):

    PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/sync_test.py

The module imports only the stdlib at import time; pylib is imported
lazily so the unit tests (tests/test_sync_test.py) run under plain
python3.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DESKTOP_DIR = HERE.parents[1]
DEFAULT_BASE_DIR = DESKTOP_DIR / "out" / "speedrun_eval" / "sync"
DEFAULT_REPORT_DIR = HERE / "eval"

DEFAULT_PORT = 28711  # bench agent uses 28701 - do not collide
SYNC_USER = "cfa"
SYNC_PASS = "speedrun"
DEFAULT_CARDS = 40
DEFAULT_SEED = 20260704
PER_CLIENT_REVIEWS = 10
#: Revlog ids are taken-at epoch-ms and the merge is INSERT OR IGNORE on
#: that id (idempotent retries). Two DIFFERENT reviews grading in the
#: same millisecond on two devices would collide and the later-arriving
#: entry would be ignored - impossible at human review speed, but a
#: script grading 10 cards in <1 ms hits it immediately. Space scripted
#: answers so the run models human-speed reviewing; the union check still
#: carries a cross-side collision tripwire.
ANSWER_SPACING_S = 0.05

DECK_NAME = "CFA Sync Test"

#: SyncCollectionResponse.ChangesRequired values (proto/anki/sync.proto).
NO_CHANGES = 0
NORMAL_SYNC = 1
FULL_SYNC = 2
FULL_DOWNLOAD = 3
FULL_UPLOAD = 4

#: Card-table fields that define the scheduling state we compare across
#: clients (usn intentionally excluded: it is bookkeeping the server
#: rewrites; mod is the conflict tiebreaker itself).
CARD_STATE_FIELDS = (
    "mod",
    "type",
    "queue",
    "due",
    "ivl",
    "factor",
    "reps",
    "lapses",
    "left",
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no pylib)
# ---------------------------------------------------------------------------


def check_revlog_union(
    a_rows: list[tuple[int, int]],
    b_rows: list[tuple[int, int]],
    expected_a: list[tuple[int, int]],
    expected_b: list[tuple[int, int]],
) -> dict[str, Any]:
    """Verify the union of offline reviews landed on BOTH sides exactly
    once. Rows and expectations are (revlog_id, cid) pairs; the revlog id
    is the taken-at epoch-ms and the table's primary key.

    Detects: lost entries (expected pair absent from a side), duplicated
    entries (a pair appearing more than once in one side's table - the
    double-count case), and id collisions (same revlog id on different
    cids, which the primary-key-only view would mask).
    """
    expected = list(expected_a) + list(expected_b)
    result: dict[str, Any] = {
        "expected_total": len(expected),
        "lost": [],
        "duplicated": [],
        "id_collisions": [],
        "cross_side_id_collisions": [],
        "per_side": {},
    }
    # Tripwire for the epoch-ms id collision hazard: the SAME revlog id
    # minted on both clients for DIFFERENT cards would make the
    # append-only merge (INSERT OR IGNORE by id) silently drop the
    # later-arriving entry.
    ids_a = {rid: cid for rid, cid in expected_a}
    for rid, cid in expected_b:
        if rid in ids_a and ids_a[rid] != cid:
            result["cross_side_id_collisions"].append(
                {"id": rid, "cid_a": ids_a[rid], "cid_b": cid}
            )
    for side, rows in (("a", a_rows), ("b", b_rows)):
        by_pair: dict[tuple[int, int], int] = {}
        by_id: dict[int, set[int]] = {}
        for rid, cid in rows:
            by_pair[(rid, cid)] = by_pair.get((rid, cid), 0) + 1
            by_id.setdefault(rid, set()).add(cid)
        found = sum(1 for pair in expected if by_pair.get(pair, 0) >= 1)
        result["per_side"][side] = {"found": found, "expected": len(expected)}
        for pair in expected:
            count = by_pair.get(pair, 0)
            if count == 0:
                result["lost"].append({"side": side, "id": pair[0], "cid": pair[1]})
            elif count > 1:
                result["duplicated"].append(
                    {"side": side, "id": pair[0], "cid": pair[1], "count": count}
                )
        for rid, cids in sorted(by_id.items()):
            if len(cids) > 1:
                result["id_collisions"].append(
                    {"side": side, "id": rid, "cids": sorted(cids)}
                )
    result["passed"] = not (
        result["lost"]
        or result["duplicated"]
        or result["id_collisions"]
        or result["cross_side_id_collisions"]
    )
    return result


def conflict_verdict(
    snapshot_a: dict[str, Any],
    snapshot_b: dict[str, Any],
    final_a: dict[str, Any],
    final_b: dict[str, Any],
) -> dict[str, Any]:
    """Assert the documented conflict rule on card scheduling state.

    Inputs are card-state dicts (CARD_STATE_FIELDS) captured on each
    client right after its offline answer (snapshots) and after the
    clients converged (finals). The copy with the strictly newer ``mod``
    must be the one both clients ended up with.
    """
    result: dict[str, Any] = {
        "a_mod": snapshot_a.get("mod"),
        "b_mod": snapshot_b.get("mod"),
        "converged": final_a == final_b,
        "winner": None,
        "reason": None,
        "passed": False,
    }
    if not result["converged"]:
        result["reason"] = f"clients did NOT converge: A={final_a} B={final_b}"
        return result
    if snapshot_a.get("mod") == snapshot_b.get("mod"):
        result["reason"] = (
            "card modification times are equal - the newer-mod rule cannot "
            "pick a winner deterministically (test bug: answers must be "
            ">1 s apart)"
        )
        return result
    newer_side, newer_snapshot = max(
        (("A", snapshot_a), ("B", snapshot_b)), key=lambda item: item[1]["mod"]
    )
    if final_a == newer_snapshot:
        result["winner"] = newer_side
        result["reason"] = (
            f"client {newer_side}'s state won: its card.mod "
            f"({newer_snapshot['mod']}) is newer, matching rslib's "
            "add_or_update_card_if_newer rule"
        )
        result["passed"] = True
    else:
        result["reason"] = (
            f"converged state {final_a} does not match the newer-mod copy "
            f"{newer_snapshot} (expected client {newer_side} to win)"
        )
    return result


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        "# Sync test report (two clients, offline reviews, conflict rule)",
        "",
        f"Generated: {meta['generated_at']} — server port {meta['port']}, "
        f"seed {meta['seed']}",
        "",
        f"Invocation: `{meta['invocation']}`",
        "",
        "## Setup",
        "",
    ]
    setup = report.get("setup", {})
    if setup:
        lines += [
            f"- client A created with {setup['cards']} cards; first sync "
            f"required={setup['a_first_sync_required']} -> full upload",
            f"- client B cloned by full download "
            f"(required={setup['b_first_sync_required']}); card ids match A: "
            f"{setup['card_ids_match']}",
            "",
        ]
    union = report.get("union_check")
    offline = report.get("offline", {})
    lines += ["## Offline reviews -> reconnect -> union", ""]
    if union:
        lines += [
            f"- A answered {len(offline.get('a_reviews', []))} cards offline; "
            f"B answered {len(offline.get('b_reviews', []))} DIFFERENT cards "
            f"(server process stopped during the phase: "
            f"{offline.get('server_stopped')})",
            "",
            "| side | expected (A's 10 + B's 10) | found | lost | duplicated |",
            "|---|---|---|---|---|",
        ]
        for side in ("a", "b"):
            per = union["per_side"][side]
            lost = sum(1 for row in union["lost"] if row["side"] == side)
            dup = sum(1 for row in union["duplicated"] if row["side"] == side)
            lines.append(
                f"| {side.upper()} | {per['expected']} | {per['found']} | "
                f"{lost} | {dup} |"
            )
        lines += [
            "",
            f"Union check: **{'PASS' if union['passed'] else 'FAIL'}** — "
            f"{union['expected_total']} expected entries, "
            f"{len(union['lost'])} lost, {len(union['duplicated'])} "
            f"duplicated, {len(union['id_collisions'])} id collisions, "
            f"{len(union['cross_side_id_collisions'])} cross-side id "
            "collisions.",
            f"Full revlog tables identical on both sides: "
            f"{report.get('full_revlog_identical')}",
            "",
        ]
    conflict = report.get("conflict")
    lines += ["## Conflict: same card answered on both clients offline", ""]
    if conflict:
        verdict = conflict["verdict"]
        lines += [
            f"- card id {conflict['cid']}: A answered ease "
            f"{conflict['a_ease']} (revlog id {conflict['a_revlog_id']}), "
            f"B answered ease {conflict['b_ease']} (revlog id "
            f"{conflict['b_revlog_id']}) — {conflict['gap_seconds']} s apart",
            f"- both revlog entries present on A: "
            f"{conflict['both_entries_on_a']}; on B: "
            f"{conflict['both_entries_on_b']} (append-only history - two "
            "real reviews, neither double-counted)",
            f"- scheduling-state winner: **{verdict['winner']}** — {verdict['reason']}",
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
    '"Offline" here means no sync calls were made between the phases - '
    "honest for Anki's model, where sync is manual and explicit. The "
    "server process was additionally STOPPED during the offline phases, "
    "so any stray sync attempt would have failed loudly.",
    "Observed protocol hazard (found by an earlier run of this script, "
    "now guarded): revlog ids are taken-at epoch-MILLISECONDS and the "
    "append-only merge is INSERT OR IGNORE on that id, so two DIFFERENT "
    "reviews graded in the same millisecond on two different devices "
    "collide and the later-arriving entry is dropped. Unreachable at "
    "human review speed (reviews are seconds apart); this script now "
    "spaces scripted answers 50 ms apart to model that, and keeps a "
    "cross-side collision tripwire in the union check so the hazard is "
    "detected loudly rather than masked.",
    "The revlog id doubles as the taken-at epoch-ms and the table's "
    "primary key; the server merges revlog entries append-only (rslib "
    "sync/collection/chunks.rs merge_revlog -> INSERT OR IGNORE). Both "
    "conflict reviews therefore survive as two REAL distinct reviews; "
    "review counts add, nothing is double-counted.",
    "Card scheduling conflicts resolve by modification time: "
    "add_or_update_card_if_newer keeps the locally-modified copy unless "
    "the incoming one is newer. The test spaces the two answers >1 s "
    "apart so card.mod (seconds) orders them deterministically.",
    "The phone offline path is exercised by AnkiDroid using this same "
    "sync protocol against the same server implementation; this script "
    "does not re-prove the Android client itself.",
    "This script talks only to 127.0.0.1 on its own port (28711 by "
    "default; the bench agent owns 28701).",
]


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------


def wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def port_is_free(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return False
    except OSError:
        return True


def start_server(port: int, base_dir: Path) -> subprocess.Popen:
    base_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        SYNC_BASE=str(base_dir),
        SYNC_HOST="127.0.0.1",
        SYNC_PORT=str(port),
        SYNC_USER1=f"{SYNC_USER}:{SYNC_PASS}",
        RUST_LOG=env.get("RUST_LOG", "error"),
        PYTHONPATH=env.get("PYTHONPATH", str(DESKTOP_DIR / "out" / "pylib")),
    )
    log = open(base_dir / "server.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "anki.syncserver"],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    if not wait_for_port(port):
        proc.terminate()
        raise RuntimeError(
            f"sync server did not open 127.0.0.1:{port} within 15 s "
            f"(see {base_dir / 'server.log'})"
        )
    return proc


def stop_server(proc: subprocess.Popen | None, port: int) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    deadline = time.monotonic() + 10
    while not port_is_free(port) and time.monotonic() < deadline:
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# Client operations (pylib imported lazily)
# ---------------------------------------------------------------------------


def sync_to_converged(col: Any, auth: Any) -> int:
    """One normal sync, asserting no full sync is demanded (the call
    itself performs the chunked normal sync when one is needed).
    Returns the ChangesRequired value."""
    out = col.sync_collection(auth, False)
    if out.required not in (NO_CHANGES,):
        raise RuntimeError(
            f"expected a normal in-place sync, server demanded "
            f"required={out.required} (full sync) - schema diverged?"
        )
    return out.required


def full_upload(col: Any, auth: Any) -> None:
    col.close_for_full_sync()
    col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
    col.reopen(after_full_sync=True)


def full_download(col: Any, auth: Any) -> None:
    col.close_for_full_sync()
    col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
    col.reopen(after_full_sync=True)


def answer_card(col: Any, cid: int, rating: int) -> tuple[int, int]:
    """Grade one due card through the real v3 scheduler (the exact
    get_scheduling_states -> build_answer -> answer_card path the
    reviewer uses). Returns (revlog_id, ease_recorded)."""
    states = col._backend.get_scheduling_states(cid)
    card = col.get_card(cid)
    card.start_timer()
    answer = col.sched.build_answer(card=card, states=states, rating=rating)
    col.sched.answer_card(answer)
    rid, ease = col.db.first(
        "select id, ease from revlog where cid = ? order by id desc limit 1", cid
    )
    return int(rid), int(ease)


def card_state(col: Any, cid: int) -> dict[str, Any]:
    row = col.db.first(
        "select mod, type, queue, due, ivl, factor, reps, lapses, left "
        "from cards where id = ?",
        cid,
    )
    return dict(zip(CARD_STATE_FIELDS, row))


def revlog_pairs(col: Any) -> list[tuple[int, int]]:
    return [
        (int(rid), int(cid))
        for rid, cid in col.db.all("select id, cid from revlog order by id")
    ]


def revlog_full_rows(col: Any) -> list[tuple]:
    """Full revlog rows minus usn (server bookkeeping) for convergence
    comparison."""
    return [
        tuple(row)
        for row in col.db.all(
            "select id, cid, ease, ivl, lastIvl, factor, time, type "
            "from revlog order by id"
        )
    ]


# ---------------------------------------------------------------------------
# The test itself
# ---------------------------------------------------------------------------


def run_sync_test(args: argparse.Namespace) -> dict[str, Any]:
    from anki.collection import Collection
    from anki.scheduler.v3 import CardAnswer

    base_dir = Path(args.base_dir)
    server_dir = base_dir / "server"
    report: dict[str, Any] = {}
    failures: list[str] = []
    rng = random.Random(args.seed)

    # fresh directories per run: this test is about sync, not caching
    for sub in ("server", "client_a", "client_b"):
        path = base_dir / sub
        if path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
        path.mkdir(parents=True, exist_ok=True)

    endpoint = f"http://127.0.0.1:{args.port}/"
    server: subprocess.Popen | None = None
    col_a = None
    col_b = None
    try:
        server = start_server(args.port, server_dir)

        # ---- client A: seed + full upload ----
        col_a = Collection(str(base_dir / "client_a" / "collection.anki2"))
        deck_id = col_a.decks.id(DECK_NAME)
        notetype = col_a.models.by_name("Basic")
        for index in range(args.cards):
            note = col_a.new_note(notetype)
            note["Front"] = f"Sync-test question {index}"
            note["Back"] = f"Sync-test answer {index}"
            note.tags = ["cfa::topic::ethics"]
            col_a.add_note(note, deck_id)
        col_a.decks.select(deck_id)
        auth_a = col_a.sync_login(SYNC_USER, SYNC_PASS, endpoint)
        first_a = col_a.sync_collection(auth_a, False).required
        if first_a not in (FULL_UPLOAD, FULL_SYNC, FULL_DOWNLOAD):
            raise RuntimeError(f"expected a full first sync, got {first_a}")
        full_upload(col_a, auth_a)

        # ---- client B: clone by full download ----
        col_b = Collection(str(base_dir / "client_b" / "collection.anki2"))
        auth_b = col_b.sync_login(SYNC_USER, SYNC_PASS, endpoint)
        first_b = col_b.sync_collection(auth_b, False).required
        if first_b not in (FULL_DOWNLOAD, FULL_SYNC):
            raise RuntimeError(f"expected a full download for B, got {first_b}")
        full_download(col_b, auth_b)

        a_cids = sorted(int(c) for c in col_a.find_cards(f'deck:"{DECK_NAME}"'))
        b_cids = sorted(int(c) for c in col_b.find_cards(f'deck:"{DECK_NAME}"'))
        report["setup"] = {
            "cards": args.cards,
            "a_first_sync_required": first_a,
            "b_first_sync_required": first_b,
            "card_ids_match": a_cids == b_cids,
        }
        if a_cids != b_cids:
            raise RuntimeError("clone failed: card ids differ between A and B")
        if len(a_cids) < 2 * PER_CLIENT_REVIEWS + 1:
            raise RuntimeError("not enough cards for the disjoint review sets")
        targets_a = a_cids[:PER_CLIENT_REVIEWS]
        targets_b = a_cids[PER_CLIENT_REVIEWS : 2 * PER_CLIENT_REVIEWS]
        conflict_cid = a_cids[2 * PER_CLIENT_REVIEWS]

        # ---- offline phase: server STOPPED, disjoint reviews ----
        stop_server(server, args.port)
        server = None
        ratings = (CardAnswer.AGAIN, CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY)
        a_reviews = []
        for cid in targets_a:
            rid, ease = answer_card(col_a, cid, CardAnswer.GOOD)
            a_reviews.append({"cid": cid, "revlog_id": rid, "ease": ease})
            time.sleep(ANSWER_SPACING_S)
        b_reviews = []
        for cid in targets_b:
            rid, ease = answer_card(col_b, cid, rng.choice(ratings))
            b_reviews.append({"cid": cid, "revlog_id": rid, "ease": ease})
            time.sleep(ANSWER_SPACING_S)
        report["offline"] = {
            "server_stopped": True,
            "a_reviews": a_reviews,
            "b_reviews": b_reviews,
        }

        # ---- reconnect: A, B, A ----
        server = start_server(args.port, server_dir)
        sync_to_converged(col_a, auth_a)
        sync_to_converged(col_b, auth_b)
        sync_to_converged(col_a, auth_a)

        expected_a = [(row["revlog_id"], row["cid"]) for row in a_reviews]
        expected_b = [(row["revlog_id"], row["cid"]) for row in b_reviews]
        union = check_revlog_union(
            revlog_pairs(col_a), revlog_pairs(col_b), expected_a, expected_b
        )
        report["union_check"] = union
        report["full_revlog_identical"] = revlog_full_rows(col_a) == revlog_full_rows(
            col_b
        )
        if not union["passed"]:
            failures.append(
                f"union check failed: {len(union['lost'])} lost, "
                f"{len(union['duplicated'])} duplicated, "
                f"{len(union['id_collisions'])} id collisions, "
                f"{len(union['cross_side_id_collisions'])} cross-side id "
                "collisions"
            )
        if not report["full_revlog_identical"]:
            failures.append("full revlog tables differ between A and B")

        # ---- conflict: same card on both clients, offline ----
        stop_server(server, args.port)
        server = None
        a_rid, a_ease = answer_card(col_a, conflict_cid, CardAnswer.GOOD)
        snapshot_a = card_state(col_a, conflict_cid)
        time.sleep(1.2)  # card.mod has 1 s resolution; force a strict order
        b_rid, b_ease = answer_card(col_b, conflict_cid, CardAnswer.AGAIN)
        snapshot_b = card_state(col_b, conflict_cid)
        gap = snapshot_b["mod"] - snapshot_a["mod"]

        server = start_server(args.port, server_dir)
        sync_to_converged(col_a, auth_a)
        sync_to_converged(col_b, auth_b)
        sync_to_converged(col_a, auth_a)

        final_a = card_state(col_a, conflict_cid)
        final_b = card_state(col_b, conflict_cid)
        pairs_a = set(revlog_pairs(col_a))
        pairs_b = set(revlog_pairs(col_b))
        both_on_a = (a_rid, conflict_cid) in pairs_a and (
            b_rid,
            conflict_cid,
        ) in pairs_a
        both_on_b = (a_rid, conflict_cid) in pairs_b and (
            b_rid,
            conflict_cid,
        ) in pairs_b
        verdict = conflict_verdict(snapshot_a, snapshot_b, final_a, final_b)
        report["conflict"] = {
            "cid": conflict_cid,
            "a_revlog_id": a_rid,
            "a_ease": a_ease,
            "b_revlog_id": b_rid,
            "b_ease": b_ease,
            "gap_seconds": gap,
            "snapshot_a": snapshot_a,
            "snapshot_b": snapshot_b,
            "final_a": final_a,
            "final_b": final_b,
            "both_entries_on_a": both_on_a,
            "both_entries_on_b": both_on_b,
            "verdict": verdict,
        }
        if not (both_on_a and both_on_b):
            failures.append(
                "conflict revlog entries missing: "
                f"A has both={both_on_a}, B has both={both_on_b}"
            )
        if not verdict["passed"]:
            failures.append(f"conflict verdict failed: {verdict['reason']}")
    finally:
        for col in (col_a, col_b):
            if col is not None:
                try:
                    col.close()
                except Exception as exc:
                    failures.append(f"collection close failed: {exc}")
        stop_server(server, args.port)

    report["failures"] = failures
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="sync server port (default: %(default)s; 28701 is taken)",
    )
    parser.add_argument(
        "--base-dir",
        default=str(DEFAULT_BASE_DIR),
        help="scratch directory for server + clients (default: %(default)s)",
    )
    parser.add_argument(
        "--cards",
        type=int,
        default=DEFAULT_CARDS,
        help="cards seeded on client A (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="RNG seed for B's gradings (default: %(default)s)",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="where to write sync_test_report.{json,md} (default: %(default)s)",
    )
    return parser


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "sync_test_report.json"
    md_path = report_dir / "sync_test_report.md"
    json_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report: dict[str, Any] = {
        "meta": {
            "tool": "sync_test",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "port": args.port,
            "seed": args.seed,
            "python": sys.version.split()[0],
            "invocation": (
                "PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/sync_test.py"
            ),
        },
        "honesty": HONESTY_NOTES,
    }
    try:
        result = run_sync_test(args)
    except Exception as exc:
        result = {"failures": [f"sync test aborted: {exc!r}"]}
    report.update(result)
    failures = report.get("failures", [])

    union = report.get("union_check")
    if union:
        print(
            f"union check: {'PASS' if union['passed'] else 'FAIL'} "
            f"({union['per_side']['a']['found']}/{union['expected_total']} on A, "
            f"{union['per_side']['b']['found']}/{union['expected_total']} on B; "
            f"{len(union['lost'])} lost, {len(union['duplicated'])} duplicated)"
        )
    conflict = report.get("conflict")
    if conflict:
        verdict = conflict["verdict"]
        print(
            f"conflict: winner={verdict['winner']} "
            f"(A mod {verdict['a_mod']} vs B mod {verdict['b_mod']}); "
            f"both revlog entries on both sides: "
            f"{conflict['both_entries_on_a'] and conflict['both_entries_on_b']}"
        )
    report["exit_code"] = 1 if failures else 0
    json_path, md_path = write_reports(report, Path(args.report_dir))
    print(f"reports: {json_path} + {md_path.name}")
    if failures:
        print("FAILURES: " + "; ".join(failures), file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
