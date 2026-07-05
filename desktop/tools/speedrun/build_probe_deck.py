# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the held-out probe deck (Anki Speedrun, Phase 3 M3).

Consumes speedrun-probe-v1 records (probes/PROBE_SCHEMA.md) from
probes/probe_bank.jsonl, validates every record AND the bank-level
invariants via probe_harness (hard fail on any violation), creates a
minimal probe MCQ note type in the ladder style (front shows stem +
choices with the same tap-to-reveal interaction as the solve rung; back is
a no-JS fallback showing the correct answer + rationale - the [R9]
feedback step), tags each note per the schema's "Tagging" section
(probe::held_out, probe::pool::<pool>, cfa::topic::<topic>,
cluster::<cluster>, probe::concept::<id>, probe::variant::<a|b> - never
aig::*, never rung::*), and exports cfa_probes.apkg.

The deck ("CFA Probes") gets its OWN preset with contrast, fade and
readiness-allocation all explicitly OFF: probes are measurement, not
study, and no scheduling feature may reorder or gate them.

Usage (from desktop/, after a build):
    PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_probe_deck.py \
        [--items PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import html
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ladder_notetypes  # type: ignore[import-not-found]  # noqa: E402
import ladder_schema  # type: ignore[import-not-found]  # noqa: E402
import probe_harness  # type: ignore[import-not-found]  # noqa: E402

from anki.collection import Collection  # noqa: E402
from anki.import_export_pb2 import ExportAnkiPackageOptions  # noqa: E402

DECK_NAME = "CFA Probes"
#: Its OWN preset: importing the probe deck must never inherit (or enable)
#: the study deck's scheduling features.
PRESET_NAME = "CFA Probes"

PROBE_NOTETYPE_NAME = "Speedrun Probe MCQ"

HERE = Path(__file__).parent

# The front mirrors the solve rung's interaction (same class names, same
# reveal script) minus the per-distractor Wrong fields and the
# self-explain ordinal: probes must generate exactly ONE card per note so
# the revlog carries exactly one first answer per probe.
_PROBE_FRONT = (
    '<div class="sr-card sr-solve sr-solve-front" data-correct="{{Correct}}">\n'
    "{{#Title}}"
    '<div class="sr-kicker">{{Title}}</div>'
    "{{/Title}}\n"
    '<div class="sr-stem">{{Stem}}</div>\n'
    '<div class="sr-choices">\n'
    '<button type="button" class="sr-choice" data-letter="A"><span class="sr-choice-letter">A</span><span class="sr-choice-text">{{ChoiceA}}</span></button>\n'
    '<button type="button" class="sr-choice" data-letter="B"><span class="sr-choice-letter">B</span><span class="sr-choice-text">{{ChoiceB}}</span></button>\n'
    '<button type="button" class="sr-choice" data-letter="C"><span class="sr-choice-letter">C</span><span class="sr-choice-text">{{ChoiceC}}</span></button>\n'
    "</div>\n"
    '<div class="sr-feedback" hidden>\n'
    '<div class="sr-verdict"></div>\n'
    '<div class="sr-rationale"><span class="sr-why-label">Why {{Correct}} is correct:</span> {{Rationale}}</div>\n'
    '<div class="sr-selfgrade">Show the answer, then grade yourself with the usual buttons.</div>\n'
    "</div>\n"
    "</div>\n" + ladder_notetypes._SOLVE_FRONT_SCRIPT
)

# No-JS fallback back side: names the correct answer and renders the
# rationale even if the script never ran ([R9]).
_PROBE_BACK = """\
<div class="sr-card sr-solve sr-solve-back" data-correct="{{Correct}}">
{{#Title}}<div class="sr-kicker">{{Title}}</div>{{/Title}}
<div class="sr-stem">{{Stem}}</div>
<div class="sr-answer-line">Correct answer: <span class="sr-answer-letter">{{Correct}}</span></div>
<div class="sr-choices sr-choices-static">
<div class="sr-choice" data-letter="A"><span class="sr-choice-letter">A</span><span class="sr-choice-text">{{ChoiceA}}</span></div>
<div class="sr-choice" data-letter="B"><span class="sr-choice-letter">B</span><span class="sr-choice-text">{{ChoiceB}}</span></div>
<div class="sr-choice" data-letter="C"><span class="sr-choice-letter">C</span><span class="sr-choice-text">{{ChoiceC}}</span></div>
</div>
<div class="sr-feedback">
<div class="sr-rationale"><span class="sr-why-label">Why {{Correct}} is correct:</span> {{Rationale}}</div>
</div>
</div>
<script>
(function () {
    var root = document.querySelector(".sr-solve-back:not([data-sr-bound])");
    if (!root) { return; }
    root.setAttribute("data-sr-bound", "1");
    var correct = (root.getAttribute("data-correct") || "").trim();
    var choices = root.querySelectorAll(".sr-choice");
    for (var i = 0; i < choices.length; i++) {
        if (choices[i].getAttribute("data-letter") === correct) {
            choices[i].className += " sr-correct";
        }
    }
})();
</script>
"""

PROBE_NOTETYPE = {
    "name": PROBE_NOTETYPE_NAME,
    "fields": [
        "Title",
        "Stem",
        "ChoiceA",
        "ChoiceB",
        "ChoiceC",
        "Correct",
        "Rationale",
    ],
    "templates": [
        {"name": "Probe", "qfmt": _PROBE_FRONT, "afmt": _PROBE_BACK},
    ],
    # same look as the solve rung; the unused Wrong*/self-explain CSS rules
    # are inert
    "css": ladder_notetypes.SOLVE_MCQ["css"],
}


def _escape(value: object) -> str:
    return html.escape(str(value))


def probe_note_fields(record: dict) -> dict[str, str]:
    choices = record["choices"]
    return {
        "Title": _escape(record["title"]),
        "Stem": _escape(record["stem"]),
        "ChoiceA": _escape(choices["A"]),
        "ChoiceB": _escape(choices["B"]),
        "ChoiceC": _escape(choices["C"]),
        "Correct": record["correct"],
        "Rationale": _escape(record["rationale"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--items",
        default=str(HERE / "probes" / "probe_bank.jsonl"),
        help="probe bank JSONL (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=str(HERE / "cfa_probes.apkg"),
        help="output .apkg path (default: %(default)s)",
    )
    return parser.parse_args()


def create_notetype(col: Collection, spec: dict) -> dict:
    notetype = col.models.new(spec["name"])
    for field_name in spec["fields"]:
        col.models.add_field(notetype, col.models.new_field(field_name))
    for template_spec in spec["templates"]:
        template = col.models.new_template(template_spec["name"])
        template["qfmt"] = template_spec["qfmt"]
        template["afmt"] = template_spec["afmt"]
        col.models.add_template(notetype, template)
    notetype["css"] = spec["css"]
    col.models.add(notetype)
    return notetype


def main() -> None:
    args = parse_args()
    output = Path(args.output).resolve()

    # ---- validate everything first; the build hard-fails on violations ----
    records, failures = probe_harness.load_bank(args.items)
    failures = failures + probe_harness.validate_bank(records)
    if failures:
        print("refusing to build - invalid probe records:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)

    # [R9] feedback lint: the answer side must actually render Rationale.
    lint_failures = ladder_schema.lint_notetype_feedback(
        PROBE_NOTETYPE["templates"], feedback_fields=("Rationale",)
    )
    if lint_failures:
        print(
            "refusing to build - probe notetype failed the feedback lint [R9]:",
            file=sys.stderr,
        )
        for failure in lint_failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "collection.anki2"))

        deck_id = col.decks.id(DECK_NAME)
        assert deck_id is not None
        notetype = create_notetype(col, PROBE_NOTETYPE)

        pool_counts: Counter[str] = Counter()
        for record in records:
            note = col.new_note(notetype)
            for name, value in probe_note_fields(record).items():
                note[name] = value
            note.tags = probe_harness.tags_for_probe(record)
            col.add_note(note, deck_id)
            pool_counts[record["pool"]] += 1

        # ---- verify the built collection BEFORE exporting ----
        note_ids = col.find_notes(f'"deck:{DECK_NAME}"')
        assert len(note_ids) == len(records) == 70, (
            f"expected 70 probe notes, built {len(note_ids)}"
        )
        seen_pools: Counter[str] = Counter()
        for nid in note_ids:
            note = col.get_note(nid)
            tags = set(note.tags)
            assert probe_harness.PROBE_HELD_OUT_TAG in tags, note.tags
            pools = [
                tag for tag in tags if tag.startswith(probe_harness.POOL_TAG_PREFIX)
            ]
            assert len(pools) == 1, f"expected exactly one pool tag, got {pools}"
            seen_pools[pools[0].removeprefix(probe_harness.POOL_TAG_PREFIX)] += 1
            assert any(
                tag.startswith(probe_harness.CLUSTER_TAG_PREFIX) for tag in tags
            ), note.tags
            assert any(
                tag.startswith(probe_harness.TOPIC_TAG_PREFIX) for tag in tags
            ), note.tags
            # hand-authored: no aig::* tag may ever appear; never gated
            assert not any(tag.startswith("aig::") for tag in tags), note.tags
            assert not any(tag.startswith("rung::") for tag in tags), note.tags
            # exactly one card per note: one first answer per probe
            assert len(col.card_ids_of_note(nid)) == 1
        assert seen_pools["performance"] >= probe_harness.MIN_PERFORMANCE_ITEMS

        # ---- own preset, every scheduling feature explicitly off ----
        conf = col.decks.add_config(PRESET_NAME)
        conf["contrastScheduling"] = False
        conf["contrastTagPrefix"] = ""
        conf["contrastConfusableTag"] = ""
        conf["fadeEnabled"] = False
        conf["readinessAllocation"] = False
        col.decks.update_config(conf)
        deck = col.decks.get(deck_id)
        assert deck is not None
        deck["conf"] = conf["id"]
        col.decks.save(deck)

        col.export_anki_package(
            out_path=str(output),
            options=ExportAnkiPackageOptions(
                with_scheduling=False,
                with_deck_configs=True,
                with_media=False,
                legacy=True,
            ),
            limit=None,
        )
        total = len(note_ids)
        col.close()

    breakdown = ", ".join(
        f"{pool}={count}" for pool, count in sorted(pool_counts.items())
    )
    print(f"wrote {total} probe notes ({breakdown}) to {output}")


if __name__ == "__main__":
    main()
