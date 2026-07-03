# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""[R24] retirement tool: engineered discriminating vs. flat items."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from retire_items import Review, aig_note_ids, analyse, point_biserial


def synth_reviews() -> list[Review]:
    """20 days of history for three notes plus background cards.

    * note 1 (generated, discriminating): correct exactly on the days the
      background accuracy is high.
    * note 2 (generated, non-discriminating): alternates correctness in a
      fixed pattern unrelated to the background signal.
    * note 3 (background, not generated).
    """
    reviews: list[Review] = []
    for day in range(20):
        good_day = day % 2 == 0
        # background: 4 cards, all correct on good days, all wrong on bad days
        for card in range(4):
            reviews.append(
                Review(
                    card_id=100 + card,
                    note_id=3,
                    epoch_day=day,
                    correct=good_day,
                )
            )
        # discriminating generated item tracks the background signal
        reviews.append(Review(card_id=11, note_id=1, epoch_day=day, correct=good_day))
        # flat generated item ignores it (correct on days 0-1, wrong 2-3, ...)
        reviews.append(
            Review(card_id=12, note_id=2, epoch_day=day, correct=(day // 2) % 2 == 0)
        )
    return reviews


class RetireItemsTest(unittest.TestCase):
    def test_point_biserial_basics(self) -> None:
        self.assertIsNone(point_biserial([]))
        self.assertIsNone(point_biserial([(1.0, 0.5)]))
        # zero variance in the item -> None
        self.assertIsNone(point_biserial([(1.0, 0.2), (1.0, 0.9)]))
        perfect = [(1.0, 1.0), (0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        self.assertAlmostEqual(point_biserial(perfect), 1.0)

    def test_discriminating_item_kept_flat_item_retired(self) -> None:
        stats = analyse(
            synth_reviews(),
            generated_notes={1, 2},
            min_responses=6,
            threshold=0.1,
        )
        self.assertFalse(stats[1].retire, stats[1].reason)
        self.assertGreater(stats[1].point_biserial or 0.0, 0.9)
        self.assertTrue(stats[2].retire, stats[2].reason)

    def test_insufficient_data_abstains(self) -> None:
        reviews = synth_reviews()[:12]  # only two days
        stats = analyse(reviews, {1, 2}, min_responses=6, threshold=0.1)
        for nid in (1, 2):
            self.assertFalse(stats[nid].retire)
            self.assertIn("insufficient data", stats[nid].reason)

    def test_aig_note_detection(self) -> None:
        tags = {
            1: "cfa::topic::fixed_income aig::ungraded rung::solve",
            2: "cfa::topic::quant AIG::GRADED",
            3: "cfa::topic::quant aig::retired",
            4: "plain::tag",
        }
        self.assertEqual(aig_note_ids(tags), {1, 2})


if __name__ == "__main__":
    unittest.main()
