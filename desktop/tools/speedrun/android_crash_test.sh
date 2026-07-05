#!/usr/bin/env bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# android_crash_test.sh - AnkiDroid crash test (challenge 7g, device leg).
#
# Requires a connected device/emulator (adb) with the AnkiDroid debug build
# installed. Launches the reviewer, then ITERATIONS (default 20) times:
# waits a short random delay, kills the app abruptly, relaunches, and
# finally pulls the collection off the device and runs
# `pragma integrity_check` + `pragma quick_check` locally via sqlite3.
#
# Kill modes (KILL_MODE env var):
#   force-stop (default) - `adb shell am force-stop`: ActivityManager kills
#       the app's whole process group with SIGKILL and no lifecycle
#       callbacks. This is the app-level abrupt kill (what Android itself
#       does on force stop / some OOM paths).
#   kill9 - `adb shell run-as $PKG kill -9 <pid>`: a raw SIGKILL to the app
#       process, closest to the desktop crash_test.py child kill. Works on
#       debug builds only (run-as executes with the app's uid).
#
# Usage:
#   tools/speedrun/android_crash_test.sh [package]
#     package        app id (default: com.ichi2.anki.debug)
#   Env overrides:
#     ITERATIONS=20  kill iterations
#     KILL_MODE=force-stop|kill9
#     DECK_APKG=/path/to/deck.apkg   optional test deck to push first
#     OUT_DIR=...    where to put the pulled collection + logs
#     SEED=...       RANDOM seed for the kill delays
#
# Honest scope:
# - If NO device is attached the script FAILS FAST with a clear message
#   (exit 2). It never fabricates a result.
# - The per-iteration reviewer interaction (tap to reveal, tap to grade)
#   is best-effort blind input; on an unfamiliar screen layout the kill
#   may land on the deck list instead of mid-answer. The kill itself is
#   always real and abrupt.
# - Pulling an apkg into AnkiDroid may require a one-time manual import
#   confirmation on the device; the script pushes and fires the VIEW
#   intent but does not fake the tap.
# - The pulled collection is checked AFTER a final force-stop, so the
#   on-device files are quiescent; WAL/journal sidecars are pulled along
#   with the .anki2 so sqlite3 recovers exactly what the device would.

set -euo pipefail

PKG="${1:-com.ichi2.anki.debug}"
ITERATIONS="${ITERATIONS:-20}"
KILL_MODE="${KILL_MODE:-force-stop}"
DECK_APKG="${DECK_APKG:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/../../out/speedrun_eval/crash/android}"
RANDOM="${SEED:-20260704}"

die() { echo "FATAL: $*" >&2; exit 2; }

command -v adb >/dev/null 2>&1 || die "adb not found on PATH - install platform-tools"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 not found on PATH"

# ---- fail fast without a device -------------------------------------------
DEVICES="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
[ -n "$DEVICES" ] || die "no device/emulator attached (adb devices lists none in state 'device'). Connect one with the AnkiDroid debug build installed, then re-run."
DEVICE_COUNT="$(printf '%s\n' "$DEVICES" | wc -l | tr -d ' ')"
[ "$DEVICE_COUNT" = "1" ] || die "expected exactly one attached device, found $DEVICE_COUNT - set ANDROID_SERIAL and re-run."

BOOTED="$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
[ "$BOOTED" = "1" ] || die "device is not fully booted (sys.boot_completed='$BOOTED')"

adb shell pm path "$PKG" >/dev/null 2>&1 || die "package $PKG is not installed on the device (build/install the AnkiDroid debug apk first)"

mkdir -p "$OUT_DIR"
echo "device ok; package $PKG present; kill mode: $KILL_MODE; iterations: $ITERATIONS"

# ---- optional test deck -----------------------------------------------------
if [ -n "$DECK_APKG" ]; then
    [ -f "$DECK_APKG" ] || die "DECK_APKG '$DECK_APKG' does not exist"
    REMOTE_APKG="/data/local/tmp/$(basename "$DECK_APKG")"
    adb push "$DECK_APKG" "$REMOTE_APKG"
    adb shell am start -a android.intent.action.VIEW \
        -d "file://$REMOTE_APKG" -t "application/apkg" -p "$PKG" || true
    echo "pushed $DECK_APKG - confirm the import on the device if prompted"
    sleep 5
fi

# ---- helpers ----------------------------------------------------------------
launch_reviewer() {
    # The Reviewer activity is exported in the AnkiDroid manifest; falling
    # back to the launcher keeps the loop going on builds where a direct
    # start is refused (e.g. nothing to review -> Reviewer finishes).
    adb shell am start -n "$PKG/com.ichi2.anki.Reviewer" >/dev/null 2>&1 \
        || adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
}

best_effort_review_taps() {
    # Blind but harmless: tap screen centre (reveal answer), then the
    # lower-middle grade strip. Coordinates from wm size; failures ignored.
    local size w h
    size="$(adb shell wm size | tr -d '\r' | awk '{print $NF}')" || return 0
    w="${size%x*}"; h="${size#*x}"
    [ -n "$w" ] && [ -n "$h" ] || return 0
    adb shell input tap "$((w / 2))" "$((h / 2))" >/dev/null 2>&1 || true
    sleep 1
    adb shell input tap "$((w / 2))" "$((h * 9 / 10))" >/dev/null 2>&1 || true
}

kill_app() {
    if [ "$KILL_MODE" = "kill9" ]; then
        local pid
        pid="$(adb shell pidof "$PKG" | tr -d '\r' | awk '{print $1}')"
        if [ -n "$pid" ]; then
            # run-as executes with the app's uid on debug builds, so a raw
            # SIGKILL of the app's own process is permitted.
            adb shell run-as "$PKG" kill -9 "$pid" 2>/dev/null \
                || adb shell am force-stop "$PKG"
        else
            echo "  (no pid found - app already dead; force-stopping anyway)"
            adb shell am force-stop "$PKG"
        fi
    else
        adb shell am force-stop "$PKG"
    fi
}

# ---- the kill loop ----------------------------------------------------------
for i in $(seq 1 "$ITERATIONS"); do
    launch_reviewer
    sleep 2
    best_effort_review_taps
    # random 1.0-3.0 s so the kill lands at varying points of the review
    DELAY_TENTHS=$((10 + RANDOM % 21))
    sleep "$(printf '%d.%d' $((DELAY_TENTHS / 10)) $((DELAY_TENTHS % 10)))"
    echo "iteration $i/$ITERATIONS: killing $PKG ($KILL_MODE) after ${DELAY_TENTHS}00 ms"
    kill_app
    sleep 1
done

# ---- pull the collection and verify ------------------------------------------
adb shell am force-stop "$PKG"   # make sure nothing is writing during the pull
sleep 1

PULLED=""
EXTERNAL_DIR="/storage/emulated/0/Android/data/$PKG/files/AnkiDroid"
INTERNAL_DIR="files/AnkiDroid"

if adb shell "[ -f '$EXTERNAL_DIR/collection.anki2' ]" >/dev/null 2>&1; then
    for f in collection.anki2 collection.anki2-wal collection.anki2-shm; do
        adb pull "$EXTERNAL_DIR/$f" "$OUT_DIR/$f" >/dev/null 2>&1 || true
    done
    [ -f "$OUT_DIR/collection.anki2" ] && PULLED="external ($EXTERNAL_DIR)"
fi
if [ -z "$PULLED" ]; then
    # debug builds allow run-as access to the app-private files dir
    for f in collection.anki2 collection.anki2-wal collection.anki2-shm; do
        adb exec-out run-as "$PKG" cat "$INTERNAL_DIR/$f" \
            > "$OUT_DIR/$f" 2>/dev/null || rm -f "$OUT_DIR/$f"
    done
    [ -s "$OUT_DIR/collection.anki2" ] && PULLED="app-private via run-as ($INTERNAL_DIR)"
fi
[ -n "$PULLED" ] || die "could not pull collection.anki2 from the device (tried $EXTERNAL_DIR and run-as $INTERNAL_DIR). Open AnkiDroid once so the collection exists, or adjust the path for your storage layout."

echo "pulled collection from: $PULLED"
INTEGRITY="$(sqlite3 "$OUT_DIR/collection.anki2" 'pragma integrity_check;')"
QUICK="$(sqlite3 "$OUT_DIR/collection.anki2" 'pragma quick_check;')"
echo "integrity_check: $INTEGRITY"
echo "quick_check:     $QUICK"

if [ "$INTEGRITY" = "ok" ] && [ "$QUICK" = "ok" ]; then
    echo "RESULT: PASS - collection intact after $ITERATIONS abrupt kills ($KILL_MODE)"
    exit 0
else
    echo "RESULT: FAIL - CORRUPTION DETECTED after $ITERATIONS kills" >&2
    exit 1
fi
