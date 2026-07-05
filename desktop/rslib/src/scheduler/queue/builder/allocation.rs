// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Readiness-optimization allocation (Anki Speedrun, Phase 3 M2 — the
//! demoted SPOV 4).
//!
//! When `readiness_allocation` is enabled on a deck, the merged main queue
//! is stably re-ordered so cards from the topics with the largest
//! `exam-weight × (target − topic recall)` gap are studied first — the
//! direction of steepest marginal gain in the pass-band centre. Under a
//! bounded daily study budget, ordering IS allocation: the cards the
//! learner never reaches today are the cards the pass deprioritized.
//!
//! Honesty constraints:
//!
//! * **Pure permutation** — like the contrast pass, nothing is added, dropped
//!   or gated, so counts and limits stay exact.
//! * **[R8] within-topic credit only** — a card's priority comes from its own
//!   topic's recall gap; there is no cross-topic transfer credit anywhere in
//!   the score (cross-topic credit is an explicit ablation arm, never a
//!   default).
//! * **[R25] fixed priors** — exam weights are the versioned CFA blueprint
//!   midpoints (`readiness::blueprint`), never fitted; the published (min, max)
//!   range is carried as documented uncertainty, and budgeting uses the
//!   midpoint. A single weighted-overall target is used — CFA publishes only an
//!   overall MPS, so no per-topic cutoffs are invented.
//! * Topic attribution follows the dashboard exactly: canonical `cfa::topic::*`
//!   tag first, then the user's tag→topic map (`speedrun:tagTopicMap`, exact
//!   then longest-prefix match). Cards that resolve to no blueprint topic score
//!   0 and keep their relative order — attribution is never invented.
//!
//! Ordering relative to the other passes: fade gates first (pre-gather),
//! allocation orders the merged survivors, and the contrast pass runs last
//! so confusable adjacency is preserved inside the allocation's macro
//! order.

use std::collections::HashMap;
use std::collections::VecDeque;

use super::MainQueueEntry;
use super::QueueBuilder;
use crate::prelude::*;
use crate::readiness::blueprint;
use crate::readiness::fold_mastery_topics;
use crate::readiness::PERFORMANCE_TARGET;
use crate::stats::mastery::longest_prefix_match;
use crate::stats::mastery::normalized_tag_topic_map;

/// Synced collection config key holding the user's tag→topic map (written
/// by the dashboard's Map-tags editor).
const TAG_TOPIC_MAP_KEY: &str = "speedrun:tagTopicMap";
/// Same canonical prefix the mastery RPC and the contrast pass use.
const TOPIC_TAG_PREFIX: &str = "cfa::topic::";

#[derive(Debug, Default)]
pub(crate) struct AllocationContext {
    /// note id -> priority score (higher = studied earlier). Notes without
    /// a resolvable topic are absent and score 0.
    note_priority: HashMap<NoteId, f32>,
}

impl QueueBuilder {
    /// Compute per-note priorities for the gathered cards. Called between
    /// `gather_cards()` and `build()` (after the fade gate has already
    /// excluded locked rungs). One mastery SQL pass + one tag batch load;
    /// a no-op unless the deck preset enables readiness_allocation.
    pub(super) fn load_allocation_priorities(&mut self, col: &mut Collection) -> Result<()> {
        if !self.context.sort_options.readiness_allocation {
            return Ok(());
        }
        let mut note_ids: Vec<NoteId> = self
            .new
            .iter()
            .map(|c| c.note_id)
            .chain(self.review.iter().map(|c| c.note_id))
            .chain(self.day_learning.iter().map(|c| c.note_id))
            .collect();
        note_ids.sort_unstable();
        note_ids.dedup();
        if note_ids.is_empty() {
            return Ok(());
        }

        // the same attribution inputs the dashboard uses
        let user_map: HashMap<String, String> = col
            .get_config_optional(TAG_TOPIC_MAP_KEY)
            .unwrap_or_default();
        let mastery = col.topic_mastery("", "", 0.0, &user_map)?;
        let by_topic = fold_mastery_topics(&mastery);
        let normalized_map = normalized_tag_topic_map(&user_map);

        // priority per blueprint topic: weight × recall gap, both fixed/
        // measured — nothing fitted ([R25]); unstudied topics carry the
        // full gap so big unstudied topics lead
        let total_weight = blueprint::total_midpoint_weight();
        let topic_priority = |id: &str| -> Option<f32> {
            let topic = blueprint::topic(id)?;
            let mean = by_topic
                .get(topic.id)
                .filter(|evidence| evidence.studied_cards > 0)
                .map(|evidence| evidence.mean_retrievability)
                .unwrap_or(0.0);
            Some((topic.midpoint / total_weight) * (PERFORMANCE_TARGET - mean).max(0.0))
        };

        let mut note_priority: HashMap<NoteId, f32> = HashMap::new();
        for note_tags in col.storage.get_note_tags_by_id_list(&note_ids)? {
            let tags: Vec<String> = note_tags
                .tags
                .split_whitespace()
                .map(str::to_ascii_lowercase)
                .collect();
            // (1) canonical topic tag (aliases fold onto blueprint ids)
            let mut topic_id: Option<&str> = tags.iter().find_map(|tag| {
                tag.strip_prefix(TOPIC_TAG_PREFIX)
                    .filter(|rest| !rest.is_empty())
                    .and_then(blueprint::canonical_topic_id)
            });
            // (2) exact user-map match, note tag order
            if topic_id.is_none() && !normalized_map.is_empty() {
                topic_id = tags.iter().find_map(|tag| {
                    normalized_map
                        .get(tag.as_str())
                        .and_then(|mapped| blueprint::canonical_topic_id(mapped))
                });
            }
            // (3) longest-prefix user-map match
            if topic_id.is_none() && !normalized_map.is_empty() {
                let mut best: Option<(usize, &str)> = None;
                for tag in &tags {
                    if let Some((len, mapped)) = longest_prefix_match(&normalized_map, tag) {
                        if best.map_or(true, |(best_len, _)| len > best_len) {
                            best = Some((len, mapped));
                        }
                    }
                }
                topic_id = best.and_then(|(_, mapped)| blueprint::canonical_topic_id(mapped));
            }
            if let Some(priority) = topic_id.and_then(topic_priority) {
                if priority > 0.0 {
                    note_priority.insert(note_tags.id, priority);
                }
            }
        }

        if !note_priority.is_empty() {
            self.allocation = Some(AllocationContext { note_priority });
        }
        Ok(())
    }
}

/// Stable sort of the merged queue by descending priority. Equal-priority
/// cards (including every card outside a blueprint topic, at 0) keep their
/// existing relative order, so the pass composes with the vanilla sort
/// options instead of replacing them.
pub(super) fn apply_allocation(
    main: VecDeque<MainQueueEntry>,
    card_note: &HashMap<CardId, NoteId>,
    allocation: &AllocationContext,
) -> VecDeque<MainQueueEntry> {
    let mut entries: Vec<MainQueueEntry> = main.into();
    let priority_of = |entry: &MainQueueEntry| -> f32 {
        card_note
            .get(&entry.id)
            .and_then(|note_id| allocation.note_priority.get(note_id))
            .copied()
            .unwrap_or(0.0)
    };
    entries.sort_by(|a, b| priority_of(b).total_cmp(&priority_of(a)));
    entries.into()
}

#[cfg(test)]
mod test {
    use crate::card::FsrsMemoryState;
    use crate::prelude::*;
    use crate::tests::NoteAdder;

    impl Collection {
        fn set_allocation(&mut self, enabled: bool) {
            self.update_default_deck_config(|config| {
                config.readiness_allocation = enabled;
            });
        }

        fn add_allocation_note(&mut self, front: &str, tags: &[&str], due_today: bool) -> Note {
            let note = NoteAdder::basic(self)
                .fields(&[front, "back"])
                .tags(tags)
                .add(self);
            if due_today {
                let cids = self.storage.card_ids_of_notes(&[note.id]).unwrap();
                self.set_due_date(&cids, "0", None).unwrap();
            }
            note
        }

        /// Give the note's first card a memory state so its topic has a
        /// mean recall (reviewed just now → retrievability ≈ stability
        /// driven; large stability ≈ 1.0, tiny ≈ low after elapsed time).
        fn set_recall(&mut self, note: &Note, stability: f32, days_ago: i64) {
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

        fn note_order(&mut self) -> Vec<NoteId> {
            self.build_queues(DeckId(1))
                .unwrap()
                .iter()
                .map(|entry| {
                    self.storage
                        .get_card(entry.card_id())
                        .unwrap()
                        .unwrap()
                        .note_id
                })
                .collect()
        }
    }

    /// Weighted-gap ordering: a weak, heavily-weighted topic's cards come
    /// first; untopiced cards keep their relative order after them; the
    /// pass is a pure permutation and default-off.
    #[test]
    fn weak_heavy_topics_lead_and_off_is_vanilla() {
        let mut col = Collection::new();
        // insertion order: ethics (weak) after quant (strong) so the pass
        // has to move it forward
        let quant_a = col.add_allocation_note("q a", &["cfa::topic::quant"], false);
        let plain = col.add_allocation_note("plain", &["random::tag"], false);
        let ethics_a = col.add_allocation_note("e a", &["cfa::topic::ethics"], false);
        let ethics_b = col.add_allocation_note("e b", &["cfa::topic::ethics"], true);

        // quant recalled ~perfectly, ethics recalled poorly
        col.set_recall(&quant_a, 10_000.0, 0);
        col.set_recall(&ethics_a, 1.0, 60);

        col.set_allocation(false);
        let vanilla = col.note_order();

        col.set_allocation(true);
        let allocated = col.note_order();

        // same cards either way
        let mut sorted_vanilla = vanilla.clone();
        sorted_vanilla.sort_unstable();
        let mut sorted_allocated = allocated.clone();
        sorted_allocated.sort_unstable();
        assert_eq!(sorted_vanilla, sorted_allocated);

        // ethics (weight 17.5, huge gap) leads; both ethics cards precede
        // every other card
        let position = |queue: &[NoteId], id: NoteId| queue.iter().position(|n| *n == id).unwrap();
        assert!(position(&allocated, ethics_a.id) < position(&allocated, quant_a.id));
        assert!(position(&allocated, ethics_b.id) < position(&allocated, quant_a.id));
        assert!(position(&allocated, ethics_a.id) < position(&allocated, plain.id));

        // untopiced and at-target cards keep their vanilla relative order
        let background_vanilla: Vec<NoteId> = vanilla
            .iter()
            .copied()
            .filter(|id| *id == quant_a.id || *id == plain.id)
            .collect();
        let background_allocated: Vec<NoteId> = allocated
            .iter()
            .copied()
            .filter(|id| *id == quant_a.id || *id == plain.id)
            .collect();
        assert_eq!(background_vanilla, background_allocated);
    }

    /// [R8]: the score uses within-topic recall only — an unstudied heavy
    /// topic outranks a weak light topic, and strong recall in one topic
    /// never boosts or drags another.
    #[test]
    fn priorities_are_within_topic_only() {
        let mut col = Collection::new();
        let deriv = col.add_allocation_note("d", &["cfa::topic::derivatives"], false);
        let fi = col.add_allocation_note("f", &["cfa::topic::fixed_income"], false);
        // derivatives studied and weak; fixed income never studied.
        // weights: fi 12.5 > deriv 6.5, so with within-topic-only credit the
        // unstudied fi (full gap × larger weight) must lead even though
        // deriv has "worse" measured recall than fi's (absent) measurement.
        col.set_recall(&deriv, 1.0, 60);

        col.set_allocation(true);
        let order = col.note_order();
        let position = |id: NoteId| order.iter().position(|n| *n == id).unwrap();
        assert!(position(fi.id) < position(deriv.id));
    }

    /// The user's tag→topic map attributes untagged decks the same way the
    /// dashboard does (exact then longest-prefix), feeding the same
    /// priorities.
    #[test]
    fn user_map_attribution_matches_dashboard_rules() {
        let mut col = Collection::new();
        let mapped = col.add_allocation_note("m", &["myreading::bonds::duration"], false);
        let strong = col.add_allocation_note("s", &["cfa::topic::quant"], false);
        col.set_recall(&strong, 10_000.0, 0);
        col.set_config_json(
            "speedrun:tagTopicMap",
            &std::collections::HashMap::from([(
                "myreading::bonds".to_string(),
                "fixed_income".to_string(),
            )]),
            false,
        )
        .unwrap();

        col.set_allocation(true);
        let order = col.note_order();
        let position = |id: NoteId| order.iter().position(|n| *n == id).unwrap();
        // the mapped note resolves to fixed_income (unstudied, weight 12.5)
        // and must precede the strong quant note
        assert!(position(mapped.id) < position(strong.id));
    }

    /// Allocation + contrast compose: macro order by priority, confusable
    /// adjacency preserved inside it.
    #[test]
    fn contrast_adjacency_survives_allocation() {
        let mut col = Collection::new();
        col.update_default_deck_config(|config| {
            config.readiness_allocation = true;
            config.contrast_scheduling = true;
            config.contrast_tag_prefix = String::new();
            config.contrast_confusable_tag = String::new();
        });
        let fi_tags = &["cluster::fi::duration", "cfa::topic::fixed_income"];
        let a = col.add_allocation_note("dur a", fi_tags, false);
        col.add_allocation_note("noise", &["cfa::topic::quant"], false);
        let b = col.add_allocation_note("dur b", fi_tags, true);

        let order = col.note_order();
        let position = |id: NoteId| order.iter().position(|n| *n == id).unwrap();
        let (first, second) = (position(a.id), position(b.id));
        assert_eq!(
            first.abs_diff(second),
            1,
            "cluster members must stay adjacent: {order:?}"
        );
    }
}
