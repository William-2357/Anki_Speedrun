// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Per-topic mastery metrics (Anki Speedrun).
//!
//! Groups the searched cards by the topic tag on their notes (default
//! `cfa::topic::*`) and returns, per topic, how many cards exist, how many
//! have been studied (carry an FSRS memory state), how many currently sit at
//! a high predicted recall probability, and the mean/spread of predicted
//! retrievability. Computed in one SQL pass so the dashboard stays fast on
//! large collections.
//!
//! Honesty notes:
//! * "high recall probability" is deliberately not called "mastered": FSRS
//!   retrievability is a scheduling target, not a competence threshold.
//! * The response carries `fsrs_enabled` and per-topic studied counts so the UI
//!   can abstain instead of showing proxy numbers when data is missing.
//! * The engine only aggregates by an opaque tag prefix; the CFA topic list,
//!   exam weights and any score mapping live in the frontend.

use std::collections::HashMap;

use anki_proto::stats::topic_mastery_response::Topic;
use anki_proto::stats::TopicMasteryResponse;

use crate::config::BoolKey;
use crate::prelude::*;
use crate::search::SortMode;

/// Cards at/above this predicted retrievability count as "high recall".
pub const DEFAULT_HIGH_RECALL_THRESHOLD: f32 = 0.9;
/// Used when the request does not specify a topic prefix.
pub const DEFAULT_TOPIC_TAG_PREFIX: &str = "cfa::topic::";

#[derive(Debug, Default)]
struct TopicAccumulator {
    total: u32,
    studied: u32,
    high_recall: u32,
    sum: f64,
    sum_squares: f64,
}

impl TopicAccumulator {
    fn add_card(&mut self, retrievability: Option<f32>, threshold: f32) {
        self.total += 1;
        if let Some(r) = retrievability {
            self.studied += 1;
            if r >= threshold {
                self.high_recall += 1;
            }
            self.sum += r as f64;
            self.sum_squares += (r as f64) * (r as f64);
        }
    }

    fn into_topic(self, name: String) -> Topic {
        let n = self.studied as f64;
        let average = if self.studied > 0 {
            (self.sum / n) as f32
        } else {
            0.0
        };
        let stddev = if self.studied > 1 {
            let variance = ((self.sum_squares - self.sum * self.sum / n) / (n - 1.0)).max(0.0);
            variance.sqrt() as f32
        } else {
            0.0
        };
        Topic {
            topic: name,
            total_cards: self.total,
            studied_cards: self.studied,
            high_recall_cards: self.high_recall,
            average_retrievability: average,
            retrievability_stddev: stddev,
        }
    }
}

impl Collection {
    /// Per-topic mastery over the cards matched by `search` (empty = whole
    /// collection). See [TopicMasteryResponse] in `stats.proto`.
    pub fn topic_mastery(
        &mut self,
        search: &str,
        topic_prefix: &str,
        high_recall_threshold: f32,
    ) -> Result<TopicMasteryResponse> {
        let threshold = if high_recall_threshold > 0.0 {
            high_recall_threshold
        } else {
            DEFAULT_HIGH_RECALL_THRESHOLD
        };
        let trimmed_prefix = topic_prefix.trim().to_ascii_lowercase();
        let prefix: &str = if trimmed_prefix.is_empty() {
            DEFAULT_TOPIC_TAG_PREFIX
        } else {
            &trimmed_prefix
        };

        let timing = self.timing_today()?;
        let mut accumulators: HashMap<String, TopicAccumulator> = HashMap::new();
        let mut total_cards = 0u32;
        let mut cards_without_topic = 0u32;
        {
            let guard = self.search_cards_into_table(search, SortMode::NoOrder)?;
            guard
                .col
                .storage
                .for_each_searched_card_tags_and_retrievability(
                    timing,
                    |tags, retrievability| {
                        total_cards += 1;
                        let topic = tags.split_whitespace().find_map(|tag| {
                            let tag = tag.to_ascii_lowercase();
                            tag.strip_prefix(prefix)
                                .filter(|rest| !rest.is_empty())
                                .map(ToString::to_string)
                        });
                        match topic {
                            Some(topic) => accumulators
                                .entry(topic)
                                .or_default()
                                .add_card(retrievability, threshold),
                            None => cards_without_topic += 1,
                        }
                    },
                )?;
            // guard dropped here; temporary search table cleaned up
        }

        let mut topics: Vec<Topic> = accumulators
            .into_iter()
            .map(|(name, acc)| acc.into_topic(name))
            .collect();
        topics.sort_by(|a, b| a.topic.cmp(&b.topic));

        Ok(TopicMasteryResponse {
            topics,
            cards_without_topic,
            total_cards,
            graded_reviews: self.storage.graded_review_count()?,
            fsrs_enabled: self.get_config_bool(BoolKey::Fsrs),
            high_recall_threshold: threshold,
        })
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::card::FsrsMemoryState;
    use crate::tests::NoteAdder;

    impl Collection {
        fn add_topic_note(&mut self, front: &str, tags: &[&str]) -> Note {
            NoteAdder::basic(self)
                .fields(&[front, "back"])
                .tags(tags)
                .add(self)
        }

        /// Give the note's first card an FSRS memory state whose last review
        /// was `days_ago`, so its current retrievability is deterministic.
        fn set_memory_state(&mut self, note: &Note, stability: f32, days_ago: i64) {
            let cid = self.storage.card_ids_of_notes(&[note.id]).unwrap()[0];
            self.get_and_update_card(cid, |card| {
                card.memory_state = Some(FsrsMemoryState {
                    stability,
                    difficulty: 5.0,
                });
                card.last_review_time = Some(TimestampSecs::now().adding_secs(-days_ago * 86_400));
                Ok(())
            })
            .unwrap();
        }
    }

    #[test]
    fn groups_cards_by_topic_and_abstains_on_unstudied() -> Result<()> {
        let mut col = Collection::new();
        // fixed income: one strong studied card, one unstudied card
        let strong = col.add_topic_note("duration", &["cfa::topic::fixed_income"]);
        col.add_topic_note("convexity", &["cfa::topic::fixed_income"]);
        // quant: one weak studied card (reviewed long ago, low stability)
        let weak = col.add_topic_note("bayes", &["cfa::topic::quant", "cluster::q::prob"]);
        // no topic tag at all
        col.add_topic_note("stray", &["random::tag"]);

        // reviewed just now with high stability -> retrievability ~1.0
        col.set_memory_state(&strong, 100.0, 0);
        // reviewed 60 days ago with 1-day stability -> retrievability ~0.0
        col.set_memory_state(&weak, 1.0, 60);

        let response = col.topic_mastery("", "", 0.0)?;
        assert_eq!(response.total_cards, 4);
        assert_eq!(response.cards_without_topic, 1);
        assert_eq!(
            response.high_recall_threshold,
            DEFAULT_HIGH_RECALL_THRESHOLD
        );
        assert_eq!(response.topics.len(), 2);

        let fixed_income = &response.topics[0];
        assert_eq!(fixed_income.topic, "fixed_income");
        assert_eq!(fixed_income.total_cards, 2);
        assert_eq!(fixed_income.studied_cards, 1);
        assert_eq!(fixed_income.high_recall_cards, 1);
        assert!(fixed_income.average_retrievability > 0.95);

        let quant = &response.topics[1];
        assert_eq!(quant.topic, "quant");
        assert_eq!(quant.total_cards, 1);
        assert_eq!(quant.studied_cards, 1);
        assert_eq!(quant.high_recall_cards, 0);
        assert!(quant.average_retrievability < 0.5);

        // an unstudied topic contributes no fake averages
        assert_eq!(fixed_income.retrievability_stddev, 0.0);
        Ok(())
    }

    #[test]
    fn graded_review_count_ignores_manual_entries() -> Result<()> {
        let mut col = Collection::new();
        let note = col.add_topic_note("q", &["cfa::topic::economics"]);
        assert_eq!(col.topic_mastery("", "", 0.0)?.graded_reviews, 0);

        // answering logs a graded review
        col.answer_easy();
        assert_eq!(col.topic_mastery("", "", 0.0)?.graded_reviews, 1);

        // a manual due-date change logs an ungraded (ease 0) entry
        let cids = col.storage.card_ids_of_notes(&[note.id])?;
        col.set_due_date(&cids, "5", None)?;
        assert_eq!(col.topic_mastery("", "", 0.0)?.graded_reviews, 1);
        Ok(())
    }

    #[test]
    fn scoped_search_and_custom_prefix() -> Result<()> {
        let mut col = Collection::new();
        col.add_topic_note("a", &["exam::sec::one"]);
        col.add_topic_note("b", &["exam::sec::two", "cfa::topic::quant"]);

        let response = col.topic_mastery("tag:exam::sec::one", "exam::sec::", 0.0)?;
        assert_eq!(response.total_cards, 1);
        assert_eq!(response.topics.len(), 1);
        assert_eq!(response.topics[0].topic, "one");
        assert_eq!(response.cards_without_topic, 0);
        Ok(())
    }
}
