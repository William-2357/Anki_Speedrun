// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Contrast scheduling (Anki Speedrun, SPOV 1 + SPOV 3).
//!
//! Conventional SRS schedules every card independently and *spaces* related
//! cards apart; for an exam full of confusable categories (FIFO/LIFO, the
//! duration trio, neighbouring Ethics Standards) that interference *is* the
//! lesson. When `contrast_scheduling` is enabled on a deck, this pass reorders
//! the merged study queue so cards whose notes share a confusable-cluster tag
//! (`cluster::*` by default) are shown back-to-back, forcing the learner to
//! discriminate between them.
//!
//! Design constraints (see PHASE1_PLAN_V2.md + GRILLING_NOTES.md):
//!
//! * **Pure reordering** — no card is added, dropped or gated, so counts stay
//!   valid, undo is unaffected, and the collection cannot be corrupted.
//! * **C3** — the pass runs on the *merged* main queue (after new/review
//!   interspersing), so the interspersers cannot splice unrelated cards between
//!   two confusable cards. Cluster members sit at lag 0 within a run.
//! * **C10** — a sibling-adjacency guard avoids placing two templates of the
//!   *same note* next to each other; that adjacency would be trivial
//!   repetition, not discrimination.
//! * **R28** — clusters never span two topics: the effective cluster key is
//!   (topic tag, cluster tag), so cross-topic cards sharing a cluster tag get
//!   no adjacency credit.
//! * **C13** — when no usable cluster tags exist the pass is a no-op. There is
//!   deliberately *no* fallback to grouping by an arbitrary first tag: that
//!   would block whole readings together, which is measurably worse than doing
//!   nothing (Carvalho & Goldstone 2014).
//! * **R18 (Phase 2)** — the signed confusability gate: when the deck config
//!   names a `contrast_confusable_tag` marker (default `confusable::high`,
//!   written by the offline behavioural confusion-mining pass), only clusters
//!   carrying it are forced adjacent; merely-similar clusters keep default SRS
//!   spacing (wrong-side adjacency is a measured loss, d=0.76). An empty marker
//!   string preserves the legacy ungated Phase 1 behaviour (the ablation OFF
//!   arm).
//! * **R13 (Phase 2)** — clusters that failed the fade ladder's
//!   comprehension/fluency preconditions are not forced adjacent either;
//!   discrimination practice waits until both members clear the fluency floor.

use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;

use super::MainQueueEntry;
use super::QueueBuilder;
use crate::prelude::*;

/// Maximum number of same-cluster cards placed in one adjacent run. Small
/// runs keep the queue interleaved *between* clusters while cards *within*
/// a run stay at lag 0.
pub(crate) const CONTRAST_CHUNK: usize = 4;

/// Used when the deck config's `contrast_tag_prefix` is empty.
pub(crate) const DEFAULT_CONTRAST_TAG_PREFIX: &str = "cluster::";

/// Tag prefix that scopes clusters to a single exam topic (R28: cross-topic
/// "confusables" get no adjacency credit). The engine only treats this as an
/// opaque grouping prefix; the CFA topic list itself lives in the frontend.
pub(crate) const TOPIC_TAG_PREFIX: &str = "cfa::topic::";

/// Cluster assignments for the notes gathered into the current queue build.
#[derive(Debug, Default)]
pub(crate) struct ContrastContext {
    /// note id -> interned (topic, cluster) index
    note_cluster: HashMap<NoteId, usize>,
    /// interned indices allowed to force adjacency: confusability-gated-on
    /// (R18) and not fluency-blocked (R13)
    gated_on: HashSet<usize>,
}

impl QueueBuilder {
    /// Batch-load the gathered notes' tags and derive cluster membership.
    /// Called between `gather_cards()` and `build()`, while the collection
    /// is still available. One query; no per-card work.
    pub(super) fn load_contrast_clusters(&mut self, col: &mut Collection) -> Result<()> {
        if !self.context.sort_options.contrast_scheduling {
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

        let configured_prefix = self
            .context
            .sort_options
            .contrast_tag_prefix
            .trim()
            .to_ascii_lowercase();
        let cluster_prefix = if configured_prefix.is_empty() {
            DEFAULT_CONTRAST_TAG_PREFIX.to_string()
        } else {
            configured_prefix
        };
        // R18: the confusability marker; empty = legacy ungated behaviour
        let confusable_marker = self
            .context
            .sort_options
            .contrast_confusable_tag
            .trim()
            .to_ascii_lowercase();

        let mut interned: HashMap<(Option<String>, String), usize> = HashMap::new();
        let mut note_cluster: HashMap<NoteId, usize> = HashMap::new();
        let mut confusable: HashSet<usize> = HashSet::new();
        for note_tags in col.storage.get_note_tags_by_id_list(&note_ids)? {
            let tags: Vec<String> = note_tags
                .tags
                .split_whitespace()
                .map(str::to_ascii_lowercase)
                .collect();
            let cluster = tags.iter().find_map(|tag| {
                tag.strip_prefix(cluster_prefix.as_str())
                    .filter(|rest| !rest.is_empty())
                    .map(ToString::to_string)
            });
            let Some(cluster) = cluster else {
                // C13: notes without a cluster tag stay in their vanilla
                // position; never group by an unrelated tag.
                continue;
            };
            let topic = tags
                .iter()
                .find(|tag| tag.starts_with(TOPIC_TAG_PREFIX))
                .cloned();
            let next_index = interned.len();
            let index = *interned.entry((topic, cluster)).or_insert(next_index);
            note_cluster.insert(note_tags.id, index);
            // R18: a cluster is confusability-gated-on when any member note
            // carries the marker tag (or a child tag under it)
            if !confusable_marker.is_empty()
                && tags.iter().any(|tag| {
                    tag == &confusable_marker
                        || tag
                            .strip_prefix(confusable_marker.as_str())
                            .is_some_and(|rest| rest.starts_with("::"))
                })
            {
                confusable.insert(index);
            }
        }

        // R18 + R13: adjacency is forced only for clusters that are
        // confusability-gated-on AND clear the fade ladder's fluency
        // preconditions; everything else keeps default SRS spacing.
        let fluency_blocked = self.fade.fluency_blocked_clusters();
        let gated_on: HashSet<usize> = interned
            .iter()
            .filter(|(key, index)| {
                (confusable_marker.is_empty() || confusable.contains(index))
                    && !fluency_blocked.contains(*key)
            })
            .map(|(_, index)| *index)
            .collect();

        if !note_cluster.is_empty() {
            self.contrast = Some(ContrastContext {
                note_cluster,
                gated_on,
            });
        }
        Ok(())
    }
}

/// Reorder the merged main queue so same-cluster cards form adjacent runs of
/// up to [CONTRAST_CHUNK] cards, with runs from different clusters
/// round-robined so the learner keeps switching between confusable families.
/// Cards outside any cluster keep their relative order. The result is a
/// permutation of the input: same entries, same counts.
pub(super) fn apply_contrast(
    main: VecDeque<MainQueueEntry>,
    card_note: &HashMap<CardId, NoteId>,
    contrast: &ContrastContext,
) -> VecDeque<MainQueueEntry> {
    let entries: Vec<MainQueueEntry> = main.into();
    let cluster_of = |entry: &MainQueueEntry| -> Option<usize> {
        card_note
            .get(&entry.id)
            .and_then(|note_id| contrast.note_cluster.get(note_id))
            .copied()
    };

    // Count members present in this queue; only clusters with >= 2 members
    // can produce contrast, others are background (C13 no-op guard). R18/R13:
    // clusters outside the gated-on set never force adjacency.
    let mut member_counts: HashMap<usize, usize> = HashMap::new();
    for entry in &entries {
        if let Some(cluster) = cluster_of(entry) {
            *member_counts.entry(cluster).or_default() += 1;
        }
    }
    member_counts.retain(|cluster, count| *count >= 2 && contrast.gated_on.contains(cluster));
    if member_counts.is_empty() {
        return entries.into();
    }

    // Gather each active cluster's entries in queue order.
    let mut cluster_order: Vec<usize> = Vec::new();
    let mut cluster_entries: HashMap<usize, Vec<MainQueueEntry>> = HashMap::new();
    let mut is_contrast_slot: Vec<bool> = Vec::with_capacity(entries.len());
    for entry in &entries {
        match cluster_of(entry).filter(|c| member_counts.contains_key(c)) {
            Some(cluster) => {
                if !cluster_entries.contains_key(&cluster) {
                    cluster_order.push(cluster);
                }
                cluster_entries.entry(cluster).or_default().push(*entry);
                is_contrast_slot.push(true);
            }
            None => is_contrast_slot.push(false),
        }
    }

    // Chunk each cluster into sibling-safe runs, then round-robin the runs
    // across clusters.
    let mut chunks_per_cluster: Vec<VecDeque<Vec<MainQueueEntry>>> = cluster_order
        .iter()
        .map(|cluster| chunk_cluster(cluster_entries.remove(cluster).unwrap(), card_note))
        .collect();
    let mut stream: VecDeque<Vec<MainQueueEntry>> = VecDeque::new();
    loop {
        let mut exhausted = true;
        for chunks in &mut chunks_per_cluster {
            if let Some(chunk) = chunks.pop_front() {
                stream.push_back(chunk);
                exhausted = false;
            }
        }
        if exhausted {
            break;
        }
    }

    // Rebuild: background cards keep their relative order; each contrast run
    // is emitted, whole, at the position of the earliest unconsumed contrast
    // slot, and consumes as many later slots as it has cards.
    let mut rebuilt: Vec<MainQueueEntry> = Vec::with_capacity(entries.len());
    let mut slots_to_skip = 0usize;
    for (entry, is_slot) in entries.iter().zip(is_contrast_slot) {
        if !is_slot {
            rebuilt.push(*entry);
            continue;
        }
        if slots_to_skip > 0 {
            slots_to_skip -= 1;
            continue;
        }
        if let Some(mut chunk) = stream.pop_front() {
            avoid_boundary_sibling(&mut chunk, rebuilt.last(), card_note);
            slots_to_skip = chunk.len() - 1;
            rebuilt.extend(chunk);
        }
    }
    debug_assert_eq!(rebuilt.len(), entries.len());
    rebuilt.into()
}

fn note_of(entry: &MainQueueEntry, card_note: &HashMap<CardId, NoteId>) -> Option<NoteId> {
    card_note.get(&entry.id).copied()
}

/// Split one cluster's cards into runs of <= CONTRAST_CHUNK, greedily
/// avoiding two templates of the same note sitting adjacently inside a run
/// (C10). If only same-note cards remain, the run is cut short so another
/// cluster's run lands between them.
fn chunk_cluster(
    entries: Vec<MainQueueEntry>,
    card_note: &HashMap<CardId, NoteId>,
) -> VecDeque<Vec<MainQueueEntry>> {
    let mut remaining: VecDeque<MainQueueEntry> = entries.into();
    let mut chunks: VecDeque<Vec<MainQueueEntry>> = VecDeque::new();
    while !remaining.is_empty() {
        let mut chunk: Vec<MainQueueEntry> = Vec::with_capacity(CONTRAST_CHUNK);
        while chunk.len() < CONTRAST_CHUNK && !remaining.is_empty() {
            let last_note = chunk.last().and_then(|entry| note_of(entry, card_note));
            let pick = remaining
                .iter()
                .position(|candidate| {
                    last_note.is_none() || note_of(candidate, card_note) != last_note
                })
                .unwrap_or({
                    // only same-note siblings left; end the run early
                    usize::MAX
                });
            if pick == usize::MAX {
                break;
            }
            chunk.push(remaining.remove(pick).unwrap());
        }
        chunks.push_back(chunk);
    }
    chunks
}

/// If the first card of `chunk` would sit next to a same-note card already
/// emitted, promote the first different-note card to the front, keeping the
/// rest in order. Reverts if that would create a same-note adjacency inside
/// the run.
fn avoid_boundary_sibling(
    chunk: &mut [MainQueueEntry],
    previous: Option<&MainQueueEntry>,
    card_note: &HashMap<CardId, NoteId>,
) {
    let Some(previous_note) = previous.and_then(|entry| note_of(entry, card_note)) else {
        return;
    };
    if note_of(&chunk[0], card_note) != Some(previous_note) {
        return;
    }
    let Some(swap_with) = chunk
        .iter()
        .position(|entry| note_of(entry, card_note) != Some(previous_note))
    else {
        return;
    };
    chunk[..=swap_with].rotate_right(1);
    if has_internal_sibling_adjacency(chunk, card_note) {
        // revert; internal correctness wins over the boundary case
        chunk[..=swap_with].rotate_left(1);
    }
}

fn has_internal_sibling_adjacency(
    chunk: &[MainQueueEntry],
    card_note: &HashMap<CardId, NoteId>,
) -> bool {
    chunk.windows(2).any(|pair| {
        let first = note_of(&pair[0], card_note);
        first.is_some() && first == note_of(&pair[1], card_note)
    })
}

#[cfg(test)]
mod test {
    use anki_proto::deck_config::deck_config::config::NewCardSortOrder;

    use crate::card::Card;
    use crate::prelude::*;
    use crate::tests::NoteAdder;

    const FI_TAGS: &[&str] = &["cluster::fi::duration", "cfa::topic::fixed_income"];
    const QUANT_TAGS: &[&str] = &["cluster::quant::inventory", "cfa::topic::quant"];

    impl Collection {
        fn enable_contrast(&mut self) {
            self.update_default_deck_config(|config| {
                config.contrast_scheduling = true;
                // exercise the default-prefix fallback
                config.contrast_tag_prefix = String::new();
                // legacy ungated arm: these tests exercise the reorder
                // machinery itself; the R18 gate has its own tests below
                config.contrast_confusable_tag = String::new();
            });
        }

        fn set_confusable_marker(&mut self, marker: &str) {
            self.update_default_deck_config(|config| {
                config.contrast_confusable_tag = marker.to_string();
            });
        }

        fn disable_contrast(&mut self) {
            self.update_default_deck_config(|config| config.contrast_scheduling = false);
        }

        /// Add a note; if `due_today`, its cards become review cards due now.
        fn add_tagged_note(&mut self, front: &str, tags: &[&str], due_today: bool) -> Note {
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

        /// The main queue as (card id, note id), in study order.
        fn queue_notes(&mut self) -> Vec<(CardId, NoteId)> {
            self.build_queues(DeckId(1))
                .unwrap()
                .iter()
                .map(|entry| {
                    let card: Card = self.storage.get_card(entry.card_id()).unwrap().unwrap();
                    (card.id, card.note_id)
                })
                .collect()
        }
    }

    fn positions_of(queue: &[(CardId, NoteId)], notes: &[NoteId]) -> Vec<usize> {
        queue
            .iter()
            .enumerate()
            .filter_map(|(idx, (_, note_id))| notes.contains(note_id).then_some(idx))
            .collect()
    }

    fn assert_adjacent_run(queue: &[(CardId, NoteId)], notes: &[NoteId]) {
        let positions = positions_of(queue, notes);
        assert!(positions.len() >= 2, "cluster missing from queue");
        let width = positions.last().unwrap() - positions.first().unwrap();
        assert_eq!(
            width,
            positions.len() - 1,
            "cluster {notes:?} not adjacent; positions {positions:?} in {queue:?}"
        );
    }

    fn sorted_cards(queue: &[(CardId, NoteId)]) -> Vec<CardId> {
        let mut ids: Vec<CardId> = queue.iter().map(|(card_id, _)| *card_id).collect();
        ids.sort_unstable();
        ids
    }

    /// C3: cluster members must adjoin on the merged queue, even when one
    /// member is a new card and the other a due review card that the
    /// intersperser would otherwise separate.
    #[test]
    fn clusters_adjoin_across_new_and_review_piles() {
        let mut col = Collection::new();
        let fi_new = col.add_tagged_note("duration A", FI_TAGS, false);
        let quant_new = col.add_tagged_note("inventory A", QUANT_TAGS, false);
        // background noise, some due today, some new
        for i in 0..4 {
            col.add_tagged_note(&format!("background {i}"), &[], i % 2 == 0);
        }
        let fi_due = col.add_tagged_note("duration B", FI_TAGS, true);
        let quant_due = col.add_tagged_note("inventory B", QUANT_TAGS, true);

        col.disable_contrast();
        let vanilla = col.queue_notes();

        col.enable_contrast();
        let contrasted = col.queue_notes();

        assert_adjacent_run(&contrasted, &[fi_new.id, fi_due.id]);
        assert_adjacent_run(&contrasted, &[quant_new.id, quant_due.id]);
        // pure reordering: exactly the same cards, just in a new order
        assert_eq!(sorted_cards(&contrasted), sorted_cards(&vanilla));
        assert_eq!(contrasted.len(), vanilla.len());
    }

    /// C13: with no cluster tags anywhere, the pass must leave the queue
    /// untouched rather than falling back to grouping by unrelated tags.
    #[test]
    fn noop_without_cluster_tags() {
        let mut col = Collection::new();
        for i in 0..3 {
            col.add_tagged_note(
                &format!("plain {i}"),
                &["some::reading::tag", "cfa::topic::quant"],
                i == 0,
            );
        }
        col.disable_contrast();
        let vanilla = col.queue_notes();
        col.enable_contrast();
        assert_eq!(col.queue_notes(), vanilla);
    }

    /// R28: a cluster tag shared across two different topics does not create
    /// adjacency; cross-topic cards get no contrast credit.
    #[test]
    fn clusters_do_not_bridge_topics() {
        let mut col = Collection::new();
        col.add_tagged_note(
            "fi member",
            &["cluster::x", "cfa::topic::fixed_income"],
            false,
        );
        col.add_tagged_note("separator 1", &[], false);
        col.add_tagged_note("separator 2", &[], false);
        col.add_tagged_note("quant member", &["cluster::x", "cfa::topic::quant"], false);

        col.disable_contrast();
        let vanilla = col.queue_notes();
        col.enable_contrast();
        // both cluster::x members are singletons within their topic, so the
        // queue is untouched
        assert_eq!(col.queue_notes(), vanilla);
    }

    /// R18: with a confusability marker configured, a cluster whose notes
    /// lack the marker keeps default SRS spacing; a marked cluster still
    /// adjoins. The signed gate separates "confusable" from "merely similar".
    #[test]
    fn confusability_gate_blocks_unmarked_clusters() {
        let mut col = Collection::new();
        // marked cluster: known confusables (the marker tag is written by
        // the offline confusion-mining pass)
        let marked_a = col.add_tagged_note(
            "duration A",
            &[
                "cluster::fi::duration",
                "cfa::topic::fixed_income",
                "confusable::high",
            ],
            false,
        );
        // unmarked cluster: merely similar
        let unmarked_a = col.add_tagged_note("inventory A", QUANT_TAGS, false);
        for i in 0..4 {
            col.add_tagged_note(&format!("background {i}"), &[], i % 2 == 0);
        }
        let marked_b = col.add_tagged_note(
            "duration B",
            &["cluster::fi::duration", "cfa::topic::fixed_income"],
            true,
        );
        let unmarked_b = col.add_tagged_note("inventory B", QUANT_TAGS, true);

        col.disable_contrast();
        let vanilla = col.queue_notes();

        col.enable_contrast();
        col.set_confusable_marker("confusable::high");
        let contrasted = col.queue_notes();

        // the marked cluster is forced adjacent (one marked member is enough)
        assert_adjacent_run(&contrasted, &[marked_a.id, marked_b.id]);
        // the unmarked cluster keeps its vanilla relative order
        let unmarked = [unmarked_a.id, unmarked_b.id];
        assert_eq!(
            positions_of(&vanilla, &unmarked).len(),
            2,
            "sanity: both unmarked cards present"
        );
        let vanilla_order: Vec<NoteId> = vanilla
            .iter()
            .filter(|(_, note)| unmarked.contains(note))
            .map(|(_, note)| *note)
            .collect();
        let contrasted_order: Vec<NoteId> = contrasted
            .iter()
            .filter(|(_, note)| unmarked.contains(note))
            .map(|(_, note)| *note)
            .collect();
        assert_eq!(vanilla_order, contrasted_order);
        // still a pure permutation
        assert_eq!(sorted_cards(&contrasted), sorted_cards(&vanilla));

        // an empty marker restores the legacy ungated behaviour
        col.set_confusable_marker("");
        let ungated = col.queue_notes();
        assert_adjacent_run(&ungated, &[unmarked_a.id, unmarked_b.id]);
    }

    /// R18: a child tag under the marker (e.g. a mined score bucket) also
    /// switches the gate on.
    #[test]
    fn confusability_marker_matches_child_tags() {
        let mut col = Collection::new();
        let a = col.add_tagged_note(
            "duration A",
            &[
                "cluster::fi::duration",
                "cfa::topic::fixed_income",
                "confusable::high::mined",
            ],
            false,
        );
        col.add_tagged_note("separator", &[], false);
        let b = col.add_tagged_note(
            "duration B",
            &["cluster::fi::duration", "cfa::topic::fixed_income"],
            false,
        );

        col.enable_contrast();
        col.set_confusable_marker("confusable::high");
        assert_adjacent_run(&col.queue_notes(), &[a.id, b.id]);
    }

    /// C10: two templates of the same note never adjoin inside a contrast
    /// run; a different-note card is slotted between them.
    #[test]
    fn sibling_templates_do_not_adjoin() {
        let mut col = Collection::new();
        // keep gather order = insertion order so the sibling pair starts out
        // adjacent and the guard has real work to do
        col.update_default_deck_config(|config| {
            config.new_card_sort_order = NewCardSortOrder::NoSort as i32;
        });
        col.enable_contrast();

        // a two-card note and a one-card note in the same cluster
        let nt = col.basic_rev_notetype();
        let mut two_card_note = nt.new_note();
        two_card_note.set_field(0, "duration front").unwrap();
        two_card_note.set_field(1, "duration back").unwrap();
        two_card_note.tags = FI_TAGS.iter().map(ToString::to_string).collect();
        col.add_note(&mut two_card_note, DeckId(1)).unwrap();
        let single = col.add_tagged_note("modified duration", FI_TAGS, false);

        let queue = col.queue_notes();
        let cluster_positions = positions_of(&queue, &[two_card_note.id, single.id]);
        assert_eq!(cluster_positions.len(), 3);
        for pair in queue.windows(2) {
            assert_ne!(pair[0].1, pair[1].1, "sibling templates adjoin: {queue:?}");
        }
    }
}
