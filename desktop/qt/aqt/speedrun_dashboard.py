# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Anki Speedrun: the CFA Level I readiness dashboard.

Hosts the shared SvelteKit ``dashboard`` page (Memory / Performance /
Readiness gauges, per-topic table, coverage map and the give-up rule) in an
API-enabled web view. The same page ships to the Android build inside the
rsdroid ``.aar``, so both apps render the identical dashboard from the
identical engine data.
"""

from __future__ import annotations

import aqt
import aqt.main
from aqt.qt import QDialog, QDialogButtonBox, Qt, QVBoxLayout, qconnect
from aqt.utils import disable_help_button, restoreGeom, saveGeom
from aqt.webview import AnkiWebView, AnkiWebViewKind


class SpeedrunDashboard(QDialog):
    """Whole-collection readiness dashboard for the configured exam."""

    GEOM_KEY = "speedrunDashboard"

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        QDialog.__init__(self, mw, Qt.WindowType.Window)
        mw.garbage_collect_on_dialog_finish(self)
        self.mw = mw
        self.setWindowTitle("CFA Level 1 - Readiness Dashboard")
        self.setMinimumSize(880, 640)
        disable_help_button(self)
        restoreGeom(self, self.GEOM_KEY, default_size=(1000, 800))

        self.web = AnkiWebView(kind=AnkiWebViewKind.SPEEDRUN_DASHBOARD)
        self.web.set_bridge_command(lambda _cmd: False, self)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(buttons.rejected, self.reject)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.web.load_sveltekit_page("dashboard")
        self.show()
        self.activateWindow()

    def reject(self) -> None:
        self.web.cleanup()
        self.web = None  # type: ignore[assignment]
        saveGeom(self, self.GEOM_KEY)
        aqt.dialogs.markClosed("SpeedrunDashboard")
        QDialog.reject(self)

    def closeWithCallback(self, callback) -> None:  # noqa: ANN001
        self.reject()
        callback()

    def reopen(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.activateWindow()
        self.raise_()


class SpeedrunConceptMap(QDialog):
    """Force-directed knowledge map for a deck (or the whole collection).

    One node per tag, edges where two tags co-occur on a note; nodes are
    coloured by answer difficulty (Again/Hard share) with a toggle to colour
    by FSRS recall. The page is the shared SvelteKit ``concept-graph`` route,
    so the identical map ships to Android inside the rsdroid ``.aar``.
    """

    GEOM_KEY = "speedrunConceptMap"

    def __init__(self, mw: aqt.main.AnkiQt, deck_id: int = 0) -> None:
        QDialog.__init__(self, mw, Qt.WindowType.Window)
        mw.garbage_collect_on_dialog_finish(self)
        self.mw = mw
        self.setMinimumSize(880, 640)
        disable_help_button(self)
        restoreGeom(self, self.GEOM_KEY, default_size=(1100, 800))

        self.web = AnkiWebView(kind=AnkiWebViewKind.SPEEDRUN_CONCEPT_MAP)
        self.web.set_bridge_command(lambda _cmd: False, self)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(buttons.rejected, self.reject)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self._load_deck(deck_id)
        self.show()
        self.activateWindow()

    def _load_deck(self, deck_id: int) -> None:
        self._deck_id = deck_id
        name = "collection"
        if deck_id and self.mw.col:
            name = self.mw.col.decks.name(deck_id)  # type: ignore[arg-type]
        self.setWindowTitle(f"Concept map - {name}")
        self.web.load_sveltekit_page(f"concept-graph/{deck_id}")

    def reject(self) -> None:
        self.web.cleanup()
        self.web = None  # type: ignore[assignment]
        saveGeom(self, self.GEOM_KEY)
        aqt.dialogs.markClosed("SpeedrunConceptMap")
        QDialog.reject(self)

    def closeWithCallback(self, callback) -> None:  # noqa: ANN001
        self.reject()
        callback()

    def reopen(self, mw: aqt.main.AnkiQt, deck_id: int = 0) -> None:
        if deck_id != self._deck_id:
            self._load_deck(deck_id)
        self.activateWindow()
        self.raise_()
