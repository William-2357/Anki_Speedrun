# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for ladder_notetypes: the four card variants of the fade
ladder (worked / faded-cloze / solve-MCQ / compare). stdlib only; run with:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ladder_notetypes  # noqa: E402
import ladder_schema  # noqa: E402

FEEDBACK_MARKER = 'class="sr-feedback" hidden'


def cloze_item() -> dict:
    return {
        "prompt": "Fill the <b>blanks</b>.",
        "cloze_text": "First {{c1::alpha}}, then {{c2::beta}}.",
        "rationale": "Alpha precedes beta.",
        "source": {"doc": "Fixture corpus", "loc": "s1", "passage": "A passage."},
    }


def mcq_item() -> dict:
    return {
        "title": "Callable bond",
        "stem": "Which duration measure?",
        "choices": {"A": "modified", "B": "effective", "C": "Macaulay"},
        "correct": "B",
        "rationale": "Cash flows move with rates.",
        "distractor_rationales": {"A": "Fixed cash flows.", "C": "A time measure."},
        "source": {"doc": "Fixture corpus", "loc": "s3", "passage": "A passage."},
    }


class NotetypeStructureTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(ladder_notetypes.WORKED["name"], "Speedrun Worked")
        self.assertEqual(ladder_notetypes.SOLVE_MCQ["name"], "Speedrun Solve MCQ")
        self.assertEqual(ladder_notetypes.COMPARE["name"], "Speedrun Compare")
        self.assertEqual(ladder_notetypes.STOCK_CLOZE_NOTETYPE_NAME, "Cloze")

    def test_fields(self) -> None:
        self.assertEqual(
            ladder_notetypes.WORKED["fields"],
            ["Title", "Prompt", "Steps", "Rationale", "Source"],
        )
        self.assertEqual(
            ladder_notetypes.SOLVE_MCQ["fields"],
            [
                "Title",
                "Stem",
                "ChoiceA",
                "ChoiceB",
                "ChoiceC",
                "Correct",
                "Rationale",
                "WrongA",
                "WrongB",
                "WrongC",
                "SelfExplainPrompt",
                "Source",
            ],
        )
        self.assertEqual(
            ladder_notetypes.COMPARE["fields"],
            [
                "Title",
                "LeftTitle",
                "LeftBody",
                "RightTitle",
                "RightBody",
                "Discriminator",
                "Rationale",
                "Source",
            ],
        )

    def test_template_counts_and_ordinals(self) -> None:
        self.assertEqual(
            [t["name"] for t in ladder_notetypes.WORKED["templates"]], ["Worked"]
        )
        # ord 0 = plain solve, ord 1 = self-explain: fade.rs serves the
        # lowest ordinal with self_explain_enabled off, the highest with it
        # on, so the order here is load-bearing.
        self.assertEqual(
            [t["name"] for t in ladder_notetypes.SOLVE_MCQ["templates"]],
            ["Solve", "Solve + Self-Explain"],
        )
        self.assertEqual(
            [t["name"] for t in ladder_notetypes.COMPARE["templates"]], ["Compare"]
        )

    def test_kind_mapping(self) -> None:
        self.assertEqual(
            set(ladder_notetypes.NOTETYPE_FOR_KIND), {"worked", "mcq", "compare"}
        )

    def test_every_template_referenced_field_exists(self) -> None:
        """Templates must not reference fields the note type lacks (silent
        render failure in Anki)."""
        special = {"FrontSide"}
        for spec in ladder_notetypes.NOTETYPES:
            fields = set(spec["fields"])
            for template in spec["templates"]:
                for side in ("qfmt", "afmt"):
                    used = ladder_schema.rendered_fields(template[side])
                    unknown = used - fields - special
                    self.assertEqual(
                        unknown,
                        set(),
                        f"{spec['name']}/{template['name']}/{side} references "
                        f"unknown fields {unknown}",
                    )


class SelfExplainVariantTests(unittest.TestCase):
    def test_ord1_wrapped_in_self_explain_conditional(self) -> None:
        qfmt = ladder_notetypes.SOLVE_MCQ["templates"][1]["qfmt"].strip()
        self.assertTrue(qfmt.startswith("{{#SelfExplainPrompt}}"))
        self.assertTrue(qfmt.endswith("{{/SelfExplainPrompt}}"))
        # and the prompt is actually shown above the stem
        self.assertIn('<div class="sr-self-explain">{{SelfExplainPrompt}}</div>', qfmt)
        self.assertLess(
            qfmt.index("sr-self-explain"),
            qfmt.index("sr-stem"),
            "the self-explain prompt must precede the stem",
        )

    def test_ord0_has_no_self_explain(self) -> None:
        qfmt = ladder_notetypes.SOLVE_MCQ["templates"][0]["qfmt"]
        self.assertNotIn("SelfExplainPrompt", qfmt)


class HonestyTests(unittest.TestCase):
    """The front must not visibly leak Correct/Wrong* before reveal, and
    templates must be dependency-free (no pycmd, no bridge, no external
    scripts)."""

    def test_solve_front_leaks_nothing_before_feedback_panel(self) -> None:
        for template in ladder_notetypes.SOLVE_MCQ["templates"]:
            qfmt = template["qfmt"]
            self.assertIn(FEEDBACK_MARKER, qfmt, "feedback panel must start hidden")
            before_feedback = qfmt.split(FEEDBACK_MARKER)[0]
            self.assertNotIn("{{Wrong", before_feedback)
            self.assertNotIn("{{Rationale}}", before_feedback)
            # the correct letter appears exactly once: as a data attribute
            self.assertEqual(before_feedback.count("{{Correct}}"), 1)
            self.assertIn('data-correct="{{Correct}}"', before_feedback)

    def test_choices_rendered_in_fixed_order(self) -> None:
        for template in ladder_notetypes.SOLVE_MCQ["templates"]:
            qfmt = template["qfmt"]
            self.assertLess(qfmt.index("{{ChoiceA}}"), qfmt.index("{{ChoiceB}}"))
            self.assertLess(qfmt.index("{{ChoiceB}}"), qfmt.index("{{ChoiceC}}"))

    def test_no_bridge_or_external_deps_anywhere(self) -> None:
        for spec in ladder_notetypes.NOTETYPES:
            blobs = [spec["css"]] + [
                template[side]
                for template in spec["templates"]
                for side in ("qfmt", "afmt")
            ]
            for blob in blobs:
                self.assertNotIn("pycmd", blob)
                self.assertNotIn("<script src", blob)
                self.assertNotIn("@import", blob)
                self.assertNotIn("http://", blob)
                self.assertNotIn("https://", blob)

    def test_solve_back_is_a_nojs_fallback(self) -> None:
        afmt = ladder_notetypes.SOLVE_MCQ["templates"][0]["afmt"]
        rendered = ladder_schema.rendered_fields(afmt)
        self.assertLessEqual(
            {
                "Correct",
                "Stem",
                "ChoiceA",
                "ChoiceB",
                "ChoiceC",
                "Rationale",
                "WrongA",
                "WrongB",
                "WrongC",
            },
            rendered,
            "the back must show the answer + all rationales without JS",
        )
        self.assertIn("Correct answer:", afmt)


class FeedbackInvariantTests(unittest.TestCase):
    def test_r9_lint_passes_for_all_four_notetypes(self) -> None:
        for spec in ladder_notetypes.NOTETYPES:
            self.assertEqual(
                ladder_schema.lint_notetype_feedback(
                    spec["templates"], feedback_fields=("Rationale",)
                ),
                [],
                f"{spec['name']} must end in a feedback step",
            )
        # the faded rung's stock cloze reveal: Back Extra carries the
        # rationale (faded_cloze_fields puts it there)
        stock_cloze = [
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<br>\n{{Back Extra}}",
            }
        ]
        self.assertEqual(
            ladder_schema.lint_notetype_feedback(
                stock_cloze, feedback_fields=("Back Extra",)
            ),
            [],
        )

    def test_r9_lint_fails_with_rationale_stripped(self) -> None:
        for spec in ladder_notetypes.NOTETYPES:
            for template in spec["templates"]:
                stripped = dict(template)
                stripped["afmt"] = template["afmt"].replace("{{Rationale}}", "")
                errors = ladder_schema.lint_notetype_feedback(
                    [stripped], feedback_fields=("Rationale",)
                )
                self.assertTrue(
                    errors,
                    f"{spec['name']}/{template['name']} must fail once the "
                    "rationale is stripped",
                )


class CssTests(unittest.TestCase):
    def test_css_present_and_night_mode_aware(self) -> None:
        for spec in ladder_notetypes.NOTETYPES:
            css = spec["css"]
            self.assertTrue(css.strip(), f"{spec['name']} must embed CSS")
            self.assertIn(".card", css)
            self.assertIn(".nightMode", css, "desktop night mode")
            self.assertIn(".night_mode", css, "AnkiDroid night mode")


class SourceHtmlTests(unittest.TestCase):
    """The [R21] citation must read as a labelled source, never as raw
    machine coordinates ("duration.md, #compounding-conventions-...")."""

    def test_machine_coordinates_render_as_readable_citation(self) -> None:
        out = ladder_notetypes.source_html(
            {
                "doc": "duration.md",
                "loc": "#compounding-conventions-for-duration",
                "passage": "Yields are quoted nominally.",
            }
        )
        self.assertIn('<span class="sr-source-label">Source:</span>', out)
        self.assertIn("Duration &mdash; Compounding conventions for duration", out)
        self.assertIn("&ldquo;Yields are quoted nominally.&rdquo;", out)
        # the raw filename / slug forms must not leak onto the card
        self.assertNotIn("duration.md", out)
        self.assertNotIn("#compounding", out)

    def test_doc_only_and_underscores(self) -> None:
        out = ladder_notetypes.source_html(
            {"doc": "bond_pricing.md", "loc": "", "passage": ""}
        )
        self.assertIn("Bond pricing", out)
        self.assertNotIn("&mdash;", out)
        self.assertNotIn("sr-source-passage", out)

    def test_reference_is_escaped(self) -> None:
        out = ladder_notetypes.source_html(
            {"doc": "a&b.md", "loc": "#x<y", "passage": ""}
        )
        self.assertIn("A&amp;b", out)
        self.assertIn("X&lt;y", out)


class FieldHelperTests(unittest.TestCase):
    def test_faded_cloze_fields(self) -> None:
        text, back_extra = ladder_notetypes.faded_cloze_fields(cloze_item())
        # prompt is escaped, cloze markup is preserved
        self.assertIn("Fill the &lt;b&gt;blanks&lt;/b&gt;.", text)
        self.assertIn("{{c1::alpha}}", text)
        self.assertIn("{{c2::beta}}", text)
        self.assertGreaterEqual(len(ladder_schema.cloze_indices(text)), 2)
        # Back Extra = rationale + source: the [R9] feedback the stock
        # cloze answer template renders
        self.assertIn("Alpha precedes beta.", back_extra)
        self.assertIn("Fixture corpus", back_extra)

    def test_worked_note_fields(self) -> None:
        fields = ladder_notetypes.worked_note_fields(
            {
                "title": "T & Co",
                "prompt": "P",
                "worked_steps": ["one", "two", "three"],
                "rationale": "R",
                "source": {"doc": "D", "loc": "L", "passage": "Q"},
            }
        )
        self.assertEqual(fields["Title"], "T &amp; Co")
        self.assertEqual(fields["Steps"].count("<li>"), 3)
        self.assertTrue(fields["Steps"].startswith("<ol"))
        self.assertEqual(
            set(fields),
            set(ladder_notetypes.WORKED["fields"]),
            "helper must fill exactly the notetype's fields",
        )

    def test_solve_note_fields(self) -> None:
        fields = ladder_notetypes.solve_note_fields(mcq_item())
        self.assertEqual(fields["Correct"], "B")
        self.assertEqual(fields["WrongB"], "", "the correct letter has no wrong-why")
        self.assertEqual(fields["WrongA"], "Fixed cash flows.")
        self.assertEqual(fields["WrongC"], "A time measure.")
        self.assertEqual(
            fields["SelfExplainPrompt"],
            ladder_notetypes.DEFAULT_SELF_EXPLAIN_PROMPT,
        )
        self.assertEqual(set(fields), set(ladder_notetypes.SOLVE_MCQ["fields"]))

    def test_solve_note_fields_no_self_explain(self) -> None:
        fields = ladder_notetypes.solve_note_fields(
            mcq_item(), include_self_explain=False
        )
        self.assertEqual(
            fields["SelfExplainPrompt"],
            "",
            "an empty prompt suppresses the ord-1 card via the front "
            "conditional, so --no-self-explain decks have no duplicates",
        )

    def test_compare_note_fields(self) -> None:
        fields = ladder_notetypes.compare_note_fields(
            {
                "title": "T",
                "left_title": "L",
                "left_body": "LB",
                "right_title": "R",
                "right_body": "RB",
                "discriminator": "D?",
                "rationale": "because",
                "source": {"doc": "D", "loc": "L", "passage": ""},
            }
        )
        self.assertEqual(fields["LeftTitle"], "L")
        self.assertEqual(fields["RightBody"], "RB")
        self.assertEqual(fields["Discriminator"], "D?")
        self.assertEqual(set(fields), set(ladder_notetypes.COMPARE["fields"]))


class CompareLayoutTests(unittest.TestCase):
    def test_side_by_side_columns_and_discriminator(self) -> None:
        qfmt = ladder_notetypes.COMPARE["templates"][0]["qfmt"]
        self.assertIn("sr-columns", qfmt)
        self.assertLess(qfmt.index("{{LeftTitle}}"), qfmt.index("{{RightTitle}}"))
        self.assertIn("{{Discriminator}}", qfmt)
        self.assertIn("display: flex", ladder_notetypes.COMPARE["css"])

    def test_back_adds_rationale(self) -> None:
        afmt = ladder_notetypes.COMPARE["templates"][0]["afmt"]
        self.assertIn("{{FrontSide}}", afmt)
        self.assertIn("{{Rationale}}", afmt)


if __name__ == "__main__":
    unittest.main()
