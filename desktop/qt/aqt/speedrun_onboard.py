# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Anki Speedrun: the "Prepare this deck for Speedrun" onboarding action (M5).

Desktop-only wrapper around ``tools/speedrun/onboard.py``: a deck gear-menu
action (nothing runs automatically on import) that proposes topic / cluster /
rung / interactivity tags plus behaviorally-validated confusability markers
for a BYO deck, PREVIEWS them for approval, and applies the accepted subset
as one undoable pylib note update. Tags only - no note content is ever
modified, and nothing persists unless the user clicks Apply.

Gating mirrors ``speedrun_assistant.py``: the synced collection-config flag
``speedrun:byoOnboardingEnabled`` defaults to OFF; while it is off the menu
item stays visible but the action politely explains the toggle and returns.
The AI backend for the optional topic fill follows ``speedrun:aiBackend``.

All collection reads happen on the main thread before the (potentially
AI-calling) proposal runs in a background task. The revlog is handed to the
proposal engine as pre-read rows rather than the database path: the open
collection holds an exclusive sqlite lock, so a second read-only connection
could not see it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anki.decks import DeckId
from anki.utils import ids2str, strip_html
from aqt.qt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import disable_help_button, restoreGeom, saveGeom, showInfo, tooltip

if TYPE_CHECKING:
    import aqt.main
    from anki.collection import Collection

# Synced collection-config keys ("speedrun:..." style, see speedrun_assistant).
CONFIG_ENABLED = "speedrun:byoOnboardingEnabled"
CONFIG_AI_BACKEND = "speedrun:aiBackend"
KNOWN_BACKENDS = ("", "mock", "claude-cli", "openai-compatible")

#: Confusability mining is only attempted with a meaningful history.
MIN_REVIEWS_FOR_CONFUSABILITY = 200
#: Preview-table snippet length for note fronts.
FRONT_SNIPPET_CHARS = 80
_NOTE_CHUNK = 500

_import_lock = threading.Lock()
_modules: dict[str, Any] | None = None
_import_error: str | None = None


def _speedrun_tools_dir() -> Path | None:
    """<repo>/tools/speedrun in a dev checkout, else None."""
    candidate = Path(__file__).resolve().parents[2] / "tools" / "speedrun"
    if (candidate / "onboard.py").exists():
        return candidate
    return None


def _onboard_modules() -> dict[str, Any] | None:
    """Import (once) onboard + the assistant backend factory; None if absent."""
    global _modules, _import_error
    with _import_lock:
        if _modules is not None or _import_error is not None:
            return _modules
        tools_dir = _speedrun_tools_dir()
        if tools_dir is None:
            _import_error = "tools/speedrun not present (not a dev checkout)"
            return None
        import sys

        if str(tools_dir) not in sys.path:
            sys.path.append(str(tools_dir))
        try:
            import onboard  # type: ignore[import-not-found]
            from assistant import core  # type: ignore[import-not-found]
        except Exception as exc:  # degrade, never break the deck browser
            _import_error = f"onboarding package failed to import: {exc}"
            return None
        _modules = {"onboard": onboard, "core": core}
        return _modules


# ---------------------------------------------------------------------------
# Collection reads (main thread, read-only)
# ---------------------------------------------------------------------------


def _deck_note_infos(col: Collection, onboard_mod: Any, dids: list[int]) -> list[Any]:
    """The deck's notes as NoteInfo: front/back = first two fields, HTML
    stripped; tags as a list."""
    nids = col.db.list(f"select distinct nid from cards where did in {ids2str(dids)}")
    infos: list[Any] = []
    for start in range(0, len(nids), _NOTE_CHUNK):
        chunk = nids[start : start + _NOTE_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        for nid, flds, tags in col.db.all(
            f"select id, flds, tags from notes where id in ({placeholders})", *chunk
        ):
            fields = str(flds).split("\x1f")
            infos.append(
                onboard_mod.NoteInfo(
                    note_id=int(nid),
                    front=strip_html(fields[0] if fields else "").strip(),
                    back=strip_html(fields[1] if len(fields) > 1 else "").strip(),
                    tags=str(tags).split(),
                )
            )
    infos.sort(key=lambda info: info.note_id)
    return infos


def _deck_revlog_rows(col: Collection, dids: list[int]) -> list[dict[str, Any]] | None:
    """The deck's graded reviews, or None below the cheap volume guard."""
    rows = col.db.all(
        "select r.id, r.ease, c.nid from revlog r join cards c on c.id = r.cid"
        f" where c.did in {ids2str(dids)} and r.ease >= 1 order by r.id"
    )
    if len(rows) < MIN_REVIEWS_FOR_CONFUSABILITY:
        return None
    return [{"note_id": nid, "button": ease, "id_ms": rid} for rid, ease, nid in rows]


# ---------------------------------------------------------------------------
# Preview dialog
# ---------------------------------------------------------------------------


class OnboardPreviewDialog(QDialog):
    """Per-note proposed tags with evidence + confidence; nothing applies
    until the user accepts, and the apply is a single undoable op."""

    GEOM_KEY = "speedrunOnboard"

    def __init__(self, mw: aqt.main.AnkiQt, onboard_mod: Any, proposal: Any) -> None:
        QDialog.__init__(self, mw, Qt.WindowType.Window)
        mw.garbage_collect_on_dialog_finish(self)
        self.mw = mw
        self._onboard = onboard_mod
        self._proposal = proposal
        self.setWindowTitle("Prepare for Speedrun - preview")
        self.setMinimumSize(720, 480)
        disable_help_button(self)
        restoreGeom(self, self.GEOM_KEY, default_size=(900, 640))

        summary = proposal.summary
        header = QLabel(
            f"{summary['n_notes_with_proposals']} of {summary['n_notes']} notes "
            f"have proposals ({summary['n_tag_proposals']} tags). "
            f"Confusability: {proposal.confusability}\n"
            "Tags are only added, never removed; applying is one undoable step."
        )
        header.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Note", "Proposed tag", "Confidence", "Evidence"])
        self.tree.setRootIsDecorated(True)
        for note_proposal in proposal.notes:
            snippet = note_proposal.front[:FRONT_SNIPPET_CHARS] or "(empty front)"
            parent = QTreeWidgetItem(self.tree, [snippet, "", "", ""])
            parent.setFlags(
                parent.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            for tag_proposal in note_proposal.tags:
                child = QTreeWidgetItem(
                    parent,
                    [
                        "",
                        tag_proposal.tag,
                        f"{tag_proposal.confidence:.2f}",
                        tag_proposal.evidence,
                    ],
                )
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    (note_proposal.note_id, tag_proposal.tag),
                )
            parent.setExpanded(True)
        for column, width in enumerate((280, 220, 90)):
            self.tree.setColumnWidth(column, width)

        select_all = QPushButton("Select all")
        select_none = QPushButton("Select none")
        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        qconnect(select_all.clicked, lambda: self._set_all(Qt.CheckState.Checked))
        qconnect(select_none.clicked, lambda: self._set_all(Qt.CheckState.Unchecked))
        qconnect(apply_button.clicked, self._apply)
        qconnect(cancel_button.clicked, self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(select_all)
        buttons.addWidget(select_none)
        buttons.addStretch()
        buttons.addWidget(apply_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout()
        layout.addWidget(header)
        layout.addWidget(self.tree)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _tag_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for row in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(row)
            assert parent is not None
            items.extend(parent.child(i) for i in range(parent.childCount()))
        return items

    def _set_all(self, state: Qt.CheckState) -> None:
        for item in self._tag_items():
            item.setCheckState(0, state)

    def _accepted_pairs(self) -> set[tuple[int, str]]:
        return {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in self._tag_items()
            if item.checkState(0) == Qt.CheckState.Checked
        }

    def _apply(self) -> None:
        accepted = self._accepted_pairs()
        if not accepted:
            tooltip("Nothing selected.", parent=self)
            return
        col = self.mw.col
        assert col is not None
        # One update_notes call inside apply() = one undoable step; tags
        # are only added, note content is never touched.
        changed = self._onboard.apply(col, self._proposal, accepted)
        self.mw.update_undo_actions()
        tooltip(f"Added tags to {changed} notes (undoable)", parent=self.mw)
        self.accept()

    def done(self, result: int) -> None:
        saveGeom(self, self.GEOM_KEY)
        QDialog.done(self, result)


# ---------------------------------------------------------------------------
# Entry point (deck gear menu)
# ---------------------------------------------------------------------------


def show_onboard_dialog(mw: aqt.main.AnkiQt, deck_id: int) -> None:
    """Propose onboarding tags for one deck and preview them for approval."""
    col = mw.col
    if col is None:
        return
    if not col.get_config(CONFIG_ENABLED, default=False):
        showInfo(
            "BYO-deck onboarding is turned off (it never runs automatically).\n\n"
            "To enable it, open Dashboard \u2192 AI assistant settings and turn "
            f'on "{CONFIG_ENABLED}", then run this action again.',
            parent=mw,
        )
        return
    modules = _onboard_modules()
    if modules is None:
        showInfo(f"Onboarding is unavailable: {_import_error}", parent=mw)
        return
    onboard_mod = modules["onboard"]

    backend_name = col.get_config(CONFIG_AI_BACKEND, default="")
    if backend_name not in KNOWN_BACKENDS:
        backend_name = ""
    backend = modules["core"].make_backend(backend_name or None)

    dids = [int(did) for did in col.decks.deck_and_child_ids(DeckId(deck_id))]
    notes = _deck_note_infos(col, onboard_mod, dids)
    if not notes:
        showInfo("This deck has no notes.", parent=mw)
        return
    revlog_rows = _deck_revlog_rows(col, dids)

    def work() -> Any:
        return onboard_mod.propose(
            notes,
            list(onboard_mod.CFA_TOPICS),
            backend=backend,
            revlog=revlog_rows,
        )

    def on_done(future: Any) -> None:
        try:
            proposal = future.result()
        except Exception as exc:
            showInfo(f"Onboarding failed: {exc}", parent=mw)
            return
        if not proposal.notes:
            showInfo(
                "No confident tag proposals for this deck (the proposers "
                "abstain rather than guess).\n\n"
                f"Confusability: {proposal.confusability}",
                parent=mw,
            )
            return
        OnboardPreviewDialog(mw, onboard_mod, proposal).show()

    # Reads are done; the proposal (which may call the AI backend) runs off
    # the main thread without holding the collection.
    mw.taskman.with_progress(
        work, on_done, parent=mw, label="Analyzing deck\u2026", uses_collection=False
    )
