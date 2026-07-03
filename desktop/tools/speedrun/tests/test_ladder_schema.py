# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for ladder_schema: the ITEM_SCHEMA.md contract + the [R9]
feedback lint. stdlib only; run with:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ladder_notetypes  # noqa: E402
import ladder_schema  # noqa: E402

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "items" / "seed_fixtures.jsonl"


def load_fixtures() -> list[dict]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def base_item(kind: str) -> dict:
    """A minimal valid item of the given kind."""
    item = {
        "schema": "speedrun-item-v1",
        "kind": kind,
        "rung": ladder_schema.RUNG_FOR_KIND[kind],
        "topic": "fixed_income",
        "cluster": "fi::duration",
        "interactivity": "high",
        "title": "Test item",
        "rationale": "Because the discounting rescales time into sensitivity.",
        "source": {
            "doc": "Fixture corpus",
            "loc": "section 1",
            "passage": "A passage.",
        },
        "provenance": {"generator": "fixture:test", "gates": [], "graded": False},
    }
    if kind == "worked":
        item["prompt"] = "Compute the thing."
        item["worked_steps"] = ["Identify inputs.", "Apply the formula."]
    elif kind == "cloze":
        item["prompt"] = "Fill the blanks."
        item["cloze_text"] = "First {{c1::alpha}}, then {{c2::beta}}."
    elif kind == "mcq":
        item["stem"] = "Which measure applies?"
        item["choices"] = {"A": "modified", "B": "effective", "C": "Macaulay"}
        item["correct"] = "B"
        item["distractor_rationales"] = {
            "A": "Assumes fixed cash flows.",
            "C": "A time measure, not a sensitivity.",
        }
    elif kind == "compare":
        item["left_title"] = "Macaulay"
        item["left_body"] = "Weighted-average time."
        item["right_title"] = "Modified"
        item["right_body"] = "Price sensitivity."
        item["discriminator"] = "Which prices a shock?"
    return item


class FixtureTests(unittest.TestCase):
    def test_every_fixture_validates(self) -> None:
        fixtures = load_fixtures()
        self.assertGreaterEqual(len(fixtures), 6)
        self.assertLessEqual(len(fixtures), 10)
        for fixture in fixtures:
            errors = ladder_schema.validate_item(fixture)
            self.assertEqual(
                errors, [], f"fixture {fixture.get('title')!r} must validate: {errors}"
            )

    def test_fixture_coverage(self) -> None:
        """The fixture set exercises all four kinds on fi::duration plus one
        qm::tvm item, all marked as ungraded test fixtures."""
        fixtures = load_fixtures()
        duration_kinds = {
            fixture["kind"]
            for fixture in fixtures
            if fixture["cluster"] == "fi::duration"
        }
        self.assertEqual(duration_kinds, set(ladder_schema.KINDS))
        tvm = [fixture for fixture in fixtures if fixture["cluster"] == "qm::tvm"]
        self.assertEqual(len(tvm), 1)
        for fixture in fixtures:
            self.assertEqual(fixture["provenance"]["generator"], "fixture:test")
            self.assertFalse(fixture["provenance"]["graded"])


class ValidateItemTests(unittest.TestCase):
    def assert_rejected(self, item: dict, needle: str) -> list[str]:
        errors = ladder_schema.validate_item(item)
        self.assertTrue(
            any(needle in error for error in errors),
            f"expected an error mentioning {needle!r}, got {errors}",
        )
        return errors

    def test_base_items_are_valid(self) -> None:
        for kind in ladder_schema.KINDS:
            self.assertEqual(ladder_schema.validate_item(base_item(kind)), [])

    def test_non_object_rejected(self) -> None:
        self.assertTrue(ladder_schema.validate_item("not a dict"))
        self.assertTrue(ladder_schema.validate_item(None))

    def test_wrong_schema_literal(self) -> None:
        item = base_item("worked")
        item["schema"] = "speedrun-item-v2"
        self.assert_rejected(item, "schema")

    def test_empty_rationale_rejected(self) -> None:
        for value in ("", "   ", None):
            item = base_item("worked")
            item["rationale"] = value
            self.assert_rejected(item, "rationale")

    def test_missing_distractor_rationale_rejected(self) -> None:
        item = base_item("mcq")
        del item["distractor_rationales"]["C"]
        self.assert_rejected(item, "C")

    def test_empty_distractor_rationale_rejected(self) -> None:
        item = base_item("mcq")
        item["distractor_rationales"]["A"] = "  "
        self.assert_rejected(item, "distractor_rationales.A")

    def test_distractor_rationale_for_correct_letter_rejected(self) -> None:
        item = base_item("mcq")
        item["distractor_rationales"]["B"] = "but B is the correct answer"
        self.assert_rejected(item, "unexpected key")

    def test_too_few_cloze_indices_rejected(self) -> None:
        item = base_item("cloze")
        item["cloze_text"] = "Only {{c1::one}} blank."
        self.assert_rejected(item, "cloze")
        # repeating the same index is still one index
        item["cloze_text"] = "Twice {{c1::a}} and {{c1::b}}."
        self.assert_rejected(item, "cloze")

    def test_cloze_index_zero_does_not_count(self) -> None:
        item = base_item("cloze")
        item["cloze_text"] = "Bad {{c0::zero}} plus {{c1::one}}."
        self.assert_rejected(item, "cloze")

    def test_mathjax_cloze_text_accepted(self) -> None:
        # linear TeX with no "}}" outside the markers is fine
        item = base_item("cloze")
        item["cloze_text"] = (
            r"\(\text{ModDur} = \text{MacDur} / (1 + y/k)\) = "
            r"{{c1::9.80}} / (1 + {{c2::0.02650}})."
        )
        self.assertEqual(ladder_schema.validate_item(item), [])

    def test_cloze_text_with_stray_brace_pair_rejected(self) -> None:
        # nested TeX groups like x^{y^{2}} end in "}}", which makes Anki
        # close the deletion early ({{cN:: ends at the FIRST following "}}")
        item = base_item("cloze")
        item["cloze_text"] = r"Growth: {{c1::\(x^{y^{2}}\)}} and {{c2::compounding}}."
        self.assert_rejected(item, '"}}" sequence')

    def test_cloze_stripped_remainder_scans_like_anki(self) -> None:
        self.assertEqual(
            ladder_schema.cloze_stripped_remainder("a {{c1::x}} b {{c2::y}} c"),
            "a x b y c",
        )
        # the first "}}" after an opener closes it, even mid-TeX
        self.assertEqual(
            ladder_schema.cloze_stripped_remainder(r"{{c1::x^{n}} tail}}"),
            r"x^{n tail}}",
        )

    def test_empty_worked_steps_rejected(self) -> None:
        item = base_item("worked")
        item["worked_steps"] = []
        self.assert_rejected(item, "worked_steps")
        item["worked_steps"] = ["ok", "  "]
        self.assert_rejected(item, "worked_steps")

    def test_bad_kind_rejected(self) -> None:
        item = base_item("worked")
        item["kind"] = "essay"
        self.assert_rejected(item, "kind")

    def test_bad_rung_rejected(self) -> None:
        item = base_item("worked")
        item["rung"] = "mastered"
        self.assert_rejected(item, "rung")

    def test_kind_rung_mismatch_rejected(self) -> None:
        item = base_item("mcq")
        item["rung"] = "faded"
        self.assert_rejected(item, "rung")

    def test_bad_choice_keys_rejected(self) -> None:
        item = base_item("mcq")
        item["choices"] = {"A": "a", "B": "b", "D": "d"}
        self.assert_rejected(item, "choices")
        item = base_item("mcq")
        item["choices"] = {"A": "a", "B": "b"}
        self.assert_rejected(item, "choices")

    def test_bad_correct_letter_rejected(self) -> None:
        item = base_item("mcq")
        item["correct"] = "D"
        self.assert_rejected(item, "correct")

    def test_bad_interactivity_rejected(self) -> None:
        item = base_item("worked")
        item["interactivity"] = "medium"
        self.assert_rejected(item, "interactivity")

    def test_prefixed_topic_and_cluster_rejected(self) -> None:
        item = base_item("worked")
        item["topic"] = "cfa::topic::fixed_income"
        self.assert_rejected(item, "suffix")
        item = base_item("worked")
        item["cluster"] = "cluster::fi::duration"
        self.assert_rejected(item, "suffix")

    def test_bad_provenance_rejected(self) -> None:
        item = base_item("worked")
        item["provenance"]["graded"] = "no"
        self.assert_rejected(item, "graded")
        item = base_item("worked")
        del item["provenance"]
        self.assert_rejected(item, "provenance")

    def test_missing_source_rejected(self) -> None:
        item = base_item("worked")
        item["source"] = {"doc": "", "loc": "x", "passage": "y"}
        self.assert_rejected(item, "source.doc")

    def test_bad_tags_extra_rejected(self) -> None:
        item = base_item("worked")
        item["tags_extra"] = ["ok::tag", "has space"]
        self.assert_rejected(item, "tags_extra")

    def test_misconceptions_keys_checked(self) -> None:
        item = base_item("mcq")
        item["misconceptions"] = {"B": "some.id"}  # B is the correct letter
        self.assert_rejected(item, "misconceptions")


class TagDerivationTests(unittest.TestCase):
    def test_tags_exactly_as_item_schema_specifies(self) -> None:
        item = base_item("mcq")
        item["tags_extra"] = ["confusable::high"]
        self.assertEqual(
            ladder_schema.tags_for_item(item),
            [
                "cfa::topic::fixed_income",
                "cluster::fi::duration",
                "rung::solve",
                "interactivity::high",
                "aig::ungraded",
                "confusable::high",
            ],
        )

    def test_graded_flag_flips_aig_tag(self) -> None:
        item = base_item("worked")
        item["provenance"]["graded"] = True
        self.assertIn("aig::graded", ladder_schema.tags_for_item(item))
        self.assertNotIn("aig::ungraded", ladder_schema.tags_for_item(item))

    def test_compare_gets_bookkeeping_rung_tag(self) -> None:
        item = base_item("compare")
        self.assertIn("rung::compare", ladder_schema.tags_for_item(item))


# The stock cloze templates, shaped as rslib's stock.rs defines them; the
# builder lints the real ones from the collection at build time.
STOCK_CLOZE_TEMPLATES = [
    {
        "name": "Cloze",
        "qfmt": "{{cloze:Text}}",
        "afmt": "{{cloze:Text}}<br>\n{{Back Extra}}",
    }
]


class FeedbackLintTests(unittest.TestCase):
    def test_all_custom_notetypes_pass(self) -> None:
        for spec in ladder_notetypes.NOTETYPES:
            errors = ladder_schema.lint_notetype_feedback(
                spec["templates"], feedback_fields=("Rationale",)
            )
            self.assertEqual(errors, [], f"{spec['name']} must pass the R9 lint")

    def test_stock_cloze_passes_via_back_extra(self) -> None:
        self.assertEqual(
            ladder_schema.lint_notetype_feedback(
                STOCK_CLOZE_TEMPLATES, feedback_fields=("Back Extra",)
            ),
            [],
        )

    def test_default_feedback_fields_cover_both(self) -> None:
        templates = list(ladder_notetypes.WORKED["templates"]) + STOCK_CLOZE_TEMPLATES
        self.assertEqual(ladder_schema.lint_notetype_feedback(templates), [])

    def test_stripped_rationale_fails(self) -> None:
        template = copy.deepcopy(ladder_notetypes.WORKED["templates"][0])
        template["afmt"] = template["afmt"].replace("{{Rationale}}", "")
        errors = ladder_schema.lint_notetype_feedback([template], ("Rationale",))
        self.assertEqual(len(errors), 1)
        self.assertIn("[R9]", errors[0])

    def test_conditional_reference_does_not_count(self) -> None:
        # {{#Rationale}}...{{/Rationale}} tests presence without rendering
        # the rationale - that is not a feedback step.
        templates = [
            {
                "name": "Sneaky",
                "qfmt": "{{Prompt}}",
                "afmt": "{{#Rationale}}see the book{{/Rationale}}",
            }
        ]
        self.assertTrue(ladder_schema.lint_notetype_feedback(templates, ("Rationale",)))

    def test_empty_template_list_fails(self) -> None:
        self.assertTrue(ladder_schema.lint_notetype_feedback([], ("Rationale",)))

    def test_filtered_reference_counts(self) -> None:
        templates = [{"name": "T", "qfmt": "{{Prompt}}", "afmt": "{{text:Rationale}}"}]
        self.assertEqual(
            ladder_schema.lint_notetype_feedback(templates, ("Rationale",)), []
        )


class ClozeIndexTests(unittest.TestCase):
    def test_indices_extracted_distinct_sorted(self) -> None:
        self.assertEqual(
            ladder_schema.cloze_indices("{{c2::b}} {{c1::a}} {{c2::again}}"), [1, 2]
        )
        self.assertEqual(ladder_schema.cloze_indices("no cloze here"), [])
        self.assertEqual(ladder_schema.cloze_indices("{{c10::ten}}"), [10])


if __name__ == "__main__":
    unittest.main()
