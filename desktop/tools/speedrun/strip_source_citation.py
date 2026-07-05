# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Remove the on-card corpus source citation from an ALREADY-IMPORTED ladder
deck, and repurpose the Source field to the note's CFA topic tag.

Why: the deck builder (ladder_notetypes.py) was changed so future builds no
longer display the citation, but a deck already imported into a collection
keeps its baked-in note templates and field content. This one-shot migration
brings a live collection in line:

* Speedrun Worked / Solve MCQ / Compare note types (and any "+" import
  variants): strip the `{{#Source}}<div class="sr-source">...` block from
  every card template, and rewrite each note's Source field to the canonical
  `cfa::topic::<area>` tag (read from the note's own tags - never fabricated).
* Cloze ladder notes: strip the trailing `<small>...sr-source...</small>`
  citation the builder used to append to Back Extra.

Nothing else is touched: the user's own Basic/Cloze notes are left alone
(only fields that actually contain the `sr-source` marker are cleaned), the
review history/scheduling is untouched, and the Source *provenance* still
lives in the pipeline artifacts (items/generated.jsonl, validation_report).

Safety:
* the collection is BACKED UP first (a timestamped copy) unless --dry-run;
* opening fails loudly if Anki still has the collection open (SQLite lock) -
  close Anki first;
* --dry-run reports what would change and writes nothing.

The string transforms are pure functions (unit-tested); pylib is imported
lazily only when actually mutating a collection.

    python3 tools/speedrun/strip_source_citation.py --dry-run
    python3 tools/speedrun/strip_source_citation.py --collection <path>
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
from pathlib import Path
from typing import Any

DEFAULT_COLLECTION = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Anki2"
    / "User 1"
    / "collection.anki2"
)

SPEEDRUN_NOTETYPE_PREFIXES = ("Speedrun Worked", "Speedrun Solve", "Speedrun Compare")
TOPIC_TAG_RE = re.compile(r"(?:^|\s)(cfa::topic::[a-z_]+)", re.IGNORECASE)

#: The conditional source block the old templates rendered on the answer side.
_TEMPLATE_SOURCE_RE = re.compile(r"\n?\{\{#Source\}\}.*?\{\{/Source\}\}", re.DOTALL)
#: A bare source div (defensive; some variants may not use the conditional).
_TEMPLATE_SOURCE_DIV_RE = re.compile(r'\n?<div class="sr-source">.*?</div>', re.DOTALL)
#: The <small>...sr-source...</small> citation appended to a cloze Back Extra.
_FIELD_SOURCE_SMALL_RE = re.compile(
    r"(?:<br>\s*)?<small>\s*<span class=\"sr-source-ref\">.*?</small>",
    re.DOTALL,
)


def strip_source_from_template(afmt: str) -> str:
    """Remove the source block from a card template's answer side."""
    out = _TEMPLATE_SOURCE_RE.sub("", afmt)
    out = _TEMPLATE_SOURCE_DIV_RE.sub("", out)
    return out


def strip_source_from_field_html(html: str) -> str:
    """Remove the trailing citation <small> from a Back Extra field."""
    if "sr-source" not in html:
        return html
    out = _FIELD_SOURCE_SMALL_RE.sub("", html)
    # defensive: drop any lingering source spans if the wrapper differed
    out = re.sub(
        r'<span class="sr-source-(?:ref|passage|label)">.*?</span>',
        "",
        out,
        flags=re.DOTALL,
    )
    return out.rstrip()


def topic_tag_from_tags(tags: list[str]) -> str:
    """The note's canonical `cfa::topic::*` tag, or "" if it has none."""
    for tag in tags:
        m = TOPIC_TAG_RE.search(tag)
        if m:
            return m.group(1).lower()
    return ""


def _is_speedrun_notetype(name: str) -> bool:
    return any(name.startswith(p) for p in SPEEDRUN_NOTETYPE_PREFIXES)


def migrate(
    collection_path: Path, *, dry_run: bool, backup_dir: Path
) -> dict[str, Any]:
    """Open the collection and apply the migration. Returns a summary."""
    import anki.collection

    summary: dict[str, Any] = {
        "collection": str(collection_path),
        "dry_run": dry_run,
        "backup": None,
        "notetypes_templates_updated": [],
        "source_fields_rewritten": 0,
        "cloze_backextra_cleaned": 0,
        "notes_scanned": 0,
    }

    if not collection_path.exists():
        raise FileNotFoundError(f"no collection at {collection_path}")

    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"collection-before-source-strip-{stamp}.anki2"
        shutil.copy2(collection_path, backup)
        summary["backup"] = str(backup)

    try:
        col = anki.collection.Collection(str(collection_path))
    except Exception as exc:  # locked => Anki open
        raise RuntimeError(
            f"could not open the collection ({exc}). If Anki is running, "
            "close it fully and re-run."
        ) from exc

    try:
        # 1. Templates: strip the source block from every Speedrun notetype.
        for nt in col.models.all():
            if not _is_speedrun_notetype(nt["name"]):
                continue
            changed = False
            for tmpl in nt["tmpls"]:
                new_afmt = strip_source_from_template(tmpl["afmt"])
                if new_afmt != tmpl["afmt"]:
                    tmpl["afmt"] = new_afmt
                    changed = True
            if changed:
                summary["notetypes_templates_updated"].append(nt["name"])
                if not dry_run:
                    col.models.update_dict(nt)

        speedrun_mids = {
            nt["id"] for nt in col.models.all() if _is_speedrun_notetype(nt["name"])
        }

        # 2. Notes: rewrite Speedrun Source fields to the topic tag; clean the
        #    cloze Back Extra citation. Only notes that actually need it.
        for nid in col.find_notes(""):
            note = col.get_note(nid)
            summary["notes_scanned"] += 1
            dirty = False

            if note.mid in speedrun_mids and "Source" in note:
                tag = topic_tag_from_tags(list(note.tags))
                if note["Source"] != tag:
                    note["Source"] = tag
                    dirty = True
                    summary["source_fields_rewritten"] += 1

            for field_name in note.keys():
                if "sr-source" in note[field_name]:
                    cleaned = strip_source_from_field_html(note[field_name])
                    if cleaned != note[field_name]:
                        note[field_name] = cleaned
                        dirty = True
                        summary["cloze_backextra_cleaned"] += 1

            if dirty and not dry_run:
                col.update_note(note, skip_undo_entry=True)
    finally:
        col.close()

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection", default=str(DEFAULT_COLLECTION))
    parser.add_argument(
        "--backup-dir",
        default="",
        help="where to write the pre-migration backup (default: next to the "
        "collection)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    collection_path = Path(args.collection).expanduser()
    backup_dir = (
        Path(args.backup_dir).expanduser()
        if args.backup_dir
        else collection_path.parent / "speedrun-backups"
    )

    summary = migrate(collection_path, dry_run=args.dry_run, backup_dir=backup_dir)

    print(f"collection: {summary['collection']}")
    if summary["backup"]:
        print(f"backup:     {summary['backup']}")
    print(f"notes scanned: {summary['notes_scanned']}")
    print(
        "templates updated: "
        + (", ".join(summary["notetypes_templates_updated"]) or "none")
    )
    print(f"Source fields -> topic tag: {summary['source_fields_rewritten']}")
    print(f"cloze Back Extra cleaned:   {summary['cloze_backextra_cleaned']}")
    if summary["dry_run"]:
        print("(dry run - nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
