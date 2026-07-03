# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the parameterized numeric generators (aig/generators.py)."""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aig import generators as G  # noqa: E402


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_items(self) -> None:
        a = G.generate_all(seed=123)
        b = G.generate_all(seed=123)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_different_seed_different_numbers(self) -> None:
        a = G.generate_all(seed=1)
        b = G.generate_all(seed=2)
        self.assertNotEqual(
            json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True)
        )


class SliceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.items = G.generate_all()

    def test_volume_and_clusters(self) -> None:
        self.assertGreaterEqual(len(self.items), 50)
        self.assertLessEqual(len(self.items), 80)
        clusters = {i["cluster"] for i in self.items}
        self.assertEqual(clusters, {"fi::duration", "qm::tvm", "fsa::inventory"})

    def test_compare_items_present(self) -> None:
        compare = [i for i in self.items if i["kind"] == "compare"]
        self.assertGreaterEqual(len(compare), 2)
        clusters = {i["cluster"] for i in compare}
        self.assertIn("fi::duration", clusters)  # duration trio [R20]
        self.assertIn("fsa::inventory", clusters)  # FIFO/LIFO [R20]

    def test_interactivity_tagging(self) -> None:
        for item in self.items:
            if item["kind"] == "compare":
                self.assertEqual(item["interactivity"], "low")
            elif item["cluster"] in ("fi::duration", "qm::tvm"):
                self.assertEqual(item["interactivity"], "high")

    def test_provenance_marks_ungraded_param_generator(self) -> None:
        for item in self.items:
            prov = item["provenance"]
            self.assertFalse(prov["graded"])  # [R24] never graded at authoring
            self.assertTrue(prov["generator"].startswith("param:"))
            self.assertTrue(prov["generator"].endswith("_v1"))

    def test_private_keys_stripped(self) -> None:
        for item in self.items:
            public = G.strip_private(item)
            self.assertNotIn("_aig", public)


class NumericValidationTests(unittest.TestCase):
    def test_all_generators_validate_many_draws(self) -> None:
        for gen in G.GENERATORS:
            rng = random.Random(f"probe:{gen.name}")
            for _ in range(20):
                params = G._draw_valid(gen, rng)
                ok, reason = gen.validate_draw(params)
                self.assertTrue(ok, f"{gen.gen_id}: {reason}")

    def test_dual_implementations_agree(self) -> None:
        for gen in G.GENERATORS:
            rng = random.Random(f"agree:{gen.name}")
            params = G._draw_valid(gen, rng)
            a, b = gen.solve(params), gen.solve_independent(params)
            self.assertLess(
                abs(a - b) / max(abs(a), abs(b), 1.0),
                G.NUMERIC_TOLERANCE,
                f"{gen.gen_id} implementations disagree",
            )

    def test_intentionally_broken_generator_is_caught(self) -> None:
        """The dual-recompute check must catch a buggy implementation."""

        class Broken(G.ModDurationFromMac):
            def solve_independent(self, p):  # noqa: ANN001
                # An off-by-compounding bug: divides by (1+y) not (1+y/2).
                return p["mac"] / (1.0 + p["y"])

        gen = Broken()
        rng = random.Random("broken")
        params = gen.draw(rng)
        ok, reason = gen.validate_draw(params)
        self.assertFalse(ok)
        self.assertIn("independent recomputation disagrees", reason)
        with self.assertRaises(RuntimeError):
            G._draw_valid(gen, random.Random("broken2"), max_attempts=25)

    def test_distractor_margins_and_misconception_ids(self) -> None:
        for gen in G.GENERATORS:
            rng = random.Random(f"margin:{gen.name}")
            params = G._draw_valid(gen, rng)
            answer = gen.solve(params)
            values = [round(answer, gen.decimals)]
            for d in gen.distractors(params, answer):
                self.assertIn(d.misconception, G.MISCONCEPTIONS, gen.gen_id)
                self.assertTrue(d.why.strip())
                values.append(round(d.value, gen.decimals))
            self.assertTrue(
                G.margins_ok(values),
                f"{gen.gen_id}: distractors within 0.5% of each other",
            )

    def test_margins_ok_rejects_close_values(self) -> None:
        self.assertFalse(G.margins_ok([100.0, 100.2]))  # 0.2% apart
        self.assertTrue(G.margins_ok([100.0, 101.0]))  # 1% apart


class McqAssemblyTests(unittest.TestCase):
    def test_mcq_answer_key_consistency(self) -> None:
        for item in G.generate_all():
            if item["kind"] != "mcq":
                continue
            aig = item["_aig"]
            self.assertEqual(set(item["choices"]), {"A", "B", "C"})
            self.assertIn(item["correct"], "ABC")
            # The labelled letter carries the computed answer at display
            # precision; the two wrong letters carry misconception values.
            correct_value = aig["choice_values"][item["correct"]]
            self.assertEqual(correct_value, aig["correct_value"])
            wrong = set("ABC") - {item["correct"]}
            self.assertEqual(set(item["distractor_rationales"]), wrong)
            self.assertEqual(set(item["misconceptions"]), wrong)
            for mid in item["misconceptions"].values():
                self.assertIn(mid, G.MISCONCEPTIONS)

    def test_cloze_has_two_indices(self) -> None:
        for item in G.generate_all():
            if item["kind"] != "cloze":
                continue
            import re

            indices = set(re.findall(r"\{\{(c\d+)::", item["cloze_text"]))
            self.assertGreaterEqual(len(indices), 2, item["title"])


if __name__ == "__main__":
    unittest.main()
