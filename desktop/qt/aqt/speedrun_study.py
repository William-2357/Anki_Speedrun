# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Anki Speedrun: study one CFA topic on demand.

The main reviewer studies a whole deck; this lets the learner review just the
cards for one of the 10 CFA topics (or, indirectly, a mapped tag) without
hand-building a filtered deck. It reuses Anki's own filtered-deck engine (no
scheduler changes): it (re)builds a per-topic filtered deck from a
``tag:cfa::topic::<id>`` search - OR'd with any raw tags the user mapped onto
that topic on the dashboard (``speedrun:tagTopicMap``) - and drops the learner
straight into review. ``reschedule=True`` means answers count normally and the
cards' real due dates advance, exactly like studying them in place.

Desktop-only (Qt). Entry points: the deck browser's gear menu
("Study a CFA topic...") and the dashboard's per-topic "Study" button (routed
through the dashboard web view's bridge).
"""

from __future__ import annotations

import aqt
import aqt.main
from anki.collection import Collection
from anki.decks import DeckId
from anki.decks_pb2 import Deck as DeckProto
from aqt.qt import QAction, QCursor, QMenu
from aqt.utils import showInfo, tooltip, tr

#: Weakest-recall-first study order for a focused topic session; degrades
#: gracefully without FSRS.
_ORDER_RETRIEVABILITY_ASCENDING = (
    DeckProto.Filtered.SearchTerm.Order.RETRIEVABILITY_ASCENDING
)

#: The 10 CFA Level I topics: canonical id (the `cfa::topic::<id>` suffix and
#: the dashboard topic id) -> display name. Mirrors rslib blueprint.rs and
#: ts/routes/dashboard/cfa_weights_2026.json.
CFA_TOPICS: tuple[tuple[str, str], ...] = (
    ("ethics", "Ethical & Professional Standards"),
    ("quantitative_methods", "Quantitative Methods"),
    ("economics", "Economics"),
    ("financial_statement_analysis", "Financial Statement Analysis"),
    ("corporate_issuers", "Corporate Issuers"),
    ("equity_investments", "Equity Investments"),
    ("fixed_income", "Fixed Income"),
    ("derivatives", "Derivatives"),
    ("alternative_investments", "Alternative Investments"),
    ("portfolio_management", "Portfolio Management"),
)
_TOPIC_NAMES = dict(CFA_TOPICS)
TOPIC_TAG_PREFIX = "cfa::topic::"
TAG_TOPIC_MAP_KEY = "speedrun:tagTopicMap"
#: Cards per session; generous so a topic session is never truncated.
_CARD_LIMIT = 500


def topic_display_name(topic_id: str) -> str:
    return _TOPIC_NAMES.get(topic_id, topic_id.replace("_", " ").title())


def build_topic_search(col: Collection, topic_id: str) -> str:
    """The filtered-deck search for a topic: the canonical tag OR'd with any
    raw tags the user mapped onto this topic on the dashboard."""
    tags = [f"{TOPIC_TAG_PREFIX}{topic_id}"]
    mapping = col.get_config(TAG_TOPIC_MAP_KEY, {}) or {}
    if isinstance(mapping, dict):
        for raw_tag, mapped_topic in mapping.items():
            if mapped_topic == topic_id and isinstance(raw_tag, str) and raw_tag:
                tags.append(raw_tag)
    # de-dup, preserve order, and search by tag (matches subtags too via *)
    seen: set[str] = set()
    clauses = []
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        clauses.append(f'"tag:{tag}" OR "tag:{tag}::*"')
    return "(" + " OR ".join(clauses) + ")"


def _filtered_deck_id_for(col: Collection, name: str) -> DeckId:
    """Reuse an existing filtered deck of this name, else 0 (create new)."""
    existing = col.decks.id_for_name(name)
    if existing is not None and col.decks.is_filtered(existing):
        return DeckId(existing)
    return DeckId(0)


def study_topic(mw: aqt.main.AnkiQt, topic_id: str) -> None:
    """(Re)build the per-topic filtered deck and enter review."""
    col = mw.col
    if col is None:
        return
    name = f"CFA topic: {topic_display_name(topic_id)}"
    search = build_topic_search(col, topic_id)

    if not col.find_cards(search):
        showInfo(tr.qt_misc_speedrun_study_topic_empty(), parent=mw)
        return

    deck = col.sched.get_or_create_filtered_deck(
        deck_id=_filtered_deck_id_for(col, name)
    )
    deck.name = name
    deck.config.reschedule = True
    del deck.config.search_terms[:]
    term = deck.config.search_terms.add()
    term.search = search
    term.limit = _CARD_LIMIT
    term.order = _ORDER_RETRIEVABILITY_ASCENDING

    changes = col.sched.add_or_update_filtered_deck(deck)
    did = DeckId(changes.id)

    col.decks.select(did)
    mw.update_undo_actions()
    # Enter review directly. Do NOT call mw.reset() here: it starts an async
    # deck-browser re-render whose callback lands after the reviewer has taken
    # over mw.web, replacing the card with the deck list while still in the
    # "review" state. The reviewer's bridge then rejects the deck list's
    # `open:` clicks ("unrecognized anki link: open:<id>") and the screen looks
    # frozen. The reviewer rebuilds its own queue on show(), so no reset is
    # needed to enter review correctly.
    mw.moveToState("review")


def populate_topic_menu(menu: QMenu, mw: aqt.main.AnkiQt) -> None:
    """Add the 10 topic actions to ``menu``.

    Studying is launched on a fresh event-loop iteration via ``single_shot``,
    never straight from the ``triggered`` handler. Entering review is a
    main-window state change (and, for an empty topic, a modal dialog); doing
    that from inside a menu's native tracking session froze the main window on
    macOS. Deferring runs it after the menu has closed.
    """
    for topic_id, display in CFA_TOPICS:
        action = QAction(display, menu)
        action.triggered.connect(
            lambda _checked=False, tid=topic_id: mw.progress.single_shot(
                0, lambda: study_topic(mw, tid)
            )
        )
        menu.addAction(action)


def choose_and_study_topic(mw: aqt.main.AnkiQt) -> None:
    """Pop a standalone menu of the 10 topics and study the chosen one.

    The deck browser's gear menu embeds these as a native submenu (see
    ``populate_topic_menu``); this standalone popup is kept for other callers.
    """
    menu = QMenu(mw)
    populate_topic_menu(menu, mw)
    menu.popup(QCursor.pos())


def handle_dashboard_command(mw: aqt.main.AnkiQt, cmd: str) -> bool:
    """Bridge handler for the dashboard 'Study' buttons.

    Recognizes ``speedrunStudyTopic:<topic_id>``; returns True when handled so
    the web view's default bridge does nothing else.
    """
    prefix = "speedrunStudyTopic:"
    if not cmd.startswith(prefix):
        return False
    topic_id = cmd[len(prefix) :].strip()
    if topic_id in _TOPIC_NAMES:
        # Defer out of the web view's bridge callback: enter review on a fresh
        # event-loop iteration (after the dashboard dialog has closed) rather
        # than switching main-window state from inside the bridge handler.
        mw.progress.single_shot(0, lambda: study_topic(mw, topic_id))
    else:
        tooltip("Unknown topic", parent=mw)
    return True
