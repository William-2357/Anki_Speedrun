# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import os
import tempfile

from anki.collection import CardStats
from tests.shared import getEmptyCol


def test_stats():
    col = getEmptyCol()
    note = col.newNote()
    note["Front"] = "foo"
    col.addNote(note)
    c = note.cards()[0]
    # card stats
    card_stats = col.card_stats_data(c.id)
    assert card_stats.note_id == note.id
    c = col.sched.getCard()
    col.sched.answerCard(c, 3)
    col.sched.answerCard(c, 2)
    card_stats = col.card_stats_data(c.id)
    assert len(card_stats.revlog) == 2


def test_graphs_empty():
    col = getEmptyCol()
    assert col.stats().report()


def test_topic_mastery():
    "Anki Speedrun: the Rust TopicMastery RPC is reachable from Python."
    col = getEmptyCol()
    for front, tags in [
        ("duration", "cfa::topic::fixed_income cluster::fi::duration"),
        ("convexity", "cfa::topic::fixed_income"),
        ("bayes", "cfa::topic::quant"),
        ("stray", "untagged::reading"),
    ]:
        note = col.newNote()
        note["Front"] = front
        note.tags = tags.split()
        col.addNote(note)

    response = col.topic_mastery()
    assert response.total_cards == 4
    assert response.cards_without_topic == 1
    assert response.fsrs_enabled is False
    assert response.graded_reviews == 0
    assert [topic.topic for topic in response.topics] == ["fixed_income", "quant"]
    fixed_income = response.topics[0]
    assert fixed_income.total_cards == 2
    # nothing studied yet: the response must say so rather than fake a number
    assert fixed_income.studied_cards == 0
    assert fixed_income.average_retrievability == 0.0

    # graded reviews are counted collection-wide
    card = col.sched.getCard()
    col.sched.answerCard(card, 3)
    response = col.topic_mastery()
    assert response.graded_reviews == 1

    # scoped search + custom prefix
    scoped = col.topic_mastery(search="tag:cfa::topic::quant")
    assert scoped.total_cards == 1
    assert [topic.topic for topic in scoped.topics] == ["quant"]


def test_concept_graph():
    "Anki Speedrun: the Rust ConceptGraph RPC is reachable from Python."
    col = getEmptyCol()
    for front, tags in [
        ("duration", "cfa::topic::fixed_income cluster::fi::duration"),
        ("convexity", "cfa::topic::fixed_income cluster::fi::duration"),
        ("bayes", "cfa::topic::quant"),
    ]:
        note = col.newNote()
        note["Front"] = front
        note.tags = tags.split()
        col.addNote(note)

    graph = col.concept_graph()
    nodes_by_tag = {node.tag: node for node in graph.nodes}
    assert set(nodes_by_tag) == {
        "cfa::topic::fixed_income",
        "cluster::fi::duration",
        "cfa::topic::quant",
    }
    assert nodes_by_tag["cfa::topic::fixed_income"].card_count == 2
    # the two duration notes carry both fixed-income tags -> one edge of weight 2
    assert len(graph.edges) == 1
    assert graph.edges[0].note_count == 2

    # grading feeds the answer-difficulty signal
    card = col.sched.getCard()
    col.sched.answerCard(card, 1)  # Again
    graph = col.concept_graph()
    graded = [n for n in graph.nodes if n.graded_answers]
    assert graded and all(n.again_hard_answers == 1 for n in graded)


def test_graphs():
    dir = tempfile.gettempdir()
    col = getEmptyCol()
    g = col.stats()
    rep = g.report()
    with open(os.path.join(dir, "test.html"), "w", encoding="UTF-8") as note:
        note.write(rep)
    return
