# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for probe_harness: the speedrun-probe-v1 validator, the
delay rule (mirroring rslib/src/readiness/probes.rs), calibration math,
the calibration config record contract, collection reading, and the
self-test path. stdlib only; run with:

    python3 -m unittest discover desktop/tools/speedrun/tests
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_harness  # noqa: E402
from probe_harness import (  # noqa: E402
    MS_PER_DAY,
    ProbeObs,
    apply_temperature,
    brier_score,
    build_calibration_record,
    calibrate,
    compute_outcomes,
    fit_temperature,
    log_loss,
    predict_p_correct,
    validate_bank,
    validate_probe,
)

BANK_PATH = Path(__file__).resolve().parents[1] / "probes" / "probe_bank.jsonl"
BANK, _load_failures = probe_harness.load_bank(BANK_PATH)
assert not _load_failures, _load_failures


def base_record() -> dict:
    """A minimal valid probe record."""
    return {
        "schema": "speedrun-probe-v1",
        "concept_id": "c01",
        "variant": "a",
        "pool": "performance",
        "topic": "fixed_income",
        "cluster": "fi::duration",
        "title": "Test probe",
        "stem": (
            "A bond position with a stated modified duration of 5.0 faces a "
            "yield increase of 100 basis points across the curve today."
        ),
        "choices": {"A": "-5.0%", "B": "+5.0%", "C": "-0.05%"},
        "correct": "A",
        "rationale": (
            "The first-order estimate is minus duration times the yield "
            "change. B is wrong on sign. C is wrong on scale."
        ),
        "provenance": {"author": "hand", "date": "2026-07-04"},
    }


class ValidateProbeTests(unittest.TestCase):
    def assert_rejected(self, record: dict, needle: str) -> None:
        errors = validate_probe(record)
        self.assertTrue(
            any(needle in error for error in errors),
            f"expected an error mentioning {needle!r}, got {errors}",
        )

    def test_base_record_is_valid(self) -> None:
        self.assertEqual(validate_probe(base_record()), [])

    def test_non_object_rejected(self) -> None:
        self.assertTrue(validate_probe("not a dict"))
        self.assertTrue(validate_probe(None))

    def test_wrong_schema_literal(self) -> None:
        record = base_record()
        record["schema"] = "speedrun-item-v1"
        self.assert_rejected(record, "schema")

    def test_bad_concept_ids(self) -> None:
        for bad in ("c00", "c36", "x01", "c1", "", None):
            record = base_record()
            record["concept_id"] = bad
            self.assert_rejected(record, "concept_id")

    def test_pool_partition_enforced_per_record(self) -> None:
        """Concept-disjointness is a validation rule, not a convention:
        a c26+ concept may not claim the performance pool and vice versa."""
        record = base_record()
        record["concept_id"] = "c30"  # calibration territory
        self.assert_rejected(record, "deterministic partition")
        record = base_record()
        record["pool"] = "calibration"  # c01 is performance territory
        self.assert_rejected(record, "deterministic partition")

    def test_bad_variant_and_pool_values(self) -> None:
        record = base_record()
        record["variant"] = "c"
        self.assert_rejected(record, "variant")
        record = base_record()
        record["pool"] = "holdout"
        self.assert_rejected(record, "pool")

    def test_bad_topic(self) -> None:
        record = base_record()
        record["topic"] = "fixed income"
        self.assert_rejected(record, "topic")

    def test_cluster_rules(self) -> None:
        record = base_record()
        record["cluster"] = "cluster::fi::duration"  # prefixed
        self.assert_rejected(record, "suffix")
        record = base_record()
        record["cluster"] = "duration"  # single component
        self.assert_rejected(record, "components")
        record = base_record()
        record["cluster"] = "fi::du ration"  # whitespace
        self.assert_rejected(record, "whitespace")
        record = base_record()
        record["cluster"] = "qm::tvm"  # prefix implies a different topic
        self.assert_rejected(record, "implies topic")

    def test_short_stem_rejected(self) -> None:
        record = base_record()
        record["stem"] = "Define modified duration."
        self.assert_rejected(record, "words")

    def test_choice_rules(self) -> None:
        record = base_record()
        record["choices"] = {"A": "x", "B": "y"}
        self.assert_rejected(record, "choices")
        record = base_record()
        record["choices"] = {"A": "same", "B": "same", "C": "other"}
        self.assert_rejected(record, "distinct")
        record = base_record()
        record["correct"] = "D"
        self.assert_rejected(record, "correct")

    def test_rationale_must_dismiss_each_distractor(self) -> None:
        record = base_record()
        record["rationale"] = "B is wrong on sign, and that is all."
        self.assert_rejected(record, "distractor C")

    def test_provenance_rules(self) -> None:
        record = base_record()
        record["provenance"] = {"author": "llm:gpt", "date": "2026-07-04"}
        self.assert_rejected(record, "hand")
        record = base_record()
        record["provenance"] = {"author": "hand", "date": "July 4"}
        self.assert_rejected(record, "date")
        record = base_record()
        record["provenance"] = {"author": "hand", "date": "2026-07-04", "x": 1}
        self.assert_rejected(record, "unexpected")

    def test_unknown_fields_rejected(self) -> None:
        record = base_record()
        record["tags_extra"] = ["aig::graded"]
        self.assert_rejected(record, "unknown fields")


class ValidateBankTests(unittest.TestCase):
    """Bank-level invariants, exercised by mutating copies of the real bank."""

    def test_real_bank_passes(self) -> None:
        self.assertEqual(validate_bank(BANK), [])

    def test_missing_record_fails_count_and_variants(self) -> None:
        bank = copy.deepcopy(BANK)[:-1]
        errors = validate_bank(bank)
        self.assertTrue(any("70 records" in e for e in errors))
        self.assertTrue(any("exactly variants a+b" in e for e in errors))

    def test_duplicate_variant_fails(self) -> None:
        bank = copy.deepcopy(BANK)
        bank[1] = copy.deepcopy(bank[0])  # two copies of c01a
        errors = validate_bank(bank)
        self.assertTrue(any("exactly variants a+b" in e for e in errors))

    def test_variant_pool_flip_fails_partition(self) -> None:
        bank = copy.deepcopy(BANK)
        victim = next(r for r in bank if r["concept_id"] == "c26")
        victim["pool"] = "performance"
        errors = validate_bank(bank)
        self.assertTrue(any("deterministic partition" in e for e in errors))

    def test_identical_variants_fail_divergence(self) -> None:
        bank = copy.deepcopy(BANK)
        pair = [r for r in bank if r["concept_id"] == "c01"]
        pair[1]["stem"] = pair[0]["stem"]
        errors = validate_bank(bank)
        self.assertTrue(any("too similar" in e for e in errors))

    def test_trivial_string_edit_fails_divergence(self) -> None:
        """Changing one number is not a rewording."""
        bank = copy.deepcopy(BANK)
        pair = [r for r in bank if r["concept_id"] == "c17"]
        pair[1]["stem"] = pair[0]["stem"].replace("8.40", "9.10")
        errors = validate_bank(bank)
        self.assertTrue(any("too similar" in e for e in errors))

    def test_duplicate_title_fails(self) -> None:
        bank = copy.deepcopy(BANK)
        bank[1]["title"] = bank[0]["title"]
        errors = validate_bank(bank)
        self.assertTrue(any("duplicate titles" in e for e in errors))


class DelayRuleTests(unittest.TestCase):
    """The outcome/delay rule must mirror rslib/src/readiness/probes.rs."""

    CLUSTER = "fi::duration"

    def outcomes_for(self, answers, study_days=(10,), cluster=None):
        cluster = cluster or self.CLUSTER
        probe = ProbeObs("c01a", "performance", "fixed_income", cluster, answers)
        study = {self.CLUSTER: [d * MS_PER_DAY for d in study_days]}
        return compute_outcomes([probe], study)

    def test_delayed_outcome_counts(self) -> None:
        result = self.outcomes_for([(19 * MS_PER_DAY, 3)])  # lag 9 days
        pool = result["pools"]["performance"]
        self.assertEqual(pool["delayed"], 1)
        self.assertEqual(pool["correct"], 1)
        self.assertEqual(result["rows"][0]["lag_days"], 9.0)

    def test_exactly_seven_days_is_delayed(self) -> None:
        result = self.outcomes_for([(17 * MS_PER_DAY, 1)])  # lag exactly 7.0
        pool = result["pools"]["performance"]
        self.assertEqual(pool["delayed"], 1)
        self.assertEqual(pool["correct"], 0)  # Again = wrong

    def test_just_under_seven_days_is_undelayed(self) -> None:
        answered = 10 * MS_PER_DAY + int(6.99 * MS_PER_DAY)
        result = self.outcomes_for([(answered, 3)])
        pool = result["pools"]["performance"]
        self.assertEqual(pool["undelayed"], 1)
        self.assertEqual(pool["delayed"], 0)
        self.assertEqual(pool["correct"], 0)  # excluded, not credited

    def test_never_studied_cluster_is_delayed_without_lag(self) -> None:
        result = self.outcomes_for([(3 * MS_PER_DAY, 3)], cluster="alt::pe_strategies")
        pool = result["pools"]["performance"]
        self.assertEqual(pool["delayed"], 1)
        self.assertEqual(pool["never_studied"], 1)
        self.assertNotIn("lag_days", result["rows"][0])
        self.assertEqual(pool["lag_distribution"], {"n": 0})

    def test_unanswered_probe(self) -> None:
        result = self.outcomes_for([])
        pool = result["pools"]["performance"]
        self.assertEqual(pool["unanswered"], 1)
        self.assertEqual(result["rows"][0]["status"], "unanswered")

    def test_first_graded_answer_is_the_outcome(self) -> None:
        """A wrong first answer is never rescued by later practice."""
        result = self.outcomes_for([(19 * MS_PER_DAY, 1), (25 * MS_PER_DAY, 3)])
        pool = result["pools"]["performance"]
        self.assertEqual(pool["delayed"], 1)
        self.assertEqual(pool["correct"], 0)

    def test_study_touch_strictly_before_answer(self) -> None:
        """A study review at the same millisecond is not 'before' the
        answer, matching the Rust partition_point(t < answered_at)."""
        result = self.outcomes_for([(10 * MS_PER_DAY, 3)], study_days=(10,))
        pool = result["pools"]["performance"]
        # no earlier study touch -> never-studied semantics
        self.assertEqual(pool["never_studied"], 1)

    def test_pools_are_tallied_separately(self) -> None:
        probes = [
            ProbeObs("c01a", "performance", "t", self.CLUSTER, [(19 * MS_PER_DAY, 3)]),
            ProbeObs("c26a", "calibration", "t", self.CLUSTER, [(19 * MS_PER_DAY, 3)]),
        ]
        study = {self.CLUSTER: [10 * MS_PER_DAY]}
        result = compute_outcomes(probes, study)
        self.assertEqual(result["pools"]["performance"]["delayed"], 1)
        self.assertEqual(result["pools"]["calibration"]["delayed"], 1)
        inputs = probe_harness.readiness_inputs(result)
        self.assertEqual(inputs, {"x_correct": 1, "n_delayed": 1})


class CalibrationMathTests(unittest.TestCase):
    def test_brier_known_answer(self) -> None:
        self.assertAlmostEqual(
            brier_score([(0.8, True), (0.4, False)]), 0.10, places=12
        )

    def test_log_loss_known_answer(self) -> None:
        expected = -(math.log(0.8) + math.log(0.6)) / 2.0
        self.assertAlmostEqual(
            log_loss([(0.8, True), (0.4, False)]), expected, places=12
        )

    def test_log_loss_clamps_extreme_predictions(self) -> None:
        self.assertTrue(math.isfinite(log_loss([(0.0, True), (1.0, False)])))

    def test_temperature_identity_on_half(self) -> None:
        self.assertAlmostEqual(apply_temperature(0.5, 7.0), 0.5, places=12)

    def test_temperature_softens_overconfidence(self) -> None:
        """Predictions at 0.9/0.1 with 50% accuracy: the fit must push
        toward 0.5 (T large) and reduce log-loss."""
        pairs = [
            (0.9, True),
            (0.9, False),
            (0.9, True),
            (0.9, False),
            (0.1, True),
            (0.1, False),
        ]
        temperature = fit_temperature(pairs)
        self.assertGreater(temperature, 5.0)
        after = log_loss([(apply_temperature(p, temperature), y) for p, y in pairs])
        self.assertLess(after, log_loss(pairs))

    def test_temperature_sharpens_underconfidence(self) -> None:
        """Predictions at 0.6 that are always right: T < 1 sharpens."""
        pairs = [(0.6, True)] * 8
        temperature = fit_temperature(pairs)
        self.assertLess(temperature, 1.0)
        after = log_loss([(apply_temperature(p, temperature), y) for p, y in pairs])
        self.assertLessEqual(after, log_loss(pairs))

    def test_temperature_never_worse_than_identity(self) -> None:
        pairs = [(0.7, True), (0.3, False), (0.6, True), (0.4, False)]
        temperature = fit_temperature(pairs)
        after = log_loss([(apply_temperature(p, temperature), y) for p, y in pairs])
        self.assertLessEqual(after, log_loss(pairs) + 1e-12)

    def test_prediction_proxy(self) -> None:
        reviews = {"fi::duration": [(1, 3), (2, 1), (3, 3)]}
        # 2 of 3 correct, add-one smoothed: (2+1)/(3+2)
        self.assertAlmostEqual(
            predict_p_correct("fi::duration", 10, reviews), 3 / 5, places=12
        )

    def test_prediction_ignores_reviews_at_or_after_answer(self) -> None:
        reviews = {"fi::duration": [(1, 3), (10, 1), (11, 1)]}
        # only the ms<10 review counts: (1+1)/(1+2)
        self.assertAlmostEqual(
            predict_p_correct("fi::duration", 10, reviews), 2 / 3, places=12
        )

    def test_prediction_window_is_twenty_reviews(self) -> None:
        old_wrong = [(ms, 1) for ms in range(100)]  # 100 lapses...
        recent_right = [(ms, 3) for ms in range(100, 120)]  # ...then 20 wins
        reviews = {"c::x": old_wrong + recent_right}
        # window keeps only the last 20 (all correct): (20+1)/(20+2)
        self.assertAlmostEqual(
            predict_p_correct("c::x", 1000, reviews), 21 / 22, places=12
        )

    def test_prediction_for_never_studied_cluster_is_chance(self) -> None:
        self.assertAlmostEqual(
            predict_p_correct("alt::pe_strategies", 10, {}), 1 / 3, places=12
        )


class CalibrationRecordContractTests(unittest.TestCase):
    """The config record must match rslib's CalibrationRecord EXACTLY."""

    PAIRS = [(0.7, True), (0.6, False), (0.8, True), (0.4, False)] * 3

    def test_exact_snake_case_shape(self) -> None:
        record = build_calibration_record(self.PAIRS, "2026-07-04")
        self.assertEqual(
            list(record), ["fitted_at", "brier", "log_loss", "n", "temperature"]
        )
        self.assertIsInstance(record["fitted_at"], str)
        self.assertRegex(record["fitted_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIsInstance(record["brier"], float)
        self.assertIsInstance(record["log_loss"], float)
        self.assertIsInstance(record["n"], int)
        self.assertIsInstance(record["temperature"], float)
        self.assertEqual(record["n"], len(self.PAIRS))

    def test_json_round_trip_preserves_contract(self) -> None:
        record = build_calibration_record(self.PAIRS, "2026-07-04")
        decoded = json.loads(json.dumps(record))
        self.assertEqual(
            set(decoded), {"fitted_at", "brier", "log_loss", "n", "temperature"}
        )
        self.assertEqual(decoded, record)

    def test_config_key_name(self) -> None:
        self.assertEqual(
            probe_harness.CALIBRATION_CONFIG_KEY, "speedrun:readinessCalibration"
        )


class CalibratePoolTests(unittest.TestCase):
    CLUSTER = "fi::duration"

    def _setup(self, calibration_delayed: int):
        """calibration_delayed calibration probes answered at lag 9 (all
        correct), plus one performance probe that must NOT be used."""
        study_reviews = {self.CLUSTER: [(day * MS_PER_DAY, 3) for day in range(1, 11)]}
        probes = [
            ProbeObs(
                "c01a",
                "performance",
                "t",
                self.CLUSTER,
                [(19 * MS_PER_DAY, 3)],
            )
        ]
        for index in range(calibration_delayed):
            probes.append(
                ProbeObs(
                    f"c26{chr(ord('a') + index)}",
                    "calibration",
                    "t",
                    self.CLUSTER,
                    [(19 * MS_PER_DAY, 3)],
                )
            )
        study_times = {
            cluster: [ms for ms, _ in reviews]
            for cluster, reviews in study_reviews.items()
        }
        outcomes = compute_outcomes(probes, study_times)
        return probes, outcomes, study_reviews

    def test_abstains_below_ten_outcomes(self) -> None:
        probes, outcomes, study_reviews = self._setup(calibration_delayed=9)
        result = calibrate(probes, outcomes, study_reviews, "2026-07-04")
        self.assertTrue(result["abstained"])
        self.assertIn("REFUSING", result["message"])
        self.assertNotIn("record", result)

    def test_fits_at_ten_outcomes_calibration_pool_only(self) -> None:
        probes, outcomes, study_reviews = self._setup(calibration_delayed=10)
        result = calibrate(probes, outcomes, study_reviews, "2026-07-04")
        self.assertFalse(result["abstained"])
        # the performance probe is excluded: 10 pairs, not 11
        self.assertEqual(result["outcomes_used"], 10)
        self.assertEqual(result["record"]["n"], 10)
        self.assertLessEqual(
            result["calibrated"]["log_loss"], result["raw"]["log_loss"] + 1e-9
        )

    def test_undelayed_calibration_outcomes_are_not_used(self) -> None:
        probes, outcomes, study_reviews = self._setup(calibration_delayed=10)
        # one more calibration probe answered at lag 2 (undelayed)
        probes.append(
            ProbeObs(
                "c27a",
                "calibration",
                "t",
                self.CLUSTER,
                [(12 * MS_PER_DAY, 3)],
            )
        )
        study_times = {
            cluster: [ms for ms, _ in reviews]
            for cluster, reviews in study_reviews.items()
        }
        outcomes = compute_outcomes(probes, study_times)
        result = calibrate(probes, outcomes, study_reviews, "2026-07-04")
        self.assertEqual(result["outcomes_used"], 10)


class BridgeProofTests(unittest.TestCase):
    CLUSTER = "fi::duration"

    def test_windowed_retention_and_gap(self) -> None:
        # 4 old reviews (outside the window) all wrong; 4 recent: 3 right
        reviews = [(day * MS_PER_DAY, 1) for day in range(4)]
        reviews += [
            (100 * MS_PER_DAY, 3),
            (110 * MS_PER_DAY, 3),
            (115 * MS_PER_DAY, 1),
            (120 * MS_PER_DAY, 3),
        ]
        study_reviews = {self.CLUSTER: reviews}
        probes = [
            ProbeObs(
                "c01a",
                "performance",
                "t",
                self.CLUSTER,
                [(130 * MS_PER_DAY, 1)],  # delayed (lag 10), wrong
            )
        ]
        outcomes = compute_outcomes(probes, {self.CLUSTER: [ms for ms, _ in reviews]})
        bridge = probe_harness.bridge_proof(outcomes, study_reviews, {self.CLUSTER})
        retention = bridge["retention"]
        # trailing 30d window ends at day 120 -> keeps the 4 recent reviews
        self.assertEqual(retention["n_reviews"], 4)
        self.assertAlmostEqual(retention["accuracy"], 0.75, places=6)
        performance = bridge["delayed_probe_accuracy"]["performance_pool"]
        self.assertEqual(performance["n"], 1)
        self.assertEqual(performance["accuracy"], 0.0)
        self.assertAlmostEqual(bridge["memory_minus_performance_gap"], 0.75, places=6)

    def test_abstains_without_data(self) -> None:
        outcomes = compute_outcomes([], {})
        bridge = probe_harness.bridge_proof(outcomes, {}, set())
        self.assertIsNone(bridge["retention"]["accuracy"])
        self.assertIsNone(bridge["memory_minus_performance_gap"])


class CollectionReadTests(unittest.TestCase):
    """read_collection against a minimal Anki-shaped SQLite fixture."""

    def _make_collection(self, path: Path) -> None:
        con = sqlite3.connect(path)
        con.execute("create table notes (id integer primary key, tags text)")
        con.execute("create table cards (id integer primary key, nid integer)")
        con.execute(
            "create table revlog (id integer primary key, cid integer, ease integer)"
        )
        day = MS_PER_DAY
        # note 1: a probe (performance pool)
        con.execute(
            "insert into notes values (1, ' probe::held_out "
            "probe::pool::performance cfa::topic::fixed_income "
            "cluster::fi::duration probe::concept::c01 probe::variant::a ')"
        )
        con.execute("insert into cards values (11, 1)")
        # note 2: a second probe in the same cluster, answered but its
        # answers must never count as study touches for probe 1
        con.execute(
            "insert into notes values (2, ' probe::held_out "
            "probe::pool::calibration cfa::topic::fixed_income "
            "cluster::fi::duration probe::concept::c26 probe::variant::a ')"
        )
        con.execute("insert into cards values (22, 2)")
        # note 3: a study card in the cluster
        con.execute(
            "insert into notes values (3, ' cfa::topic::fixed_income "
            "cluster::fi::duration ')"
        )
        con.execute("insert into cards values (33, 3)")
        # study touch day 10; probe 1 answered day 19 (delayed, Good);
        # probe 2 answered day 12 (undelayed); manual entry ease 0 ignored
        con.execute(f"insert into revlog values ({10 * day}, 33, 3)")
        con.execute(f"insert into revlog values ({19 * day}, 11, 3)")
        con.execute(f"insert into revlog values ({12 * day}, 22, 1)")
        con.execute(f"insert into revlog values ({20 * day}, 33, 0)")
        con.commit()
        con.close()

    def test_probes_and_study_reviews_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            self._make_collection(path)
            data = probe_harness.read_collection(path)
            probes = {obs.key: obs for obs in data["probes"]}
            self.assertEqual(set(probes), {"c01a", "c26a"})
            self.assertEqual(probes["c01a"].pool, "performance")
            self.assertEqual(probes["c01a"].cluster, "fi::duration")
            # probe cards are excluded from study evidence; ease-0 rows too
            self.assertEqual(
                data["study_reviews"], {"fi::duration": [(10 * MS_PER_DAY, 3)]}
            )

    def test_full_collection_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            self._make_collection(path)
            report = probe_harness.analyze_collection(path, "2026-07-04")
            self.assertEqual(report["probe_cards"], 2)
            self.assertEqual(
                report["readiness_inputs"], {"x_correct": 1, "n_delayed": 1}
            )
            pools = report["outcomes"]["pools"]
            self.assertEqual(pools["calibration"]["undelayed"], 1)
            self.assertTrue(report["calibration"]["abstained"])


class SelfTestTests(unittest.TestCase):
    def test_self_test_is_deterministic_and_green(self) -> None:
        first = probe_harness.run_self_test(BANK)
        second = probe_harness.run_self_test(BANK)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first["checks_passed"]), 8)
        # all 50 performance probes delayed: the Rust >=50 gate is shown
        # satisfiable by this bank
        self.assertEqual(first["readiness_inputs"]["n_delayed"], 50)
        self.assertFalse(first["calibration"]["abstained"])
        record = first["calibration"]["record"]
        self.assertEqual(
            list(record), ["fitted_at", "brier", "log_loss", "n", "temperature"]
        )
        self.assertEqual(record["fitted_at"], "2026-07-04")

    def test_main_default_mode_green_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = probe_harness.main(["--report-dir", tmp])
            self.assertEqual(code, 0, stderr.getvalue())
            report_path = Path(tmp) / "probe_harness_report.json"
            self.assertTrue(report_path.exists())
            self.assertTrue((Path(tmp) / "probe_harness_report.md").exists())
            report = json.loads(report_path.read_text())
            self.assertTrue(report["validation"]["passed"])
            self.assertTrue(report["leakage"]["passed"])
            self.assertIn("self_test", report)
            self.assertEqual(report["exit_code"], 0)

    def test_main_fails_on_invalid_bank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_bank = Path(tmp) / "bad.jsonl"
            record = base_record()
            record["pool"] = "calibration"  # violates the partition rule
            bad_bank.write_text(json.dumps(record) + "\n")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = probe_harness.main(
                    ["--items", str(bad_bank), "--report-dir", tmp]
                )
            self.assertEqual(code, 1)
            self.assertIn("VALIDATION FAILED", stderr.getvalue())

    def test_apply_requires_collection(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                probe_harness.main(["--apply"])


if __name__ == "__main__":
    unittest.main()
