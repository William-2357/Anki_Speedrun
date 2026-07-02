// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Concept-graph knowledge map (Anki Speedrun).
//!
//! Builds, for a deck (or the whole collection), a graph with one node per
//! tag and one edge per pair of tags that co-occur on a note. Nodes carry
//! recall (mean FSRS retrievability) and an answer-difficulty signal (the
//! share of graded answers that were Again/Hard), so the frontend can colour
//! weak or confusable areas. Two SQL passes over the searched set, no
//! per-card statements; the engine stays exam-agnostic - tag semantics
//! (topics, clusters, readings) live in the frontend.

use std::collections::HashMap;

use anki_proto::stats::concept_graph_response::Edge;
use anki_proto::stats::concept_graph_response::Node;
use anki_proto::stats::ConceptGraphResponse;

use crate::config::BoolKey;
use crate::prelude::*;
use crate::search::SearchNode;
use crate::search::SortMode;

#[derive(Debug, Default)]
struct NodeAccumulator {
    card_count: u32,
    studied_cards: u32,
    retrievability_sum: f64,
    graded_answers: u32,
    again_hard_answers: u32,
}

impl Collection {
    /// See [ConceptGraphResponse] in `stats.proto`. `deck_id` 0 means the
    /// whole collection; child decks are included.
    pub fn concept_graph(&mut self, deck_id: DeckId) -> Result<ConceptGraphResponse> {
        let timing = self.timing_today()?;
        let search = if deck_id.0 == 0 {
            SearchNode::WholeCollection
        } else {
            SearchNode::from_deck_id(deck_id, true)
        };

        let mut node_indices: HashMap<String, usize> = HashMap::new();
        let mut accumulators: Vec<NodeAccumulator> = Vec::new();
        let mut note_tags: HashMap<NoteId, Vec<usize>> = HashMap::new();
        {
            let guard = self.search_cards_into_table(search, SortMode::NoOrder)?;
            guard
                .col
                .storage
                .for_each_searched_card_note_tags_and_retrievability(
                    timing,
                    |note_id, tags, retrievability| {
                        let indices = note_tags.entry(note_id).or_insert_with(|| {
                            tags.split_whitespace()
                                .map(|tag| {
                                    let tag = tag.to_ascii_lowercase();
                                    let next = node_indices.len();
                                    *node_indices.entry(tag).or_insert_with(|| {
                                        accumulators.push(NodeAccumulator::default());
                                        next
                                    })
                                })
                                .collect()
                        });
                        for &index in indices.iter() {
                            let acc = &mut accumulators[index];
                            acc.card_count += 1;
                            if let Some(r) = retrievability {
                                acc.studied_cards += 1;
                                acc.retrievability_sum += r as f64;
                            }
                        }
                    },
                )?;

            guard
                .col
                .storage
                .for_each_searched_card_graded_answer(|tags, ease| {
                    for tag in tags.split_whitespace() {
                        if let Some(&index) = node_indices.get(&tag.to_ascii_lowercase()) {
                            let acc = &mut accumulators[index];
                            acc.graded_answers += 1;
                            if ease <= 2 {
                                acc.again_hard_answers += 1;
                            }
                        }
                    }
                })?;
            // guard dropped; temporary search table cleaned up
        }

        // co-occurrence edges: count notes carrying each tag pair
        let mut edge_counts: HashMap<(usize, usize), u32> = HashMap::new();
        for indices in note_tags.values() {
            for (position, &first) in indices.iter().enumerate() {
                for &second in &indices[position + 1..] {
                    let key = if first < second {
                        (first, second)
                    } else {
                        (second, first)
                    };
                    *edge_counts.entry(key).or_default() += 1;
                }
            }
        }

        let mut tags: Vec<(String, usize)> = node_indices.into_iter().collect();
        tags.sort_by(|a, b| a.1.cmp(&b.1));
        let nodes: Vec<Node> = tags
            .into_iter()
            .map(|(tag, index)| {
                let acc = &accumulators[index];
                Node {
                    tag,
                    card_count: acc.card_count,
                    studied_cards: acc.studied_cards,
                    average_retrievability: if acc.studied_cards > 0 {
                        (acc.retrievability_sum / acc.studied_cards as f64) as f32
                    } else {
                        0.0
                    },
                    graded_answers: acc.graded_answers,
                    again_hard_answers: acc.again_hard_answers,
                }
            })
            .collect();

        let mut edges: Vec<Edge> = edge_counts
            .into_iter()
            .map(|((first, second), note_count)| Edge {
                first: first as u32,
                second: second as u32,
                note_count,
            })
            .collect();
        edges.sort_by(|a, b| (a.first, a.second).cmp(&(b.first, b.second)));

        Ok(ConceptGraphResponse {
            nodes,
            edges,
            fsrs_enabled: self.get_config_bool(BoolKey::Fsrs),
        })
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::tests::DeckAdder;
    use crate::tests::NoteAdder;

    fn node<'a>(response: &'a ConceptGraphResponse, tag: &str) -> &'a Node {
        response
            .nodes
            .iter()
            .find(|node| node.tag == tag)
            .unwrap_or_else(|| panic!("missing node {tag}"))
    }

    #[test]
    fn cooccurring_tags_share_an_edge() -> Result<()> {
        let mut col = Collection::new();
        for _ in 0..2 {
            NoteAdder::basic(&mut col)
                .fields(&["q", "a"])
                .tags(&["cfa::topic::fixed_income", "cluster::fi::duration"])
                .add(&mut col);
        }
        NoteAdder::basic(&mut col)
            .fields(&["solo", "a"])
            .tags(&["cfa::topic::quant"])
            .add(&mut col);

        let response = col.concept_graph(DeckId(0))?;
        assert_eq!(response.nodes.len(), 3);
        assert_eq!(node(&response, "cfa::topic::fixed_income").card_count, 2);
        assert_eq!(node(&response, "cfa::topic::quant").card_count, 1);

        // the two fixed-income notes carry both tags -> one edge, weight 2
        assert_eq!(response.edges.len(), 1);
        let edge = &response.edges[0];
        assert_eq!(edge.note_count, 2);
        let linked = [
            response.nodes[edge.first as usize].tag.as_str(),
            response.nodes[edge.second as usize].tag.as_str(),
        ];
        assert!(linked.contains(&"cfa::topic::fixed_income"));
        assert!(linked.contains(&"cluster::fi::duration"));
        Ok(())
    }

    #[test]
    fn difficulty_counts_graded_answers_and_deck_scoping_holds() -> Result<()> {
        let mut col = Collection::new();
        let other_deck = DeckAdder::new("Other").add(&mut col);
        NoteAdder::basic(&mut col)
            .fields(&["scoped out", "a"])
            .tags(&["cluster::x"])
            .deck(other_deck.id)
            .add(&mut col);
        NoteAdder::basic(&mut col)
            .fields(&["in default", "a"])
            .tags(&["cluster::y"])
            .add(&mut col);

        // grade the default-deck card once as Again
        col.answer_again();

        let default_only = col.concept_graph(DeckId(1))?;
        assert_eq!(default_only.nodes.len(), 1);
        let graph_node = node(&default_only, "cluster::y");
        assert_eq!(graph_node.graded_answers, 1);
        assert_eq!(graph_node.again_hard_answers, 1);

        let whole = col.concept_graph(DeckId(0))?;
        assert_eq!(whole.nodes.len(), 2);
        assert_eq!(node(&whole, "cluster::x").graded_answers, 0);
        Ok(())
    }
}
