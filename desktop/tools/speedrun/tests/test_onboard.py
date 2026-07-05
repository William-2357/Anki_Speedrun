# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""M5 tests: the BYO-deck onboarding proposal engine (onboard.py).

The invariants under test mirror the plan: deterministic-first with honest
abstention (lexicon margin rule, singleton clusters dropped, plain cards get
no rung), the AI pass fills only lexicon abstentions and obeys the 0.6
confidence floor, confusability integrates the validated miner and abstains
without a revlog, missing-rung generation is OFF by default and never flips
``aig::graded``, ``apply()`` adds tags through one ``update_notes`` call,
and every proposal carries evidence + confidence. No Qt, no pylib.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import onboard  # noqa: E402
from aig import confusability  # noqa: E402
from assistant import core  # noqa: E402

TOPICS = list(onboard.CFA_TOPICS)


def note(nid: int, front: str, back: str = "", tags: list[str] | None = None):
    return onboard.NoteInfo(note_id=nid, front=front, back=back, tags=tags or [])


def tags_for(proposal: onboard.OnboardProposal, nid: int) -> list[str]:
    for np in proposal.notes:
        if np.note_id == nid:
            return [tp.tag for tp in np.tags]
    return []


def proposal_for(
    proposal: onboard.OnboardProposal, nid: int, tag_prefix: str
) -> onboard.TagProposal | None:
    for np in proposal.notes:
        if np.note_id == nid:
            for tp in np.tags:
                if tp.tag.startswith(tag_prefix):
                    return tp
    return None


class CannedBackend:
    """Answers every completion with one fixed JSON reply; records prompts."""

    name = "canned"

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.reply)


class RaisingBackend:
    name = "raising"

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        raise RuntimeError("backend outage")


# ---------------------------------------------------------------------------
# Topic lexicon + margin rule
# ---------------------------------------------------------------------------


class TopicLexiconTests(unittest.TestCase):
    def test_clear_hit_proposed_with_evidence(self) -> None:
        proposal = onboard.propose(
            [note(1, "What is the Macaulay duration of a bond?")], TOPICS
        )
        tp = proposal_for(proposal, 1, "cfa::topic::")
        self.assertIsNotNone(tp)
        self.assertEqual(tp.tag, "cfa::topic::fixed_income")
        self.assertEqual(tp.source, "lexicon")
        self.assertIn("duration", tp.evidence)
        self.assertIn("score", tp.evidence)
        self.assertGreaterEqual(tp.confidence, 0.6)

    def test_no_keywords_abstains(self) -> None:
        proposal = onboard.propose([note(1, "How do you bake sourdough?")], TOPICS)
        self.assertEqual(tags_for(proposal, 1), [])

    def test_tie_abstains(self) -> None:
        # gdp (economics, 2) vs bond (fixed_income, 2): tied, no margin.
        proposal = onboard.propose([note(1, "GDP impact of a bond issue")], TOPICS)
        self.assertIsNone(proposal_for(proposal, 1, "cfa::topic::"))

    def test_margin_boundary_exactly_1_5x_proposes(self) -> None:
        # macaulay (3) vs gdp (2): 3 >= 1.5 * 2 exactly -> proposed.
        proposal = onboard.propose([note(1, "Macaulay measure and GDP")], TOPICS)
        tp = proposal_for(proposal, 1, "cfa::topic::")
        self.assertIsNotNone(tp)
        self.assertEqual(tp.tag, "cfa::topic::fixed_income")
        self.assertAlmostEqual(tp.confidence, 0.6)

    def test_single_weak_keyword_below_min_score_abstains(self) -> None:
        # "equity" alone weighs 1 < MIN_TOPIC_SCORE.
        proposal = onboard.propose([note(1, "Owners hold equity")], TOPICS)
        self.assertIsNone(proposal_for(proposal, 1, "cfa::topic::"))

    def test_existing_topic_tag_respected(self) -> None:
        proposal = onboard.propose(
            [note(1, "Macaulay duration of a bond?", tags=["cfa::topic::economics"])],
            TOPICS,
        )
        self.assertIsNone(proposal_for(proposal, 1, "cfa::topic::"))

    def test_restricted_topic_list_is_honoured(self) -> None:
        proposal = onboard.propose(
            [note(1, "Macaulay duration of a bond?")], ["economics"]
        )
        self.assertIsNone(proposal_for(proposal, 1, "cfa::topic::"))


# ---------------------------------------------------------------------------
# AI fill of lexicon abstentions
# ---------------------------------------------------------------------------


class AiFillTests(unittest.TestCase):
    ABSTAINED_FRONT = "That thing the central bank watches"

    def _run(self, reply: Any, notes: list[Any] | None = None):
        backend = CannedBackend(reply)
        proposal = onboard.propose(
            notes or [note(101, self.ABSTAINED_FRONT)],
            TOPICS,
            backend=backend,
        )
        return proposal, backend

    def test_fill_only_abstained_and_floor_kept(self) -> None:
        proposal, backend = self._run(
            {"suggestions": {"101": {"topic": "economics", "confidence": 0.6}}}
        )
        tp = proposal_for(proposal, 101, "cfa::topic::")
        self.assertIsNotNone(tp)
        self.assertEqual(tp.tag, "cfa::topic::economics")
        self.assertEqual(tp.source, "ai")
        self.assertIn("AI fill", tp.evidence)
        self.assertEqual(tp.confidence, 0.6)
        self.assertEqual(len(backend.prompts), 1)

    def test_below_floor_dropped(self) -> None:
        proposal, _ = self._run(
            {"suggestions": {"101": {"topic": "economics", "confidence": 0.59}}}
        )
        self.assertIsNone(proposal_for(proposal, 101, "cfa::topic::"))

    def test_floor_matches_tag_suggest(self) -> None:
        from assistant.tag_suggest import CONFIDENCE_FLOOR

        self.assertEqual(onboard.AI_CONFIDENCE_FLOOR, CONFIDENCE_FLOOR)
        self.assertEqual(onboard.AI_CONFIDENCE_FLOOR, 0.6)

    def test_unsure_and_invented_topics_dropped(self) -> None:
        for topic in ("unsure", "astrology"):
            proposal, _ = self._run(
                {"suggestions": {"101": {"topic": topic, "confidence": 0.99}}}
            )
            self.assertIsNone(proposal_for(proposal, 101, "cfa::topic::"), topic)

    def test_never_overrides_deterministic_proposal(self) -> None:
        # Note 7 is deterministic (duration+bond); the model tries to
        # relabel it anyway. Only the abstained note 101 may be filled.
        notes = [
            note(7, "Macaulay duration of a bond?"),
            note(101, self.ABSTAINED_FRONT),
        ]
        proposal, backend = self._run(
            {
                "suggestions": {
                    "7": {"topic": "economics", "confidence": 0.99},
                    "101": {"topic": "economics", "confidence": 0.9},
                }
            },
            notes,
        )
        self.assertEqual(
            proposal_for(proposal, 7, "cfa::topic::").tag,
            "cfa::topic::fixed_income",
        )
        self.assertEqual(
            proposal_for(proposal, 101, "cfa::topic::").tag,
            "cfa::topic::economics",
        )
        # Only the abstained note was sent to the model.
        facts = core._prompt_facts(backend.prompts[0])
        self.assertEqual([n["note_id"] for n in facts["notes"]], [101])

    def test_backend_off_is_deterministic_only(self) -> None:
        proposal = onboard.propose([note(101, self.ABSTAINED_FRONT)], TOPICS)
        self.assertEqual(tags_for(proposal, 101), [])
        self.assertIn("skipped", proposal.summary["ai"]["reason"])

    def test_backend_outage_never_an_error(self) -> None:
        proposal = onboard.propose(
            [note(101, self.ABSTAINED_FRONT), note(7, "Macaulay duration of a bond?")],
            TOPICS,
            backend=RaisingBackend(),
        )
        self.assertEqual(tags_for(proposal, 101), [])
        self.assertEqual(
            proposal_for(proposal, 7, "cfa::topic::").tag,
            "cfa::topic::fixed_income",
        )

    def test_offline_mock_backend_abstains_cleanly(self) -> None:
        # The assistant mock has no canned onboard_topics reply; the
        # grounded path abstains and the deterministic view stands.
        proposal = onboard.propose(
            [note(101, self.ABSTAINED_FRONT)],
            TOPICS,
            backend=core.MockAssistantBackend(),
        )
        self.assertEqual(tags_for(proposal, 101), [])
        self.assertEqual(proposal.summary["by_source"].get("ai", 0), 0)


# ---------------------------------------------------------------------------
# Cluster proposer
# ---------------------------------------------------------------------------


class ClusterTests(unittest.TestCase):
    def _fi_notes(self) -> list[Any]:
        return [
            note(1, "Macaulay duration of a coupon bond?"),
            note(2, "Modified duration of a coupon bond?"),
            note(3, "Convexity of a coupon bond?"),
        ]

    def test_shared_term_groups_at_least_two(self) -> None:
        proposal = onboard.propose(self._fi_notes(), TOPICS)
        tag1 = proposal_for(proposal, 1, "cluster::")
        tag2 = proposal_for(proposal, 2, "cluster::")
        self.assertIsNotNone(tag1)
        self.assertIsNotNone(tag2)
        self.assertEqual(tag1.tag, tag2.tag)
        self.assertEqual(tag1.tag, "cluster::fi::duration")
        self.assertIn("duration", tag1.evidence)
        self.assertEqual(tag1.source, "cluster")

    def test_singleton_gets_no_cluster(self) -> None:
        proposal = onboard.propose(self._fi_notes(), TOPICS)
        self.assertIsNone(proposal_for(proposal, 3, "cluster::"))

    def test_within_topic_only(self) -> None:
        # An economics note sharing the "duration" token must not join the
        # fixed-income cluster ([R8]: within-topic only).
        notes = self._fi_notes() + [
            note(4, "Elasticity duration of GDP shocks and inflation?")
        ]
        proposal = onboard.propose(notes, TOPICS)
        self.assertEqual(
            proposal_for(proposal, 4, "cfa::topic::").tag,
            "cfa::topic::economics",
        )
        self.assertIsNone(proposal_for(proposal, 4, "cluster::"))

    def test_abbreviation_convention(self) -> None:
        notes = [
            note(1, "FIFO inventory valuation under rising prices?"),
            note(2, "LIFO to FIFO inventory restatement?"),
        ]
        proposal = onboard.propose(notes, TOPICS)
        tp = proposal_for(proposal, 1, "cluster::")
        self.assertIsNotNone(tp)
        self.assertTrue(tp.tag.startswith("cluster::fsa::"), tp.tag)

    def test_existing_cluster_tag_respected(self) -> None:
        notes = self._fi_notes()
        notes[0].tags = ["cluster::fi::duration"]
        proposal = onboard.propose(notes, TOPICS)
        self.assertIsNone(proposal_for(proposal, 1, "cluster::"))


# ---------------------------------------------------------------------------
# Rung + interactivity heuristics
# ---------------------------------------------------------------------------


class RungTests(unittest.TestCase):
    def _rung(self, front: str, back: str = "") -> str | None:
        proposal = onboard.propose([note(1, front, back)], TOPICS)
        tp = proposal_for(proposal, 1, "rung::")
        return tp.tag if tp else None

    def test_cloze_markup_is_faded(self) -> None:
        self.assertEqual(
            self._rung("{{c1::Duration}} measures {{c2::rate}} sensitivity"),
            "rung::faded",
        )

    def test_step_wording_is_worked(self) -> None:
        self.assertEqual(
            self._rung("Compute NPV", "Step 1: discount. Step 2: sum."),
            "rung::worked",
        )

    def test_numbered_lines_are_worked(self) -> None:
        self.assertEqual(
            self._rung("Derive the answer", "1. discount flows\n2. add them"),
            "rung::worked",
        )

    def test_versus_framing_is_compare(self) -> None:
        self.assertEqual(
            self._rung("Duration versus convexity: which curves?"),
            "rung::compare",
        )

    def test_bare_numeric_answer_is_solve(self) -> None:
        self.assertEqual(
            self._rung("What is the bond's modified duration?", "6.18 years"),
            "rung::solve",
        )

    def test_mcq_shape_is_solve(self) -> None:
        self.assertEqual(
            self._rung(
                "The duration is closest to:\nA) 6.18\nB) 6.30\nC) 6.05",
                "A",
            ),
            "rung::solve",
        )

    def test_plain_flashcard_gets_no_rung(self) -> None:
        self.assertIsNone(
            self._rung(
                "What does duration measure?",
                "Price sensitivity to interest rates.",
            )
        )

    def test_existing_rung_tag_respected(self) -> None:
        proposal = onboard.propose(
            [note(1, "{{c1::x}} and {{c2::y}}", tags=["rung::worked"])], TOPICS
        )
        self.assertIsNone(proposal_for(proposal, 1, "rung::"))


class InteractivityTests(unittest.TestCase):
    def test_formula_plus_numbers_is_high(self) -> None:
        back = "FV = 1000 x (1.03)^10 = 1343.92"
        proposal = onboard.propose(
            [note(1, "FV of $1,000 at 6% semiannual?", back)], TOPICS
        )
        tp = proposal_for(proposal, 1, "interactivity::")
        self.assertIsNotNone(tp)
        self.assertEqual(tp.tag, "interactivity::high")
        self.assertIn("formula", tp.evidence)

    def test_definitional_card_abstains(self) -> None:
        proposal = onboard.propose(
            [note(1, "Define duration", "Price sensitivity to rates.")], TOPICS
        )
        self.assertIsNone(proposal_for(proposal, 1, "interactivity::"))

    def test_formula_without_numbers_abstains(self) -> None:
        proposal = onboard.propose(
            [note(1, "HPR formula?", "HPR = (end - begin + income) / begin")],
            TOPICS,
        )
        self.assertIsNone(proposal_for(proposal, 1, "interactivity::"))


# ---------------------------------------------------------------------------
# Confusability integration
# ---------------------------------------------------------------------------


def _synthetic_setup() -> tuple[list[Any], list[dict[str, Any]]]:
    """The miner's own engineered revlog, exposed as pre-tagged deck notes
    plus caller-read revlog rows (the desktop path's input shape)."""
    reviews, fronts = confusability.synthetic_revlog()
    cluster_by_note = {
        1: "fi::duration",
        2: "fi::convexity",
        3: "fi::credit",
        4: "fi::creditx",
    }
    notes = [
        note(
            nid,
            fronts[nid],
            tags=[
                "cfa::topic::fixed_income",
                f"cluster::{cluster_by_note[nid]}",
            ],
        )
        for nid in sorted(fronts)
    ]
    rows = [
        {"note_id": r.note_id, "button": 1 if r.lapse else 3, "id_ms": r.id_ms}
        for r in reviews
    ]
    return notes, rows


class ConfusabilityTests(unittest.TestCase):
    def test_no_revlog_skips_and_says_so(self) -> None:
        proposal = onboard.propose([note(1, "Macaulay duration of a bond?")], TOPICS)
        self.assertIn("skipped", proposal.confusability)
        self.assertEqual(proposal.cluster_markers, {})

    def test_engineered_pair_marked_with_validation_evidence(self) -> None:
        notes, rows = _synthetic_setup()
        proposal = onboard.propose(notes, TOPICS, revlog=rows)
        self.assertEqual(
            set(proposal.cluster_markers), {"fi::duration", "fi::convexity"}
        )
        for nid in (1, 2):
            tp = proposal_for(proposal, nid, "confusable::")
            self.assertIsNotNone(tp, nid)
            self.assertEqual(tp.tag, "confusable::high")
            self.assertEqual(tp.source, "confusability")
            self.assertIn("70/30", tp.evidence)
            self.assertIn("AUC", tp.evidence)
            self.assertGreater(tp.confidence, 0.5)
        for nid in (3, 4):
            self.assertIsNone(proposal_for(proposal, nid, "confusable::"), nid)

    def test_tiny_revlog_abstains_honestly(self) -> None:
        notes, rows = _synthetic_setup()
        proposal = onboard.propose(notes, TOPICS, revlog=rows[:10])
        self.assertIn("too little data", proposal.confusability)
        self.assertEqual(proposal.cluster_markers, {})
        for np in proposal.notes:
            for tp in np.tags:
                self.assertNotEqual(tp.tag, "confusable::high")

    def test_jsonl_path_input(self) -> None:
        notes, rows = _synthetic_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "revlog.jsonl"
            with path.open("w") as f:
                for row in rows:
                    f.write(json.dumps({**row, "tags": []}) + "\n")
            proposal = onboard.propose(notes, TOPICS, revlog=str(path))
        self.assertIn("fi::duration", proposal.cluster_markers)

    def test_unreadable_revlog_degrades(self) -> None:
        notes, _rows = _synthetic_setup()
        proposal = onboard.propose(notes, TOPICS, revlog="/no/such/file.jsonl")
        self.assertIn("skipped", proposal.confusability)
        self.assertEqual(proposal.cluster_markers, {})


# ---------------------------------------------------------------------------
# Missing-rung generation (default OFF, [R24])
# ---------------------------------------------------------------------------

COVERING_CORPUS = """\
## Duration fundamentals

Macaulay duration weights the timing of a coupon bond's cash flows, while
modified duration rescales that figure for the per-period yield, giving the
price response of the bond when interest rates move.
"""

UNRELATED_CORPUS = """\
## Baking bread

Knead the dough, let it rise overnight, then bake until the crust browns.
"""

FAKE_DRAFT = {
    "title": "Duration variant check",
    "stem": "Which duration figure adjusts the weighted time measure for yield?",
    "choices": {
        "A": "Modified duration",
        "B": "Macaulay duration",
        "C": "Effective duration",
    },
    "correct": "A",
    "rationale": "Modified duration equals the Macaulay figure over 1 + y/k.",
    "distractor_rationales": {
        "B": "Incorrect - that is the unadjusted weighted time measure.",
        "C": "Incorrect - effective duration handles embedded options.",
    },
    "misconceptions": {},
}


class FakeLlmPath:
    def __init__(self, draft: dict[str, Any] | None = FAKE_DRAFT) -> None:
        self.drafter = SimpleNamespace(name="fake")
        self.draft = draft
        self.calls: list[tuple[str, str, str]] = []

    def generate_validated(
        self, topic: str, cluster: str, concept: str, mids: list[str]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls.append((topic, cluster, concept))
        if self.draft is None:
            return None, {"outcome": "rejected by critic/consensus"}
        return dict(self.draft), {"outcome": "accepted by llm path"}

    def solver_consensus(self, item: dict[str, Any]) -> tuple[bool, list[str]]:
        return True, [str(item.get("correct"))] * 3


def _partial_ladder_notes() -> list[Any]:
    base = ["cfa::topic::fixed_income", "cluster::fi::duration"]
    return [
        note(11, "Macaulay duration of a coupon bond?", tags=base + ["rung::worked"]),
        note(12, "Modified duration of a coupon bond?", tags=base + ["rung::faded"]),
    ]


def _corpus_dir(tmp: str, text: str) -> Path:
    directory = Path(tmp) / "corpus"
    directory.mkdir()
    (directory / "duration.md").write_text(text)
    return directory


class GenerationTests(unittest.TestCase):
    def test_off_by_default(self) -> None:
        proposal = onboard.propose(_partial_ladder_notes(), TOPICS)
        self.assertEqual(proposal.generated_items, [])
        self.assertEqual(proposal.generation_notes, [])

    def test_missing_solve_rung_drafted_and_gated(self) -> None:
        path = FakeLlmPath()
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                _partial_ladder_notes(),
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, COVERING_CORPUS),
                llm_path=path,
            )
        self.assertEqual(len(proposal.generated_items), 1)
        entry = proposal.generated_items[0]
        item = entry["item"]
        import ladder_schema

        self.assertEqual(ladder_schema.validate_item(item), [])
        self.assertEqual(item["rung"], "solve")
        self.assertEqual(item["cluster"], "fi::duration")
        self.assertEqual(item["topic"], "fixed_income")
        # [R24]: never graded here; the ungraded tag keeps it out of
        # Readiness until validated on delayed held-out probes.
        self.assertFalse(item["provenance"]["graded"])
        self.assertIn("aig::ungraded", entry["tags"])
        self.assertNotIn("aig::graded", entry["tags"])
        self.assertTrue(item["source"]["passage"])
        gate_names = {g["gate"] for g in entry["gates"]}
        self.assertEqual(
            gate_names, {"numeric", "solve_check", "rationale", "leakage", "schema"}
        )
        self.assertTrue(all(g["passed"] for g in entry["gates"]))
        self.assertEqual(path.calls[0][1], "fi::duration")

    def test_uncovered_corpus_abstains(self) -> None:
        path = FakeLlmPath()
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                _partial_ladder_notes(),
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, UNRELATED_CORPUS),
                llm_path=path,
            )
        self.assertEqual(proposal.generated_items, [])
        self.assertTrue(
            any("does not cover" in note_ for note_ in proposal.generation_notes),
            proposal.generation_notes,
        )
        self.assertEqual(path.calls, [], "no draft without grounding")

    def test_non_solve_gaps_are_explicit_abstentions(self) -> None:
        base = ["cfa::topic::fixed_income", "cluster::fi::duration", "rung::solve"]
        notes = [
            note(11, "Macaulay duration of a coupon bond?", tags=list(base)),
            note(12, "Modified duration of a coupon bond?", tags=list(base)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                notes,
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, COVERING_CORPUS),
                llm_path=FakeLlmPath(),
            )
        self.assertEqual(proposal.generated_items, [])
        self.assertEqual(len(proposal.generation_notes), 2)  # worked + faded
        for note_ in proposal.generation_notes:
            self.assertIn("only drafts MCQ", note_)

    def test_rejected_draft_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                _partial_ladder_notes(),
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, COVERING_CORPUS),
                llm_path=FakeLlmPath(draft=None),
            )
        self.assertEqual(proposal.generated_items, [])
        self.assertTrue(any("rejected" in note_ for note_ in proposal.generation_notes))

    def test_complete_or_untagged_ladders_generate_nothing(self) -> None:
        base = ["cfa::topic::fixed_income", "cluster::fi::duration"]
        notes = [
            note(11, "a?", tags=base + ["rung::worked"]),
            note(12, "b?", tags=base + ["rung::faded"]),
            note(13, "c?", tags=base + ["rung::solve"]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                notes,
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, COVERING_CORPUS),
                llm_path=FakeLlmPath(),
            )
        self.assertEqual(proposal.generated_items, [])
        self.assertIn("no cluster has a partial ladder", proposal.generation_notes[0])

    def test_default_mock_llm_path_is_offline_and_safe(self) -> None:
        # llm_path=None builds aig.models.make_llm_path("mock"): fully
        # offline; the run must complete and stay ungraded end to end.
        with tempfile.TemporaryDirectory() as tmp:
            proposal = onboard.propose(
                _partial_ladder_notes(),
                TOPICS,
                generate_missing_rungs=True,
                corpus_dir=_corpus_dir(tmp, COVERING_CORPUS),
            )
        for entry in proposal.generated_items:
            self.assertFalse(entry["item"]["provenance"]["graded"])
            self.assertIn("aig::ungraded", entry["tags"])


# ---------------------------------------------------------------------------
# apply() - the single undoable writer
# ---------------------------------------------------------------------------


class FakeNote:
    def __init__(self, nid: int, tags: list[str]) -> None:
        self.id = nid
        self.tags = list(tags)

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    def add_tag(self, tag: str) -> None:
        self.tags.append(tag)


class FakeCollection:
    """update_notes capture; any other write is a test failure."""

    def __init__(self, notes: dict[int, FakeNote]) -> None:
        self.notes = notes
        self.update_calls: list[list[FakeNote]] = []

    def get_note(self, nid: int) -> FakeNote:
        return self.notes[nid]

    def update_notes(self, notes: list[FakeNote]) -> None:
        self.update_calls.append(list(notes))

    def update_note(self, note: FakeNote) -> None:
        raise AssertionError("apply must batch through ONE update_notes call")


class ApplyTests(unittest.TestCase):
    def _proposal_and_collection(self):
        # The only shared distinctive token is "duration", so the proposal
        # is exactly one topic tag + one cluster tag per note.
        notes = [
            note(1, "What is Macaulay duration?"),
            note(2, "What is modified duration?"),
        ]
        proposal = onboard.propose(notes, TOPICS)
        col = FakeCollection({1: FakeNote(1, []), 2: FakeNote(2, [])})
        return proposal, col

    def test_apply_all_batches_one_update(self) -> None:
        proposal, col = self._proposal_and_collection()
        changed = onboard.apply(col, proposal)
        self.assertEqual(changed, 2)
        self.assertEqual(len(col.update_calls), 1)
        self.assertEqual(
            sorted(col.notes[1].tags),
            ["cfa::topic::fixed_income", "cluster::fi::duration"],
        )
        self.assertEqual(
            sorted(col.notes[2].tags),
            ["cfa::topic::fixed_income", "cluster::fi::duration"],
        )

    def test_accepted_subset_filters_pairs(self) -> None:
        proposal, col = self._proposal_and_collection()
        changed = onboard.apply(
            col, proposal, accepted={(1, "cfa::topic::fixed_income")}
        )
        self.assertEqual(changed, 1)
        self.assertEqual(col.notes[1].tags, ["cfa::topic::fixed_income"])
        self.assertEqual(col.notes[2].tags, [])

    def test_empty_acceptance_writes_nothing(self) -> None:
        proposal, col = self._proposal_and_collection()
        self.assertEqual(onboard.apply(col, proposal, accepted=set()), 0)
        self.assertEqual(col.update_calls, [])

    def test_reapply_is_idempotent(self) -> None:
        proposal, col = self._proposal_and_collection()
        onboard.apply(col, proposal)
        self.assertEqual(onboard.apply(col, proposal), 0)
        self.assertEqual(len(col.update_calls), 1)

    def test_existing_tags_never_removed(self) -> None:
        proposal, _ = self._proposal_and_collection()
        col = FakeCollection({1: FakeNote(1, ["my::tag"]), 2: FakeNote(2, [])})
        onboard.apply(col, proposal)
        self.assertIn("my::tag", col.notes[1].tags)


# ---------------------------------------------------------------------------
# Honesty + purity
# ---------------------------------------------------------------------------


class EvidenceConfidenceTests(unittest.TestCase):
    def test_every_proposal_carries_evidence_and_confidence(self) -> None:
        notes, rows = _synthetic_setup()
        mixed = notes + [
            note(21, "Macaulay duration of a coupon bond?"),
            note(22, "Modified duration of a coupon bond?"),
            note(23, "Compute FV", "FV = 1000 x (1.03)^10 = 1343.92"),
            note(24, "{{c1::Duration}} rises when {{c2::coupons}} fall"),
        ]
        proposal = onboard.propose(mixed, TOPICS, revlog=rows)
        self.assertGreater(proposal.tag_count(), 0)
        for np in proposal.notes:
            for tp in np.tags:
                self.assertTrue(tp.evidence.strip(), tp.tag)
                self.assertTrue(0 < tp.confidence <= 1, (tp.tag, tp.confidence))
                self.assertIn(
                    tp.source,
                    {
                        "lexicon",
                        "ai",
                        "cluster",
                        "rung",
                        "interactivity",
                        "confusability",
                    },
                )

    def test_empty_deck_is_a_clean_noop(self) -> None:
        proposal = onboard.propose([], TOPICS)
        self.assertEqual(proposal.notes, [])
        self.assertEqual(proposal.tag_count(), 0)
        self.assertEqual(proposal.summary["n_notes"], 0)


class PurityTests(unittest.TestCase):
    def test_no_pylib_or_gui_imports(self) -> None:
        import inspect

        for name, value in vars(onboard).items():
            module = inspect.getmodule(value)
            if module is None or module is onboard:
                continue
            root = module.__name__.partition(".")[0]
            self.assertNotIn(
                root,
                {"anki", "aqt"},
                f"onboard.{name} pulls in {module.__name__}",
            )


if __name__ == "__main__":
    unittest.main()
