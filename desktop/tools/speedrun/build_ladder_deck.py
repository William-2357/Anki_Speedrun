# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the CFA fade-ladder deck (Anki Speedrun, Phase 2 M2+M3).

Consumes speedrun-item-v1 records (ITEM_SCHEMA.md) from items/*.jsonl,
validates every record and note type against ladder_schema (the [R9]
feedback-invariant lint included - the build hard-fails rather than emit an
item or template without a feedback step), creates the ladder note types
from ladder_notetypes.py plus stock-Cloze faded notes, tags each note
mechanically per the schema's "Tagging" section (rung::/cluster::/
cfa::topic::/interactivity::/aig::*, no schema change - tags ride native
sync), and exports cfa_ladder.apkg.

The deck ("CFA Level 1 Speedrun") and preset ("CFA Speedrun") names match
make_cfa_deck.py so importing both merges instead of forking.

Usage (from desktop/, after a build):
    PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/build_ladder_deck.py \
        [--items GLOB] [--output PATH] [--no-self-explain]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ladder_notetypes  # type: ignore[import-not-found]  # noqa: E402
import ladder_schema  # type: ignore[import-not-found]  # noqa: E402

from anki.collection import Collection  # noqa: E402
from anki.import_export_pb2 import ExportAnkiPackageOptions  # noqa: E402

# Same names as make_cfa_deck.py: importing the ladder next to the Phase 1
# sample deck merges into one deck + one preset instead of forking them.
DECK_NAME = "CFA Level 1 Speedrun"
PRESET_NAME = "CFA Speedrun"

HERE = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--items",
        default=str(HERE / "items" / "*.jsonl"),
        help="glob of item JSONL files (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=str(HERE / "cfa_ladder.apkg"),
        help="output .apkg path (default: %(default)s)",
    )
    parser.add_argument(
        "--no-self-explain",
        action="store_true",
        help="leave SelfExplainPrompt empty so the ord-1 solve variant "
        "generates no cards ([R16] ablation OFF arm)",
    )
    return parser.parse_args()


def load_items(pattern: str) -> tuple[list[dict], list[str]]:
    """All records matching the glob, plus hard-fail messages
    ("file:line: problem") for anything that is not a valid schema item."""
    paths = sorted(glob.glob(pattern))
    if not paths:
        return [], [f"no item files match {pattern!r}"]
    records: list[dict] = []
    failures: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    failures.append(f"{path}:{lineno}: invalid JSON: {exc}")
                    continue
                errors = ladder_schema.validate_item(record)
                if errors:
                    label = (
                        record.get("title") if isinstance(record, dict) else None
                    ) or "<untitled>"
                    failures.extend(
                        f"{path}:{lineno} ({label}): {error}" for error in errors
                    )
                else:
                    records.append(record)
    return records, failures


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

    records, failures = load_items(args.items)
    if failures:
        print("refusing to build - invalid item records:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)

    # [R9] template lint for the custom note types, before touching a
    # collection; the stock cloze templates are linted below once loaded.
    lint_failures = [
        f"{spec['name']}: {error}"
        for spec in ladder_notetypes.NOTETYPES
        for error in ladder_schema.lint_notetype_feedback(
            spec["templates"], feedback_fields=("Rationale",)
        )
    ]
    if lint_failures:
        print(
            "refusing to build - notetype failed the feedback lint [R9]:",
            file=sys.stderr,
        )
        for failure in lint_failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "collection.anki2"))

        # The faded rung deliberately rides the stock Cloze note type, so
        # the engine's cloze card generation / template ordinals do the
        # fading. A fresh collection is English, hence "Cloze".
        cloze_nt = col.models.by_name(ladder_notetypes.STOCK_CLOZE_NOTETYPE_NAME)
        assert cloze_nt is not None, "stock Cloze notetype missing"
        text_field = cloze_nt["flds"][0]["name"]  # "Text"
        back_extra_field = cloze_nt["flds"][1]["name"]  # "Back Extra"
        cloze_lint = ladder_schema.lint_notetype_feedback(
            cloze_nt["tmpls"], feedback_fields=(back_extra_field,)
        )
        if cloze_lint:
            print(
                "refusing to build - stock cloze failed the feedback lint [R9]:",
                file=sys.stderr,
            )
            for failure in cloze_lint:
                print(f"  {failure}", file=sys.stderr)
            sys.exit(1)

        deck_id = col.decks.id(DECK_NAME)
        assert deck_id is not None

        notetype_for_kind = {
            kind: create_notetype(col, spec)
            for kind, spec in ladder_notetypes.NOTETYPE_FOR_KIND.items()
        }

        counts: Counter[str] = Counter()
        for item in records:
            kind = item["kind"]
            if kind == "cloze":
                note = col.new_note(cloze_nt)
                text, back_extra = ladder_notetypes.faded_cloze_fields(item)
                note[text_field] = text
                note[back_extra_field] = back_extra
            else:
                note = col.new_note(notetype_for_kind[kind])
                if kind == "worked":
                    fields = ladder_notetypes.worked_note_fields(item)
                elif kind == "mcq":
                    fields = ladder_notetypes.solve_note_fields(
                        item, include_self_explain=not args.no_self_explain
                    )
                else:
                    fields = ladder_notetypes.compare_note_fields(item)
                for name, value in fields.items():
                    note[name] = value
            note.tags = ladder_schema.tags_for_item(item)
            col.add_note(note, deck_id)
            counts[kind] += 1

        # Same named-preset trick as make_cfa_deck.py: a named preset
        # survives import (the default preset deliberately does not), so
        # importing can never clobber a user's own defaults. schema11 JSON
        # keys are camelCase.
        conf = col.decks.add_config(PRESET_NAME)
        conf["contrastScheduling"] = True
        conf["contrastTagPrefix"] = "cluster::"
        # Empty = legacy ungated contrast (every cluster eligible for forced
        # adjacency) until the computed confusability pass has enough revlog
        # data to mine; the pass writes confusable::high markers and flips
        # this to that tag ([R18]).
        conf["contrastConfusableTag"] = ""
        # Default-off per the plan (PHASE2_PLAN_V2 M5): the user opts into
        # the fade ladder via deck options; the tags alone are inert.
        conf["fadeEnabled"] = False
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
        total = sum(counts.values())
        col.close()

    breakdown = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    print(f"wrote {total} notes ({breakdown}) to {output}")


if __name__ == "__main__":
    main()
