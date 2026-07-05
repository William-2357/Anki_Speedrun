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


def test_topic_mastery_tag_map():
    "Anki Speedrun: the user tag->topic map and the aig::ungraded exclusion."
    col = getEmptyCol()
    for front, tags in [
        ("bond pricing", "finance::bonds"),
        ("machine-made", "cfa::topic::quant aig::ungraded"),
    ]:
        note = col.newNote()
        note["Front"] = front
        note.tags = tags.split()
        col.addNote(note)

    # without a map (existing callers pass nothing): the flat tag stays
    # unmapped and is surfaced per raw tag rather than guessed at
    plain = col.topic_mastery()
    assert plain.total_cards == 2
    assert plain.cards_without_topic == 1
    assert [t.topic for t in plain.unmapped_tags] == ["finance::bonds"]
    assert plain.ungraded_aig_cards == 1

    # a prefix mapping folds the flat-tagged note into the mapped topic
    response = col.topic_mastery(tag_topic_map={"finance": "fixed_income"})
    assert response.cards_without_topic == 0
    assert not response.unmapped_tags
    assert [t.topic for t in response.topics] == ["fixed_income"]
    assert response.topics[0].total_cards == 1
    # the ungraded AI-generated note stays out of every topic bucket but
    # remains visible in total_cards and the disclosure count
    assert response.total_cards == 2
    assert response.ungraded_aig_cards == 1


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


def test_get_readiness():
    "Anki Speedrun Phase 3: the Rust GetReadiness RPC is reachable from Python."
    col = getEmptyCol()
    note = col.newNote()
    note["Front"] = "duration"
    note.tags = ["cfa::topic::fixed_income"]
    col.addNote(note)

    # abstains by default, naming every missing input; numbers zeroed
    response = col.get_readiness()
    assert response.kind == response.ABSTAIN
    assert response.p_pass_low == 0.0
    assert response.p_pass_high == 0.0
    assert response.call == ""
    assert len(response.missing) >= 3
    assert response.evidence.topics_total == 10
    assert response.min_delayed_probes == 50
    # honesty contract renders even while abstaining
    assert response.best_next_topic

    # test mode emits a loudly-labelled wide band and keeps the gate list
    test = col.get_readiness(test_mode=True)
    assert test.kind == test.TEST
    assert test.p_pass_high - test.p_pass_low > 0.5
    assert test.missing
    assert any("TEST MODE" in reason for reason in test.reasons)

    # the tag→topic map feeds coverage/best-next attribution
    mapped = col.get_readiness(tag_topic_map={"finance": "fixed_income"})
    assert mapped.kind == mapped.ABSTAIN


def test_readiness_probe_outcomes():
    "Probe cards feed evidence; mastery excludes them (held-out hygiene)."
    col = getEmptyCol()
    probe = col.newNote()
    probe["Front"] = "probe mcq"
    probe.tags = [
        "probe::held_out",
        "probe::pool::performance",
        "cluster::fi::duration",
        "cfa::topic::fixed_income",
    ]
    col.addNote(probe)

    # the probe card never feeds the Memory gauge or coverage
    mastery = col.topic_mastery()
    assert mastery.held_out_probe_cards == 1
    assert not mastery.topics

    # answering the probe (never-studied cluster => counts as delayed)
    card = col.sched.getCard()
    col.sched.answerCard(card, 3)
    response = col.get_readiness()
    assert response.evidence.probe_answered_delayed == 1
    assert response.evidence.probe_correct == 1
    assert response.evidence.probe_never_studied == 1
    # probe answers are measurement, not study evidence
    assert response.evidence.graded_reviews == 0


def test_graphs():
    dir = tempfile.gettempdir()
    col = getEmptyCol()
    g = col.stats()
    rep = g.report()
    with open(os.path.join(dir, "test.html"), "w", encoding="UTF-8") as note:
        note.write(rep)
    return
