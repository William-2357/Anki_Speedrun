# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for injection_eval: the prompt-injection resistance eval.

stdlib only; run from desktop/ with:
    python3 -m unittest discover -s tools/speedrun/tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import injection_eval as ie  # noqa: E402


class PayloadInventoryTests(unittest.TestCase):
    def test_payloads_cover_distinct_techniques(self):
        self.assertGreaterEqual(len(ie.PAYLOADS), 6)
        techniques = {p.technique for p in ie.PAYLOADS}
        self.assertEqual(len(techniques), len(ie.PAYLOADS))
        markers = {p.marker for p in ie.PAYLOADS}
        self.assertEqual(len(markers), len(ie.PAYLOADS))

    def test_markers_are_present_in_their_text(self):
        for p in ie.PAYLOADS:
            self.assertIn(p.marker, p.text)


class SpyBackendTests(unittest.TestCase):
    def test_spy_records_every_prompt_and_delegates(self):
        from aig import models

        spy = ie.SpyBackend(inner=models.MockBackend())
        spy.complete("hello", sample_index=0)
        spy.complete("world", sample_index=1)
        self.assertEqual(spy.prompts, ["hello", "world"])


class SurfaceAGeneratorTests(unittest.TestCase):
    def test_no_payload_marker_leaks_into_any_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            result = ie.surface_a_generator(Path(td) / "A")
        self.assertTrue(result["passed"], result)
        leak_finding = next(
            f for f in result["findings"] if "generation prompt" in f["check"]
        )
        self.assertEqual(leak_finding["payloads_leaked_into_prompts"], [])
        self.assertGreater(leak_finding["prompts_captured"], 0)

    def test_poisoned_passage_is_escaped_and_grounding_is_model_free(self):
        with tempfile.TemporaryDirectory() as td:
            result = ie.surface_a_generator(Path(td) / "A")
        f = next(
            fd for fd in result["findings"] if "grounding is model-free" in fd["check"]
        )
        self.assertFalse(f["rendered_contains_raw_markup"])
        self.assertFalse(f["grounder_exposes_a_model_call"])

    def test_leakage_wall_rejects_verbatim_copy(self):
        with tempfile.TemporaryDirectory() as td:
            result = ie.surface_a_generator(Path(td) / "A")
        f = next(fd for fd in result["findings"] if "leakage wall" in fd["check"])
        self.assertTrue(f["passed"])
        self.assertFalse(f["gate_passed_item"])  # the copying item is rejected


class AdversarialBackendTests(unittest.TestCase):
    """The compromised backends must actually emit the attacker's payload, so
    the surfaces are really testing the validators, not a benign reply."""

    def test_injected_topic_backend_emits_out_of_set_and_unknown(self):
        import json

        b = ie.InjectedTopicBackend(id_keys=["1", "2"])
        reply = json.loads(b.complete("anything"))
        sugg = reply["suggestions"]
        self.assertIn("999999_unknown_injected_id", sugg)
        self.assertTrue(
            all(v["topic"] not in ie.TOPICS for k, v in sugg.items() if k in ("1", "2"))
        )

    def test_injected_number_backend_emits_ungrounded_number(self):
        b = ie.InjectedNumberBackend()
        self.assertIn("0.9731", b.complete("anything"))


class SurfaceBTests(unittest.TestCase):
    def test_onboarding_drops_everything_from_a_compromised_backend(self):
        result = ie.surface_b_onboarding()
        self.assertTrue(result["passed"], result)
        f = result["findings"][0]
        self.assertEqual(f["kept_count"], 0)
        self.assertFalse(f["kept_out_of_set_topic"])
        self.assertFalse(f["kept_unknown_id"])

    def test_tag_suggest_drops_everything_from_a_compromised_backend(self):
        result = ie.surface_b_tag_suggest()
        self.assertTrue(result["passed"], result)
        f = result["findings"][0]
        self.assertEqual(f["kept_count"], 0)
        self.assertFalse(f["kept_out_of_set_topic"])
        self.assertFalse(f["kept_unknown_tag"])


class SurfaceCTests(unittest.TestCase):
    def test_assistant_abstains_on_injected_number(self):
        result = ie.surface_c_number_grounding()
        self.assertTrue(result["passed"], result)
        f = result["findings"][0]
        self.assertTrue(f["abstained"])
        self.assertFalse(f["returned_reply"])


class ReportTests(unittest.TestCase):
    def test_run_all_passes_and_report_renders(self):
        with tempfile.TemporaryDirectory() as td:
            report = ie.run_all(Path(td))
        self.assertTrue(report["all_passed"], report)
        self.assertEqual(len(report["surfaces"]), 4)
        md = ie.render_markdown(report)
        self.assertIn("Prompt-injection resistance eval", md)
        self.assertIn("PASS", md)
        # every surface appears in the rendered table
        for s in report["surfaces"]:
            self.assertIn(s["surface"], md)

    def test_write_reports_emits_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            report = ie.run_all(Path(td))
            jp, mp = ie.write_reports(report, Path(td) / "eval")
            self.assertTrue(jp.exists() and mp.exists())

    def test_main_exit_zero_on_all_pass(self):
        with tempfile.TemporaryDirectory() as td:
            rc = ie.main(["--report-dir", td, "--quiet"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
