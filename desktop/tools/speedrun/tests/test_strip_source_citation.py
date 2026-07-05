# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for strip_source_citation's pure transforms (stdlib only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import strip_source_citation as ss  # noqa: E402


class TemplateStripTests(unittest.TestCase):
    def test_removes_conditional_source_block(self):
        afmt = (
            '<div class="sr-feedback">\n'
            '<div class="sr-rationale">Why: {{Rationale}}</div>\n'
            '{{#Source}}<div class="sr-source">{{Source}}</div>{{/Source}}\n'
            "</div>"
        )
        out = ss.strip_source_from_template(afmt)
        self.assertNotIn("sr-source", out)
        self.assertNotIn("{{#Source}}", out)
        self.assertNotIn("{{Source}}", out)
        self.assertIn("{{Rationale}}", out)

    def test_idempotent_when_already_clean(self):
        afmt = '<div class="sr-rationale">{{Rationale}}</div>'
        self.assertEqual(ss.strip_source_from_template(afmt), afmt)


class FieldStripTests(unittest.TestCase):
    def test_removes_backextra_citation_small(self):
        html = (
            "The divisor is 1.02.<br>\n"
            '<small><span class="sr-source-ref">duration.md, #x</span> '
            '<span class="sr-source-passage">&ldquo;a long passage&rdquo;</span>'
            "</small>"
        )
        out = ss.strip_source_from_field_html(html)
        self.assertNotIn("sr-source", out)
        self.assertNotIn("passage", out)
        self.assertIn("The divisor is 1.02.", out)

    def test_leaves_unrelated_html_untouched(self):
        html = "Just a rationale with no citation."
        self.assertEqual(ss.strip_source_from_field_html(html), html)


class TopicTagTests(unittest.TestCase):
    def test_extracts_canonical_topic_tag(self):
        self.assertEqual(
            ss.topic_tag_from_tags(
                ["rung::solve", "cfa::topic::fixed_income", "cluster::fi::duration"]
            ),
            "cfa::topic::fixed_income",
        )

    def test_empty_when_no_topic_tag(self):
        self.assertEqual(ss.topic_tag_from_tags(["rung::worked", "marked"]), "")

    def test_is_speedrun_notetype(self):
        self.assertTrue(ss._is_speedrun_notetype("Speedrun Worked+"))
        self.assertTrue(ss._is_speedrun_notetype("Speedrun Solve MCQ"))
        self.assertFalse(ss._is_speedrun_notetype("Basic (and reversed card)+"))
        self.assertFalse(ss._is_speedrun_notetype("Cloze++"))


if __name__ == "__main__":
    unittest.main()
