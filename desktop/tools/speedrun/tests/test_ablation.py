# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Phase 3 M4 ablation harness: rigor mechanics.

Covers the spec'd invariants: determinism, the equal-budget invariant,
within-topic-only discrimination credit ([R8]), abstention accounting
(lenient vs strict [R1] gate), default-seed scoring sanity, and the report
schema (primary comparison flagged, disclosure present). Plus the
real-collection observational mode (--collection): feature-flag detection
from the deck-config blob, per-topic retention math on known rows,
abstention without probe outcomes, the observational disclaimer, and the
default-path (simulation) regression.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ablation
from ablation import (
    DEFAULT_BUDGET,
    DEFAULT_DAYS,
    DEFAULT_REPLICATIONS,
    DEFAULT_SEED,
    MS_PER_DAY,
    Item,
    adjacency_kind,
    analyze_real_collection,
    arm_for_features,
    build_item_bank,
    default_arms,
    render_markdown,
    render_real_markdown,
    run_ablation,
    simulate_arm,
)

# small-but-alive parameters for the fast tests (strict gate can clear:
# >= 300 reviews needs days*budget >= 300, probes need >= 50 in-window)
FAST = dict(seed=123, days=45, budget=30, replications=2)


def fast_report(**overrides) -> dict:
    params = {**FAST, **overrides}
    return run_ablation(**params)


def arm_by_name(name: str) -> ablation.ArmSpec:
    return next(arm for arm in default_arms() if arm.name == name)


def run_one_arm(
    name: str, seed: int = 123, replication: int = 0, days: int = 45, budget: int = 30
) -> ablation.RepResult:
    bank, units, concept_rungs = build_item_bank()
    return simulate_arm(
        arm_by_name(name), bank, units, concept_rungs, seed, replication, days, budget
    )


class DefaultRun(unittest.TestCase):
    """Tests against the actual default-seed run (the shipped numbers).

    The full default run (~5s) happens once for the class; assertions are
    made on its real output rather than on re-randomized small runs, per
    the M4 spec ("assert on the actual default-seed output").
    """

    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_ablation()

    # -- equal budget --------------------------------------------------------

    def test_equal_budget_across_arms(self) -> None:
        expected = DEFAULT_DAYS * DEFAULT_BUDGET
        for name, block in self.report["arms"].items():
            self.assertEqual(
                block["presentations_per_replication"],
                expected,
                f"arm {name} budget mismatch",
            )

    # -- scoring sanity ------------------------------------------------------

    def test_full_on_at_least_vanilla_on_memory(self) -> None:
        full = self.report["arms"]["full_on"]["memory"]["mean"]
        van = self.report["arms"]["vanilla"]["memory"]["mean"]
        self.assertGreaterEqual(full, van)

    def test_primary_comparison_positive_on_default_seed(self) -> None:
        self.assertGreater(self.report["primary_comparison"]["delta"]["mean"], 0.0)

    def test_contrast_reduces_confusion_errors(self) -> None:
        con = self.report["arms"]["contrast_on"]["confusion_error_rate"]["mean"]
        van = self.report["arms"]["vanilla"]["confusion_error_rate"]["mean"]
        self.assertLess(con, van)

    def test_cross_topic_does_not_beat_contrast_on(self) -> None:
        leak = self.report["arms"]["cross_topic_leakage"]
        con = self.report["arms"]["contrast_on"]
        self.assertLessEqual(
            leak["delayed_performance"]["mean"],
            con["delayed_performance"]["mean"],
        )
        # ... and it wastes adjacency slots that within-topic contrast doesn't
        self.assertGreater(
            leak["adjacency"]["wasted_pairs"]["mean"],
            con["adjacency"]["wasted_pairs"]["mean"],
        )
        # ... buying no extra true discrimination
        self.assertLessEqual(
            leak["mean_discrimination"]["mean"],
            con["mean_discrimination"]["mean"],
        )

    # -- report schema ---------------------------------------------------------

    def test_report_schema(self) -> None:
        report = self.report
        for key in (
            "schema",
            "simulation_disclosure",
            "config",
            "item_bank",
            "primary_comparison",
            "arms",
            "spov_contributions",
            "abstention_analysis",
            "limitations",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["schema"], "speedrun-ablation-v1")

        # the primary comparison is flagged and states the metric ahead
        primary = report["primary_comparison"]
        self.assertTrue(primary["preregistered"])
        self.assertEqual(primary["metric"], "delayed_performance")
        for key in ("statement", "full_on", "vanilla", "delta", "note"):
            self.assertIn(key, primary)

        # the simulation status is disclosed prominently
        self.assertTrue(report["simulation_disclosure"]["is_simulation"])
        self.assertIn("SIMULATION", report["simulation_disclosure"]["headline"])

        # all named arms are present, with the scored metrics
        expected_arms = {
            "vanilla",
            "contrast_on",
            "fade_on",
            "full_on",
            "cross_topic_leakage",
        }
        self.assertTrue(expected_arms.issubset(report["arms"].keys()))
        for name, block in report["arms"].items():
            for key in (
                "memory",
                "delayed_performance",
                "readiness_brier",
                "confusion_error_rate",
                "abstention",
            ):
                self.assertIn(key, block, f"{name} missing {key}")

        # per-SPOV table covers all three features, both baselines
        features = {row["feature"] for row in report["spov_contributions"]}
        self.assertEqual(features, {"contrast", "fade", "allocation"})
        for row in report["spov_contributions"]:
            self.assertIn("vs_vanilla", row)
            self.assertIn("within_full_on", row)

        self.assertEqual(report["config"]["seed"], DEFAULT_SEED)
        self.assertEqual(report["config"]["replications"], DEFAULT_REPLICATIONS)

    def test_report_is_json_serializable_and_renders(self) -> None:
        json.dumps(self.report)  # would raise on non-serializable content
        md = render_markdown(self.report)
        self.assertIn("SIMULATION", md)
        self.assertIn("Pre-registered primary comparison", md)
        self.assertIn("full_on", md)

    # -- abstention accounting (aggregated view) -----------------------------

    def test_overclaim_fraction_in_unit_interval(self) -> None:
        for name, block in self.report["arms"].items():
            fraction = block["abstention"]["overclaim_fraction"]["mean"]
            self.assertGreaterEqual(fraction, 0.0, name)
            self.assertLessEqual(fraction, 1.0, name)

    def test_lenient_emits_at_least_as_often_as_strict(self) -> None:
        for name, block in self.report["arms"].items():
            ab = block["abstention"]
            self.assertGreaterEqual(
                ab["lenient_emit_days"]["mean"],
                ab["strict_emit_days"]["mean"],
                name,
            )


class Determinism(unittest.TestCase):
    def test_same_seed_same_report(self) -> None:
        a = fast_report()
        b = fast_report()
        self.assertEqual(a, b)
        # byte-identical serialization too (what lands in eval/)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_different_seed_different_report(self) -> None:
        a = fast_report()
        c = fast_report(seed=FAST["seed"] + 1)
        self.assertNotEqual(a, c)


class EqualBudget(unittest.TestCase):
    def test_every_arm_presents_exactly_budget_times_days(self) -> None:
        bank, units, concept_rungs = build_item_bank()
        days, budget = 30, 25
        for arm in default_arms():
            result = simulate_arm(arm, bank, units, concept_rungs, 7, 0, days, budget)
            self.assertEqual(result.presentations, days * budget, f"arm {arm.name}")


class WithinTopicOnly(unittest.TestCase):
    """[R8]: adjacency credit never crosses a topic boundary."""

    def test_adjacency_kind_classification(self) -> None:
        fi_a = Item(
            0, "fixed_income", "duration", ("fixed_income", "duration"), None, None
        )
        fi_b = Item(
            1, "fixed_income", "duration", ("fixed_income", "duration"), None, None
        )
        eq = Item(
            2,
            "equity_investments",
            "duration",
            ("equity_investments", "duration"),
            None,
            None,
        )
        other = Item(
            3, "economics", "goods_types", ("economics", "goods_types"), None, None
        )
        plain = Item(4, "ethics", None, None, None, None)
        # same true cluster -> trains discrimination
        self.assertEqual(adjacency_kind(fi_a, fi_b), "true")
        # same family name, different topic -> wasted (no credit)
        self.assertEqual(adjacency_kind(fi_a, eq), "wasted")
        # different family -> nothing
        self.assertIsNone(adjacency_kind(fi_a, other))
        # unclustered -> nothing
        self.assertIsNone(adjacency_kind(plain, fi_a))
        self.assertIsNone(adjacency_kind(None, fi_a))

    def test_cross_topic_arm_gains_no_cross_topic_discrimination(self) -> None:
        # run at the object level so the learner state can be inspected
        bank, units, concept_rungs = build_item_bank()
        sim = ablation.ArmSimulation(
            arm_by_name("cross_topic_leakage"),
            bank,
            units,
            concept_rungs,
            seed=123,
            replication=0,
            days=30,
            budget=30,
        )
        result = sim.run()
        # discrimination state is keyed by topic-scoped clusters only: the
        # leakage arm cannot even represent a cross-topic pool ([R8])
        true_clusters = {item.cluster for item in bank if item.cluster}
        self.assertTrue(set(sim.learner.discrimination.keys()).issubset(true_clusters))
        # and its wasted adjacency slots are real (it does pay the cost)
        self.assertGreater(result.adjacency_wasted, 0)

    def test_leakage_wastes_more_than_within_topic_contrast(self) -> None:
        leak = run_one_arm("cross_topic_leakage")
        contrast = run_one_arm("contrast_on")
        self.assertGreater(leak.adjacency_wasted, contrast.adjacency_wasted)
        self.assertLessEqual(leak.mean_discrimination, contrast.mean_discrimination)


class AbstentionAccounting(unittest.TestCase):
    """Lenient vs strict gate bookkeeping on per-day gauge trajectories."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_one_arm("full_on")

    def test_strict_never_emits_before_gates_clear(self) -> None:
        for gauge in self.result.daily:
            if gauge.strict_emitted:
                self.assertTrue(
                    all(gauge.gates_pass.values()),
                    f"day {gauge.day}: strict emitted with a failed gate "
                    f"{gauge.gates_pass}",
                )
            else:
                self.assertIsNone(gauge.strict_p)

    def test_strict_gate_thresholds_enforced(self) -> None:
        for gauge in self.result.daily:
            if not gauge.strict_emitted:
                continue
            self.assertGreaterEqual(gauge.graded_reviews, ablation.STRICT_MIN_REVIEWS)
            self.assertGreaterEqual(gauge.coverage, ablation.STRICT_MIN_COVERAGE)
            self.assertGreaterEqual(gauge.delayed_probes, ablation.STRICT_MIN_PROBES)
            self.assertLessEqual(gauge.half_width, ablation.STRICT_MAX_HALF_WIDTH)

    def test_lenient_emits_whenever_strict_does(self) -> None:
        # strict's gates imply lenient's, so lenient emits >= as often
        for gauge in self.result.daily:
            if gauge.strict_emitted:
                self.assertTrue(gauge.lenient_emitted, f"day {gauge.day}")
        lenient_days = sum(1 for g in self.result.daily if g.lenient_emitted)
        strict_days = sum(1 for g in self.result.daily if g.strict_emitted)
        self.assertGreaterEqual(lenient_days, strict_days)
        self.assertEqual(self.result.lenient_days, lenient_days)
        self.assertEqual(self.result.strict_days, strict_days)

    def test_overclaim_accounting(self) -> None:
        overclaims = [
            g for g in self.result.daily if g.lenient_emitted and not g.strict_emitted
        ]
        self.assertEqual(self.result.overclaim_days, len(overclaims))
        fraction = self.result.overclaim_days / len(self.result.daily)
        self.assertGreaterEqual(fraction, 0.0)
        self.assertLessEqual(fraction, 1.0)
        if overclaims:
            self.assertIsNotNone(self.result.overclaim_brier)
            self.assertGreaterEqual(self.result.overclaim_brier, 0.0)
            self.assertLessEqual(self.result.overclaim_brier, 1.0)

    def test_strict_does_emit_once_evidence_accrues(self) -> None:
        # with 45 days x 30/day the gates do clear; the gauge is not
        # vacuously abstaining forever in this configuration
        self.assertIsNotNone(self.result.strict_first_emit)
        self.assertGreater(self.result.strict_first_emit, 0)


class ContrastPermutation(unittest.TestCase):
    """The contrast pass is a pure permutation (mirrors contrast.rs)."""

    def _items(self) -> list[Item]:
        fi = ("fixed_income", "duration")
        quant = ("quantitative_methods", "return_measures")
        items = []
        for idx in range(10):
            if idx % 3 == 0:
                items.append(Item(idx, fi[0], fi[1], fi, None, None))
            elif idx % 3 == 1:
                items.append(Item(idx, quant[0], quant[1], quant, None, None))
            else:
                items.append(Item(idx, "ethics", None, None, None, None))
        return items

    def test_permutation_and_adjacency(self) -> None:
        items = self._items()
        out = ablation.apply_contrast(items, "within_topic")
        # pure permutation: same multiset of items
        self.assertEqual(sorted(i.index for i in out), sorted(i.index for i in items))
        # same-cluster members form one adjacent run each (4 members < chunk)
        for cluster in {i.cluster for i in items if i.cluster}:
            positions = [pos for pos, item in enumerate(out) if item.cluster == cluster]
            width = positions[-1] - positions[0]
            self.assertEqual(
                width, len(positions) - 1, f"{cluster} not adjacent: {out}"
            )
        # background items keep their relative order
        background_in = [i.index for i in items if i.cluster is None]
        background_out = [i.index for i in out if i.cluster is None]
        self.assertEqual(background_in, background_out)

    def test_noop_without_clusters(self) -> None:
        items = [Item(i, "ethics", None, None, None, None) for i in range(5)]
        self.assertEqual(ablation.apply_contrast(items, "within_topic"), items)


# ---------------------------------------------------------------------------
# real-collection observational mode (--collection)
# ---------------------------------------------------------------------------


def _pb_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _pb_field_varint(number: int, value: int) -> bytes:
    return _pb_varint(number << 3) + _pb_varint(value)


def _pb_field_float(number: int, value: float) -> bytes:
    return _pb_varint(number << 3 | 5) + struct.pack("<f", value)


def _pb_field_bytes(number: int, payload: bytes) -> bytes:
    return _pb_varint(number << 3 | 2) + _pb_varint(len(payload)) + payload


def deck_config_blob(
    *,
    contrast: bool = False,
    fade: bool = False,
    allocation: bool = False,
    tag_prefix: str = "",
    fade_up_r: float = 0.0,
    fade_down_r: float = 0.0,
) -> bytes:
    """A DeckConfig.Config protobuf blob carrying only the Speedrun fields
    (proto3 omits defaults, so absent = off, like a real vanilla preset)."""
    blob = b""
    if contrast:
        blob += _pb_field_varint(47, 1)
    if tag_prefix:
        blob += _pb_field_bytes(48, tag_prefix.encode())
    if fade:
        blob += _pb_field_varint(50, 1)
    if fade_up_r:
        blob += _pb_field_float(52, fade_up_r)
    if fade_down_r:
        blob += _pb_field_float(53, fade_down_r)
    if allocation:
        blob += _pb_field_varint(59, 1)
    return blob


def deck_kind_blob(config_id: int) -> bytes:
    """Deck.kind with Normal.config_id (oneof field 1, nested field 1)."""
    return _pb_field_bytes(1, _pb_field_varint(1, config_id))


def _ms(day: int, hour: int = 12) -> int:
    """A revlog timestamp inside collection-local `day` (rollover 4am,
    creationOffset 0 in the fixtures below)."""
    return day * MS_PER_DAY + hour * 3_600_000


def make_real_collection(
    path: Path,
    *,
    contrast: bool = False,
    fade: bool = False,
    allocation: bool = False,
    revlog: list[tuple[int, int, int]] | None = None,
    with_probe: bool = False,
    calibration_record: dict | None = None,
) -> None:
    """A minimal modern-schema collection: deck_config protobuf presets,
    decks with kind blobs, config key/val table, tagged notes (mirrors how
    test_probe_harness fabricates collections, plus the deck-config side).

    Cards: 11 (fi::duration), 12 (fi::duration, sibling note), 13
    (eq::duration - same family, different cluster), 14 (untagged note),
    and with_probe adds probe card 15 (held-out, cluster fi::duration).
    """
    con = sqlite3.connect(path)
    con.execute("create table notes (id integer primary key, tags text)")
    con.execute("create table cards (id integer primary key, nid integer, did integer)")
    con.execute(
        "create table revlog (id integer primary key, cid integer, ease integer)"
    )
    con.execute(
        "create table deck_config (id integer primary key, name text, config blob)"
    )
    con.execute("create table decks (id integer primary key, name text, kind blob)")
    con.execute("create table config (key text primary key, val blob)")

    con.execute(
        "insert into deck_config values (1, 'Default', ?)",
        (deck_config_blob(contrast=contrast, fade=fade, allocation=allocation),),
    )
    con.execute("insert into decks values (10, 'CFA', ?)", (deck_kind_blob(1),))
    con.execute("insert into config values ('rollover', ?)", (b"4",))
    con.execute("insert into config values ('creationOffset', ?)", (b"0",))
    if calibration_record is not None:
        con.execute(
            "insert into config values ('speedrun:readinessCalibration', ?)",
            (json.dumps(calibration_record).encode(),),
        )

    con.execute(
        "insert into notes values (1, ' cfa::topic::fixed_income "
        "cluster::fi::duration ')"
    )
    con.execute(
        "insert into notes values (2, ' cfa::topic::fixed_income "
        "cluster::fi::duration ')"
    )
    con.execute(
        "insert into notes values (3, ' cfa::topic::equity_investments "
        "cluster::eq::duration ')"
    )
    con.execute("insert into notes values (4, ' untagged::note ')")
    con.execute("insert into cards values (11, 1, 10)")
    con.execute("insert into cards values (12, 2, 10)")
    con.execute("insert into cards values (13, 3, 10)")
    con.execute("insert into cards values (14, 4, 10)")
    if with_probe:
        con.execute(
            "insert into notes values (5, ' probe::held_out "
            "probe::pool::performance cfa::topic::fixed_income "
            "cluster::fi::duration probe::concept::c01 probe::variant::a ')"
        )
        con.execute("insert into cards values (15, 5, 10)")

    for ms, cid, ease in revlog or []:
        con.execute("insert into revlog values (?, ?, ?)", (ms, cid, ease))
    con.commit()
    con.close()


#: One known-answer revlog: day 10 sequence 11, 12 (true contrast pair),
#: 13 (same family, different cluster: wasted), 14, 14 (same card: no
#: pair); day 11: 11 again. 6 graded reviews, 4 correct.
KNOWN_REVLOG: list[tuple[int, int, int]] = [
    (_ms(10, 10), 11, 3),
    (_ms(10, 11), 12, 1),
    (_ms(10, 12), 13, 3),
    (_ms(10, 13), 14, 4),
    (_ms(10, 14), 14, 1),
    (_ms(11, 10), 11, 3),
]


class FeatureFlagDetection(unittest.TestCase):
    """Speedrun toggles decoded from the deck-config protobuf blob."""

    def analyze(self, tmp: str, **kwargs) -> dict:
        path = Path(tmp) / "collection.anki2"
        make_real_collection(path, revlog=KNOWN_REVLOG, **kwargs)
        return analyze_real_collection(path)

    def test_all_off_is_vanilla(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self.analyze(tmp)
        (preset,) = report["feature_states"]["presets"]
        self.assertEqual(
            preset["features"],
            {
                "contrastScheduling": False,
                "fadeEnabled": False,
                "readinessAllocation": False,
            },
        )
        self.assertEqual(preset["observational_arm"], "vanilla")
        self.assertEqual(report["feature_states"]["observational_arm"], "vanilla")
        self.assertIn("vanilla arm", report["feature_states"]["arm_note"])

    def test_all_on_is_full_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self.analyze(tmp, contrast=True, fade=True, allocation=True)
        (preset,) = report["feature_states"]["presets"]
        self.assertEqual(
            preset["features"],
            {
                "contrastScheduling": True,
                "fadeEnabled": True,
                "readinessAllocation": True,
            },
        )
        self.assertEqual(report["feature_states"]["observational_arm"], "full_on")

    def test_blob_detail_fields_decoded(self) -> None:
        blob = deck_config_blob(
            contrast=True, tag_prefix="cluster::", fade_up_r=0.9, fade_down_r=0.8
        )
        state = ablation._preset_feature_state(ablation._scan_message(blob))
        self.assertTrue(state["features"]["contrastScheduling"])
        self.assertFalse(state["features"]["fadeEnabled"])
        self.assertEqual(state["detail"]["contrastTagPrefix"], "cluster::")
        self.assertAlmostEqual(state["detail"]["fadeUpR"], 0.9, places=6)
        self.assertAlmostEqual(state["detail"]["fadeDownR"], 0.8, places=6)

    def test_every_feature_triple_maps_to_one_arm(self) -> None:
        expected = {
            (False, False, False): "vanilla",
            (True, False, False): "contrast_on",
            (False, True, False): "fade_on",
            (False, False, True): "allocation_on",
            (True, True, False): "full_minus_allocation",
            (True, False, True): "full_minus_fade",
            (False, True, True): "full_minus_contrast",
            (True, True, True): "full_on",
        }
        for triple, arm in expected.items():
            self.assertEqual(arm_for_features(*triple), arm, triple)

    def test_legacy_schema11_dconf_json(self) -> None:
        """Old collections store presets as JSON in col.dconf, with the
        schema11 camelCase names."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.anki2"
            con = sqlite3.connect(path)
            con.execute("create table notes (id integer primary key, tags text)")
            con.execute(
                "create table cards (id integer primary key, nid integer, did integer)"
            )
            con.execute(
                "create table revlog (id integer primary key, cid integer, "
                "ease integer)"
            )
            con.execute("create table col (conf text, dconf text, decks text)")
            dconf = {
                "1": {
                    "name": "Legacy",
                    "contrastScheduling": True,
                    "fadeEnabled": False,
                    "readinessAllocation": True,
                }
            }
            decks = {"10": {"name": "CFA", "conf": 1}}
            con.execute(
                "insert into col values (?, ?, ?)",
                (json.dumps({"rollover": 4}), json.dumps(dconf), json.dumps(decks)),
            )
            con.commit()
            con.close()
            report = analyze_real_collection(path)
        (preset,) = report["feature_states"]["presets"]
        self.assertEqual(preset["name"], "Legacy")
        self.assertTrue(preset["features"]["contrastScheduling"])
        self.assertTrue(preset["features"]["readinessAllocation"])
        self.assertEqual(preset["observational_arm"], "full_minus_fade")


class RealCollectionMetrics(unittest.TestCase):
    """Known-answer outcome math over the fixture revlog."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "collection.anki2"
        make_real_collection(path, contrast=True, revlog=KNOWN_REVLOG)
        cls.report = analyze_real_collection(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_overall_retention(self) -> None:
        memory = self.report["memory"]
        self.assertEqual(memory["n"], 6)
        self.assertEqual(memory["correct"], 4)
        self.assertAlmostEqual(memory["retention"], 4 / 6, places=6)
        self.assertAlmostEqual(memory["again_rate"], 2 / 6, places=6)
        self.assertEqual(memory["study_days"], 2)
        self.assertEqual(memory["cards_touched"], 4)

    def test_per_topic_retention(self) -> None:
        per_topic = self.report["memory"]["per_topic"]
        fi = per_topic["fixed_income"]  # cards 11, 12, 11 -> 2 of 3
        self.assertEqual((fi["n"], fi["correct"], fi["cards"]), (3, 2, 2))
        self.assertAlmostEqual(fi["retention"], 2 / 3, places=6)
        eq = per_topic["equity_investments"]  # card 13 -> 1 of 1
        self.assertEqual((eq["n"], eq["correct"]), (1, 1))
        untagged = per_topic["(no cfa::topic tag)"]  # card 14 -> 1 of 2
        self.assertEqual((untagged["n"], untagged["correct"]), (2, 1))
        self.assertAlmostEqual(untagged["retention"], 0.5, places=6)

    def test_adjacency_known_pairs(self) -> None:
        adjacency = self.report["adjacency"]
        self.assertTrue(adjacency["applicable"])
        # day-10 consecutive pairs: (11,12) (12,13) (13,14) (14,14);
        # the day-11 review pairs with nothing
        self.assertEqual(adjacency["same_day_pairs"], 4)
        self.assertEqual(adjacency["true_pairs"], 1)  # 11->12 same cluster
        self.assertEqual(adjacency["wasted_pairs"], 1)  # 12->13 family only
        self.assertAlmostEqual(adjacency["true_share"], 0.25, places=6)

    def test_adjacency_not_applicable_when_feature_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, contrast=False, revlog=KNOWN_REVLOG)
            report = analyze_real_collection(path)
        adjacency = report["adjacency"]
        self.assertFalse(adjacency["applicable"])
        self.assertEqual(
            adjacency["note"], "not applicable (feature off for all history)"
        )
        self.assertNotIn("true_pairs", adjacency)


class RealCollectionAbstention(unittest.TestCase):
    """No data -> a stated abstention, never a guessed number."""

    def test_abstains_without_probe_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, revlog=KNOWN_REVLOG)
            report = analyze_real_collection(path)
        delayed = report["delayed_performance"]
        self.assertTrue(delayed["abstained"])
        self.assertIn("no real bridge measurement yet", delayed["abstain_reason"])
        self.assertEqual(delayed["probe_cards"], 0)
        md = render_real_markdown(report)
        self.assertIn("ABSTAIN", md)
        self.assertIn("no real bridge measurement yet", md)

    def test_abstains_with_probe_cards_but_no_delayed_outcomes(self) -> None:
        # probe card exists but was never answered -> still an abstention
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, revlog=KNOWN_REVLOG, with_probe=True)
            report = analyze_real_collection(path)
        delayed = report["delayed_performance"]
        self.assertTrue(delayed["abstained"])
        self.assertEqual(delayed["probe_cards"], 1)
        self.assertIn("0 delayed probe outcomes", delayed["abstain_reason"])

    def test_delayed_outcome_via_probe_harness_import(self) -> None:
        # study touch day 10, probe answered day 19 (lag 9 >= 7): the
        # imported probe_harness rule must yield x=1 of n=1
        revlog = KNOWN_REVLOG + [(_ms(19), 15, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, revlog=revlog, with_probe=True)
            report = analyze_real_collection(path)
        delayed = report["delayed_performance"]
        self.assertFalse(delayed["abstained"])
        self.assertEqual(delayed["readiness_inputs"], {"x_correct": 1, "n_delayed": 1})
        # ... and the probe answer is never a study review
        self.assertEqual(report["memory"]["n"], 6)
        self.assertEqual(report["collection"]["graded_probe_answers"], 1)

    def test_calibration_record_status(self) -> None:
        record = {
            "fitted_at": "2026-07-04",
            "brier": 0.2,
            "log_loss": 0.5,
            "n": 12,
            "temperature": 1.5,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, revlog=KNOWN_REVLOG, calibration_record=record)
            report = analyze_real_collection(path)
            self.assertTrue(report["readiness_calibration"]["present"])
            self.assertEqual(report["readiness_calibration"]["record"], record)
            # absent case
            bare = Path(tmp) / "bare.anki2"
            make_real_collection(bare, revlog=KNOWN_REVLOG)
            bare_report = analyze_real_collection(bare)
        self.assertFalse(bare_report["readiness_calibration"]["present"])
        self.assertIsNone(bare_report["readiness_calibration"]["record"])


class RealCollectionReport(unittest.TestCase):
    """Report shape: the observational disclaimer is unmissable."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "collection.anki2"
        make_real_collection(path, contrast=True, revlog=KNOWN_REVLOG)
        cls.report = analyze_real_collection(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_disclosure_headline(self) -> None:
        disclosure = self.report["observational_disclosure"]
        self.assertFalse(disclosure["is_ablation"])
        self.assertIn("NOT an ablation", disclosure["headline"])
        self.assertIn("no counterfactual arm", disclosure["headline"])
        self.assertIn("n=1 with 6 graded reviews", disclosure["headline"])

    def test_schema_and_sections(self) -> None:
        self.assertEqual(self.report["schema"], "speedrun-ablation-real-v1")
        for key in (
            "observational_disclosure",
            "collection",
            "feature_states",
            "memory",
            "delayed_performance",
            "readiness_calibration",
            "adjacency",
            "limitations",
        ):
            self.assertIn(key, self.report)
        self.assertEqual(self.report["collection"]["opened"].split()[-1], "(read-only)")

    def test_markdown_renders_disclaimer_and_serializes(self) -> None:
        json.dumps(self.report)  # would raise on non-serializable content
        md = render_real_markdown(self.report)
        self.assertIn("NOT an ablation", md)
        self.assertIn("OBSERVATIONAL", md)
        self.assertIn("observational arm", md)
        self.assertIn("| fixed_income |", md)
        self.assertNotIn("SIMULATION ONLY", md)  # never claims to simulate

    def test_collection_mode_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            make_real_collection(path, revlog=KNOWN_REVLOG)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ablation.main(["--collection", str(path), "--output-dir", tmp])
            self.assertEqual(code, 0)
            self.assertIn("OBSERVATIONAL (not an ablation)", stdout.getvalue())
            real_json = Path(tmp) / "ablation_real_report.json"
            self.assertTrue(real_json.exists())
            self.assertTrue((Path(tmp) / "ablation_real_report.md").exists())
            # --collection must never write (or overwrite) the simulated pair
            self.assertFalse((Path(tmp) / "ablation_report.json").exists())
            report = json.loads(real_json.read_text())
            self.assertEqual(report["schema"], "speedrun-ablation-real-v1")


class SimulationDefaultPathRegression(unittest.TestCase):
    """main() without --collection still runs the simulation, unchanged."""

    def test_default_path_writes_simulated_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ablation.main(
                    [
                        "--seed",
                        "123",
                        "--days",
                        "10",
                        "--budget",
                        "8",
                        "--replications",
                        "2",
                        "--output-dir",
                        tmp,
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("SIMULATION ONLY", stdout.getvalue())
            report = json.loads((Path(tmp) / "ablation_report.json").read_text())
            md = (Path(tmp) / "ablation_report.md").read_text()
            # the real-report pair is not produced on the simulation path
            self.assertFalse((Path(tmp) / "ablation_real_report.json").exists())
        # unchanged simulated structure (same keys the M4 spec asserts on)
        for key in (
            "schema",
            "simulation_disclosure",
            "config",
            "item_bank",
            "primary_comparison",
            "arms",
            "spov_contributions",
            "abstention_analysis",
            "limitations",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["schema"], "speedrun-ablation-v1")
        self.assertTrue(report["simulation_disclosure"]["is_simulation"])
        # the headline marks the report simulated and points at the companion
        self.assertIn("SIMULATED learner", md.splitlines()[0])
        self.assertIn("ablation_real_report.md", md.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
