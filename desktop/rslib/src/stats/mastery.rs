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
//! Cards without a topic-prefix tag can be attributed through a
//! user-editable tag->topic map (see [TopicMasteryRequest] in `stats.proto`).
//! Resolution order per card: canonical prefix tag, then exact map match,
//! then longest map-prefix match at a `::` boundary, else unmapped. The map
//! is applied here at read time (note tags are never rewritten) rather than
//! returning per-raw-tag buckets, because per-raw-tag buckets double-count
//! multi-tagged cards; DASHBOARD.md sanctions this pass-the-map-in variant.
//!
//! Honesty notes:
//! * "high recall probability" is deliberately not called "mastered": FSRS
//!   retrievability is a scheduling target, not a competence threshold.
//! * The response carries `fsrs_enabled` and per-topic studied counts so the UI
//!   can abstain instead of showing proxy numbers when data is missing.
//! * The engine only aggregates by an opaque tag prefix; the CFA topic list,
//!   exam weights and any score mapping live in the frontend. User-map values
//!   are opaque topic ids passed straight through.
//! * Unmapped cards stay counted in `cards_without_topic` and their raw tags
//!   are surfaced in `unmapped_tags` - attribution is never invented.
//! * [R24] notes tagged `aig::ungraded` (AI-generated, never graded) are
//!   excluded from every topic bucket and reported in `ungraded_aig_cards`, so
//!   they can never feed readiness while remaining visibly disclosed.
//! * Notes tagged `probe::held_out` (the Phase 3 delayed-probe bank) are
//!   likewise excluded and reported in `held_out_probe_cards`: the measurement
//!   instrument must never feed the Memory gauge or the coverage it is supposed
//!   to test (held-out hygiene).

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
/// Notes carrying this tag are ungraded AI-generated items; per R24 they may
/// be studied but must never feed readiness.
pub const AIG_UNGRADED_TAG: &str = "aig::ungraded";
/// Special tag_topic_map value: drop the tag's cards from every topic and
/// from the unmapped count (noise tags). Matched case-insensitively.
pub const IGNORE_TOPIC_VALUE: &str = "ignore";
/// Cap on the per-raw-tag buckets returned for unmapped cards.
pub const MAX_UNMAPPED_TAGS: usize = 200;

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

/// Normalize user map keys for matching: trimmed, lowercased, and with a
/// trailing "::" stripped so "finance" and "finance::" address the same
/// subtree. Entries with an empty key or value are dropped.
pub(crate) fn normalized_tag_topic_map(map: &HashMap<String, String>) -> HashMap<String, String> {
    map.iter()
        .filter_map(|(key, value)| {
            let key = key.trim().to_ascii_lowercase();
            let key = key.trim_end_matches("::");
            let value = value.trim();
            if key.is_empty() || value.is_empty() {
                None
            } else {
                Some((key.to_string(), value.to_string()))
            }
        })
        .collect()
}

/// The longest map key that is a strict prefix of `tag` at a `::` boundary
/// (a key K matches tag T when T starts with K + "::"; T == K is the exact
/// tier, handled by the caller first). Returns (key length, topic id).
/// Walks the tag's own `::` boundaries from the right, so cost is bounded
/// by the tag's segment count - no per-card queries.
pub(crate) fn longest_prefix_match<'m>(
    map: &'m HashMap<String, String>,
    tag: &str,
) -> Option<(usize, &'m str)> {
    let mut end = tag.len();
    while let Some(pos) = tag[..end].rfind("::") {
        if pos == 0 {
            return None;
        }
        let candidate = &tag[..pos];
        if let Some(topic) = map.get(candidate) {
            return Some((candidate.len(), topic.as_str()));
        }
        end = pos;
    }
    None
}

impl Collection {
    /// Per-topic mastery over the cards matched by `search` (empty = whole
    /// collection). See [TopicMasteryResponse] in `stats.proto`.
    ///
    /// Per-card topic resolution order:
    /// 1. first tag under the canonical topic prefix (unchanged behaviour);
    /// 2. exact tag match in `tag_topic_map`;
    /// 3. longest-prefix map match at a `::` boundary;
    /// 4. unmapped: counted in `cards_without_topic` and bucketed per raw tag
    ///    into `unmapped_tags` (tag-frequency buckets, not disjoint card
    ///    counts).
    ///
    /// A card resolving to the special value "ignore" stays in
    /// `total_cards` but feeds neither a topic nor `cards_without_topic`.
    /// Cards tagged `aig::ungraded` are excluded before any resolution and
    /// reported in `ungraded_aig_cards` ([R24]).
    pub fn topic_mastery(
        &mut self,
        search: &str,
        topic_prefix: &str,
        high_recall_threshold: f32,
        tag_topic_map: &HashMap<String, String>,
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
        let user_map = normalized_tag_topic_map(tag_topic_map);

        let timing = self.timing_today()?;
        let mut accumulators: HashMap<String, TopicAccumulator> = HashMap::new();
        let mut unmapped: HashMap<String, TopicAccumulator> = HashMap::new();
        let mut total_cards = 0u32;
        let mut cards_without_topic = 0u32;
        let mut ungraded_aig_cards = 0u32;
        let mut held_out_probe_cards = 0u32;
        {
            let guard = self.search_cards_into_table(search, SortMode::NoOrder)?;
            guard
                .col
                .storage
                .for_each_searched_card_tags_and_retrievability(
                    timing,
                    |tags, retrievability| {
                        total_cards += 1;
                        let tag_list: Vec<String> = tags
                            .split_whitespace()
                            .map(str::to_ascii_lowercase)
                            .collect();
                        // [R24] guard first: ungraded AI-generated items may
                        // be studied but never feed readiness. They are
                        // disclosed via the count instead of being silently
                        // dropped, and skip unmapped bucketing too - they
                        // are excluded, not unattributed.
                        if tag_list.iter().any(|tag| tag == AIG_UNGRADED_TAG) {
                            ungraded_aig_cards += 1;
                            return;
                        }
                        // held-out hygiene: probe-bank cards are the
                        // measurement instrument, so they never feed the
                        // Memory gauge or coverage. Disclosed via the count.
                        if tag_list
                            .iter()
                            .any(|tag| tag == crate::readiness::PROBE_HELD_OUT_TAG)
                        {
                            held_out_probe_cards += 1;
                            return;
                        }
                        // (1) canonical prefix tag
                        let mut topic = tag_list.iter().find_map(|tag| {
                            tag.strip_prefix(prefix)
                                .filter(|rest| !rest.is_empty())
                                .map(ToString::to_string)
                        });
                        // (2) exact user-map match, in note tag order
                        if topic.is_none() && !user_map.is_empty() {
                            topic = tag_list
                                .iter()
                                .find_map(|tag| user_map.get(tag.as_str()).cloned());
                        }
                        // (3) longest-prefix user-map match; ties across
                        // tags keep the first tag in note order
                        if topic.is_none() && !user_map.is_empty() {
                            let mut best: Option<(usize, &str)> = None;
                            for tag in &tag_list {
                                if let Some((len, mapped)) = longest_prefix_match(&user_map, tag) {
                                    if best.map_or(true, |(best_len, _)| len > best_len) {
                                        best = Some((len, mapped));
                                    }
                                }
                            }
                            topic = best.map(|(_, mapped)| mapped.to_string());
                        }
                        match topic {
                            // mapped to "ignore": deliberately dropped from
                            // every topic and from the unmapped count, but
                            // still part of total_cards
                            Some(topic) if topic.eq_ignore_ascii_case(IGNORE_TOPIC_VALUE) => {}
                            Some(topic) => accumulators
                                .entry(topic)
                                .or_default()
                                .add_card(retrievability, threshold),
                            None => {
                                // (4) unmapped: keep the abstention visible,
                                // and bucket every raw tag for the mapping
                                // editor (a multi-tag card appears under
                                // each of its tags: tag-frequency buckets,
                                // not disjoint card counts)
                                cards_without_topic += 1;
                                for tag in tag_list {
                                    unmapped
                                        .entry(tag)
                                        .or_default()
                                        .add_card(retrievability, threshold);
                                }
                            }
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

        let mut unmapped_tags: Vec<Topic> = unmapped
            .into_iter()
            .map(|(name, acc)| acc.into_topic(name))
            .collect();
        // most frequent first so the mapping editor leads with the biggest
        // wins; alphabetical tie-break keeps the order deterministic
        unmapped_tags.sort_by(|a, b| {
            b.total_cards
                .cmp(&a.total_cards)
                .then_with(|| a.topic.cmp(&b.topic))
        });
        unmapped_tags.truncate(MAX_UNMAPPED_TAGS);

        Ok(TopicMasteryResponse {
            topics,
            cards_without_topic,
            total_cards,
            graded_reviews: self.storage.graded_review_count()?,
            fsrs_enabled: self.get_config_bool(BoolKey::Fsrs),
            high_recall_threshold: threshold,
            unmapped_tags,
            ungraded_aig_cards,
            held_out_probe_cards,
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

        let response = col.topic_mastery("", "", 0.0, &HashMap::new())?;
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
        let no_map = HashMap::new();
        assert_eq!(col.topic_mastery("", "", 0.0, &no_map)?.graded_reviews, 0);

        // answering logs a graded review
        col.answer_easy();
        assert_eq!(col.topic_mastery("", "", 0.0, &no_map)?.graded_reviews, 1);

        // a manual due-date change logs an ungraded (ease 0) entry
        let cids = col.storage.card_ids_of_notes(&[note.id])?;
        col.set_due_date(&cids, "5", None)?;
        assert_eq!(col.topic_mastery("", "", 0.0, &no_map)?.graded_reviews, 1);
        Ok(())
    }

    #[test]
    fn scoped_search_and_custom_prefix() -> Result<()> {
        let mut col = Collection::new();
        col.add_topic_note("a", &["exam::sec::one"]);
        col.add_topic_note("b", &["exam::sec::two", "cfa::topic::quant"]);

        let response =
            col.topic_mastery("tag:exam::sec::one", "exam::sec::", 0.0, &HashMap::new())?;
        assert_eq!(response.total_cards, 1);
        assert_eq!(response.topics.len(), 1);
        assert_eq!(response.topics[0].topic, "one");
        assert_eq!(response.cards_without_topic, 0);
        Ok(())
    }

    fn map_of(entries: &[(&str, &str)]) -> HashMap<String, String> {
        entries
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// Resolution precedence: a canonical `cfa::topic::` tag always beats
    /// the user map, and an exact map match beats a prefix match.
    #[test]
    fn user_map_precedence() -> Result<()> {
        let mut col = Collection::new();
        // canonical tag wins even though the other tag has an exact mapping
        col.add_topic_note("a", &["cfa::topic::quant", "reading42"]);
        // exact match beats a shorter prefix match on the same tag
        col.add_topic_note("b", &["finance::bonds"]);
        // and beats a prefix match on another tag of the same note
        col.add_topic_note("c", &["portfolio::intro", "reading42"]);
        let map = map_of(&[
            ("reading42", "economics"),
            ("finance", "derivatives"),
            ("finance::bonds", "fixed_income"),
            ("portfolio", "portfolio_management"),
        ]);

        let response = col.topic_mastery("", "", 0.0, &map)?;
        assert_eq!(response.cards_without_topic, 0);
        let totals: Vec<(&str, u32)> = response
            .topics
            .iter()
            .map(|t| (t.topic.as_str(), t.total_cards))
            .collect();
        assert_eq!(
            totals,
            [("economics", 1), ("fixed_income", 1), ("quant", 1)]
        );
        Ok(())
    }

    /// Prefix keys match whole `::` segments only, with or without a
    /// trailing "::" on the key, case-insensitively.
    #[test]
    fn user_map_prefix_matching() -> Result<()> {
        let mut col = Collection::new();
        col.add_topic_note("a", &["Finance::Bonds::Duration"]);
        col.add_topic_note("b", &["financeplus::bonds"]);

        // "finance::" (trailing separator) matches at the :: boundary...
        let response = col.topic_mastery("", "", 0.0, &map_of(&[("Finance::", "fixed_income")]))?;
        assert_eq!(response.topics.len(), 1);
        assert_eq!(response.topics[0].topic, "fixed_income");
        // ...but must not match the unrelated "financeplus::bonds"
        assert_eq!(response.topics[0].total_cards, 1);
        assert_eq!(response.cards_without_topic, 1);

        // the longest mapped ancestor wins
        let response = col.topic_mastery(
            "",
            "",
            0.0,
            &map_of(&[("finance", "economics"), ("finance::bonds", "fixed_income")]),
        )?;
        assert_eq!(response.topics[0].topic, "fixed_income");
        Ok(())
    }

    /// "ignore" drops a card from every bucket while keeping it visible in
    /// total_cards; unmapped cards stay counted and surfaced per raw tag.
    #[test]
    fn user_map_ignore_and_unmapped_buckets() -> Result<()> {
        let mut col = Collection::new();
        let studied = col.add_topic_note("a", &["mystery::x", "shared"]);
        col.add_topic_note("b", &["mystery::x"]);
        col.add_topic_note("c", &["noise::layout"]);
        col.set_memory_state(&studied, 100.0, 0);

        let response = col.topic_mastery("", "", 0.0, &map_of(&[("noise", "IGNORE")]))?;
        assert_eq!(response.total_cards, 3);
        // the ignored card feeds neither a topic nor the unmapped count
        assert!(response.topics.is_empty());
        assert_eq!(response.cards_without_topic, 2);
        // per-raw-tag buckets for the mapping editor: the multi-tag card
        // appears under each of its tags (tag frequency, not card count)
        let buckets: Vec<(&str, u32, u32)> = response
            .unmapped_tags
            .iter()
            .map(|t| (t.topic.as_str(), t.total_cards, t.studied_cards))
            .collect();
        assert_eq!(buckets, [("mystery::x", 2, 1), ("shared", 1, 1)]);
        Ok(())
    }

    /// The per-tag buckets are capped at the most frequent 200 so a messy
    /// deck cannot blow up the payload.
    #[test]
    fn unmapped_tags_are_capped() -> Result<()> {
        let mut col = Collection::new();
        let many: Vec<String> = (0..205).map(|i| format!("tag{i:03}")).collect();
        let refs: Vec<&str> = many.iter().map(String::as_str).collect();
        col.add_topic_note("a", &refs);
        col.add_topic_note("b", &["tag204"]);

        let response = col.topic_mastery("", "", 0.0, &HashMap::new())?;
        assert_eq!(response.cards_without_topic, 2);
        assert_eq!(response.unmapped_tags.len(), MAX_UNMAPPED_TAGS);
        // most frequent first
        assert_eq!(response.unmapped_tags[0].topic, "tag204");
        assert_eq!(response.unmapped_tags[0].total_cards, 2);
        Ok(())
    }

    /// Held-out hygiene: probe-bank cards never feed the Memory gauge or
    /// coverage - excluded from every bucket, disclosed via the count.
    #[test]
    fn held_out_probe_cards_are_excluded_and_counted() -> Result<()> {
        let mut col = Collection::new();
        let probe = col.add_topic_note(
            "p",
            &[
                "cfa::topic::ethics",
                "probe::held_out",
                "probe::pool::performance",
            ],
        );
        col.add_topic_note("study", &["cfa::topic::ethics"]);
        col.set_memory_state(&probe, 100.0, 0);

        let response = col.topic_mastery("", "", 0.0, &HashMap::new())?;
        assert_eq!(response.total_cards, 2);
        assert_eq!(response.held_out_probe_cards, 1);
        // the studied probe must not create a studied ethics bucket
        assert_eq!(response.topics.len(), 1);
        assert_eq!(response.topics[0].total_cards, 1);
        assert_eq!(response.topics[0].studied_cards, 0);
        assert_eq!(response.cards_without_topic, 0);
        Ok(())
    }

    /// [R24]: `aig::ungraded` cards may be studied but never feed
    /// readiness - excluded from every bucket, disclosed via the count.
    #[test]
    fn ungraded_aig_cards_are_excluded_and_counted() -> Result<()> {
        let mut col = Collection::new();
        let machine = col.add_topic_note("m", &["cfa::topic::quant", "aig::ungraded"]);
        col.add_topic_note("h", &["cfa::topic::quant", "aig::graded"]);
        col.set_memory_state(&machine, 100.0, 0);

        let response = col.topic_mastery("", "", 0.0, &HashMap::new())?;
        assert_eq!(response.total_cards, 2);
        assert_eq!(response.ungraded_aig_cards, 1);
        // only the graded card reaches the topic bucket, despite the
        // ungraded one having been studied
        assert_eq!(response.topics.len(), 1);
        assert_eq!(response.topics[0].total_cards, 1);
        assert_eq!(response.topics[0].studied_cards, 0);
        // excluded, not unattributed: no unmapped bucket for its tags
        assert_eq!(response.cards_without_topic, 0);
        assert!(response.unmapped_tags.is_empty());
        Ok(())
    }
}
