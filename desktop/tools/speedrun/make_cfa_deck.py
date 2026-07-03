# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the CFA Level I sample deck (Anki Speedrun).

Creates ``cfa_level1_sample.apkg`` from the curated cards in
``cfa_sample_cards.py``, carrying the two-level tag taxonomy
(``cfa::topic::*`` + ``cluster::*``) that powers contrast scheduling,
the mastery RPC, and the dashboard. Both the desktop app and AnkiDroid
import this file, so one deck exercises the shared engine end to end.

Usage (from desktop/, after a build):
    PYTHONPATH=out/pylib out/pyenv/bin/python tools/speedrun/make_cfa_deck.py [output.apkg]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cfa_sample_cards import CARDS  # type: ignore[import-not-found]  # noqa: E402

from anki.collection import Collection  # noqa: E402
from anki.import_export_pb2 import ExportAnkiPackageOptions  # noqa: E402

# Distinct from a plain "CFA Level 1" so importing next to an existing
# collection stays cleanly additive.
DECK_NAME = "CFA Level 1 Speedrun"


def main() -> None:
    output = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path(__file__).parent / "cfa_level1_sample.apkg"
    ).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "collection.anki2"))

        deck_id = col.decks.id(DECK_NAME)
        assert deck_id is not None
        notetype = col.models.by_name("Basic")
        assert notetype is not None

        for front, back, topic, cluster in CARDS:
            note = col.new_note(notetype)
            note["Front"] = front
            note["Back"] = back
            note.tags = [f"cfa::topic::{topic}"]
            if cluster:
                note.tags.append(cluster)
            col.add_note(note, deck_id)

        # enable contrast scheduling on a dedicated preset so importing the
        # deck demos the feature out of the box. (A named preset survives
        # import with deck configs; the default preset deliberately does not,
        # so the import can never clobber a user's own defaults.)
        conf = col.decks.add_config("CFA Speedrun")
        conf["contrastScheduling"] = True
        conf["contrastTagPrefix"] = "cluster::"
        # R18: ship ungated (empty marker = legacy behaviour). The sample
        # deck has no revlog history yet, so the computed confusability pass
        # has nothing to mine; once it runs it writes confusable::high tags
        # and this key should be set to "confusable::high".
        conf["contrastConfusableTag"] = ""
        # SPOV 2 fade ladder ships default-OFF; the user opts in via deck
        # options after setting an exam date on the dashboard.
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
        count = len(CARDS)
        col.close()

    print(f"wrote {count} notes to {output}")


if __name__ == "__main__":
    main()
