// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Held-out probe outcome extraction ([R7]/C8, Phase 3 M3).
//!
//! The probe bank is a hand-authored deck of application MCQs whose notes
//! carry `probe::held_out` plus a pool tag (`probe::pool::performance` or
//! `probe::pool::calibration`) and the `cluster::*` tag of the material
//! each probe paraphrases. Outcomes are read from the revlog:
//!
//! * The OUTCOME of a probe is its FIRST graded answer (`ease > 0`); Again =
//!   incorrect, Hard/Good/Easy = correct (Anki's true-retention convention).
//!   Later answers are practice on a burned probe and are never counted.
//! * A probe outcome is DELAYED when the probe was first answered ≥ 7 days
//!   after the last graded review of any non-probe card in its cluster (the
//!   "last study touch"). Immediate accuracy overstates transfer (Rohrer 2015),
//!   so undelayed outcomes are logged but excluded.
//! * A probe whose cluster was never studied cannot be recency-inflated; it
//!   counts as delayed and is reported separately (`never_studied`).
//!
//! Only the *performance* pool feeds the Readiness estimate. The
//! *calibration* pool is reserved for the offline harness so Readiness is
//! never calibrated against its own inputs (no circularity, M3).

use std::collections::HashMap;

use crate::prelude::*;
use crate::search::SortMode;

/// Marks every note in the held-out probe bank.
pub const PROBE_HELD_OUT_TAG: &str = "probe::held_out";
/// The pool whose outcomes feed the Readiness estimate.
pub const PROBE_POOL_PERFORMANCE_TAG: &str = "probe::pool::performance";
/// The pool reserved for offline calibration (never feeds the estimate).
pub const PROBE_POOL_CALIBRATION_TAG: &str = "probe::pool::calibration";
/// Minimum study→probe lag for an outcome to count ([R7]).
pub const MIN_PROBE_DELAY_DAYS: f64 = 7.0;

const MILLIS_PER_DAY: f64 = 86_400_000.0;

#[derive(Debug, Default, PartialEq)]
pub(crate) struct ProbeOutcomes {
    /// x: correct delayed performance-pool outcomes.
    pub correct: u32,
    /// n: all delayed performance-pool outcomes.
    pub delayed: u32,
    /// Performance-pool probes answered too soon after study (excluded).
    pub undelayed: u32,
    /// Performance-pool probes with no graded answer yet.
    pub unanswered: u32,
    /// Of the delayed outcomes, how many came from never-studied clusters.
    pub never_studied: u32,
    /// Mean study→probe lag in days over delayed outcomes that had a prior
    /// study touch (never-studied probes carry no lag).
    pub mean_lag_days: f32,
    /// Calibration-pool probes with a graded first answer (harness food).
    pub calibration_answered: u32,
    /// All graded revlog entries on any probe card (either pool), so the
    /// caller can keep the study-review gate free of probe answers.
    pub probe_graded_reviews: u64,
    /// Total probe cards found (both pools).
    pub probe_cards: u32,
}

struct ProbeCard {
    card_id: CardId,
    performance_pool: bool,
    cluster: Option<String>,
}

impl Collection {
    pub(crate) fn collect_probe_outcomes(&mut self) -> Result<ProbeOutcomes> {
        let probe_cids = self.search_cards(
            format!("tag:{PROBE_HELD_OUT_TAG}").as_str(),
            SortMode::NoOrder,
        )?;
        let mut outcomes = ProbeOutcomes {
            probe_cards: probe_cids.len() as u32,
            ..Default::default()
        };
        if probe_cids.is_empty() {
            return Ok(outcomes);
        }

        // batch-load the probes' note tags to find pool + cluster
        let mut card_note: HashMap<CardId, NoteId> = HashMap::new();
        for &cid in &probe_cids {
            let card = self.storage.get_card(cid)?.or_not_found(cid)?;
            card_note.insert(cid, card.note_id);
        }
        let mut note_ids: Vec<NoteId> = card_note.values().copied().collect();
        note_ids.sort_unstable();
        note_ids.dedup();
        let mut note_info: HashMap<NoteId, (bool, bool, Option<String>)> = HashMap::new();
        for note_tags in self.storage.get_note_tags_by_id_list(&note_ids)? {
            let tags: Vec<String> = note_tags
                .tags
                .split_whitespace()
                .map(str::to_ascii_lowercase)
                .collect();
            let performance = tags.iter().any(|t| t == PROBE_POOL_PERFORMANCE_TAG);
            let calibration = tags.iter().any(|t| t == PROBE_POOL_CALIBRATION_TAG);
            let cluster = tags
                .iter()
                .find(|t| t.starts_with("cluster::") && t.len() > "cluster::".len())
                .cloned();
            note_info.insert(note_tags.id, (performance, calibration, cluster));
        }
        let probes: Vec<ProbeCard> = probe_cids
            .iter()
            .filter_map(|cid| {
                let note_id = card_note.get(cid)?;
                let (performance, calibration, cluster) = note_info.get(note_id)?;
                if !performance && !calibration {
                    // held-out but unpooled: ignore rather than guess
                    return None;
                }
                Some(ProbeCard {
                    card_id: *cid,
                    performance_pool: *performance,
                    cluster: cluster.clone(),
                })
            })
            .collect();

        // first graded answer per probe card
        let mut first_answer: HashMap<CardId, (TimestampMillis, bool)> = HashMap::new();
        for entry in self.storage.get_revlog_entries_for_card_ids(&probe_cids)? {
            if entry.button_chosen == 0 {
                continue;
            }
            outcomes.probe_graded_reviews += 1;
            first_answer
                .entry(entry.cid)
                .or_insert((TimestampMillis(entry.id.0), entry.button_chosen >= 2));
        }

        // graded study times (millis, ascending) per cluster, probes excluded
        let mut clusters: Vec<String> = probes.iter().filter_map(|p| p.cluster.clone()).collect();
        clusters.sort_unstable();
        clusters.dedup();
        let mut study_times: HashMap<String, Vec<i64>> = HashMap::new();
        for cluster in clusters {
            let cids = self.search_cards(
                format!("tag:{cluster} -tag:{PROBE_HELD_OUT_TAG}").as_str(),
                SortMode::NoOrder,
            )?;
            let mut times: Vec<i64> = if cids.is_empty() {
                vec![]
            } else {
                self.storage
                    .get_revlog_entries_for_card_ids(&cids)?
                    .into_iter()
                    .filter(|e| e.button_chosen > 0)
                    .map(|e| e.id.0)
                    .collect()
            };
            times.sort_unstable();
            study_times.insert(cluster, times);
        }

        let mut lag_sum_days = 0.0f64;
        let mut lag_count = 0u32;
        for probe in &probes {
            let answer = first_answer.get(&probe.card_id);
            if !probe.performance_pool {
                if answer.is_some() {
                    outcomes.calibration_answered += 1;
                }
                continue;
            }
            let Some(&(answered_at, correct)) = answer else {
                outcomes.unanswered += 1;
                continue;
            };
            let last_study = probe.cluster.as_ref().and_then(|cluster| {
                let times = study_times.get(cluster)?;
                // last study touch strictly before the probe's first answer
                let idx = times.partition_point(|&t| t < answered_at.0);
                (idx > 0).then(|| times[idx - 1])
            });
            match last_study {
                None => {
                    // never studied (or no cluster tag): cannot be
                    // recency-inflated, counts as delayed
                    outcomes.delayed += 1;
                    outcomes.never_studied += 1;
                    if correct {
                        outcomes.correct += 1;
                    }
                }
                Some(last) => {
                    let lag_days = (answered_at.0 - last) as f64 / MILLIS_PER_DAY;
                    if lag_days >= MIN_PROBE_DELAY_DAYS {
                        outcomes.delayed += 1;
                        lag_sum_days += lag_days;
                        lag_count += 1;
                        if correct {
                            outcomes.correct += 1;
                        }
                    } else {
                        outcomes.undelayed += 1;
                    }
                }
            }
        }
        if lag_count > 0 {
            outcomes.mean_lag_days = (lag_sum_days / lag_count as f64) as f32;
        }
        Ok(outcomes)
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::revlog::RevlogEntry;
    use crate::revlog::RevlogId;
    use crate::revlog::RevlogReviewKind;
    use crate::tests::NoteAdder;

    fn add_note(col: &mut Collection, front: &str, tags: &[&str]) -> Note {
        NoteAdder::basic(col)
            .fields(&[front, "back"])
            .tags(tags)
            .add(col)
    }

    fn first_card(col: &mut Collection, note: &Note) -> CardId {
        col.storage.card_ids_of_notes(&[note.id]).unwrap()[0]
    }

    fn log_answer(col: &mut Collection, cid: CardId, at_days_ago: i64, button: u8) {
        let now = TimestampMillis::now().0;
        let entry = RevlogEntry {
            id: RevlogId(now - at_days_ago * 86_400_000),
            cid,
            usn: Usn(0),
            button_chosen: button,
            interval: 1,
            last_interval: 0,
            ease_factor: 0,
            taken_millis: 1000,
            review_kind: RevlogReviewKind::Review,
        };
        col.storage.add_revlog_entry(&entry, true).unwrap();
    }

    #[test]
    fn no_probes_is_empty() -> Result<()> {
        let mut col = Collection::new();
        add_note(&mut col, "study", &["cluster::fi::duration"]);
        let outcomes = col.collect_probe_outcomes()?;
        assert_eq!(outcomes, ProbeOutcomes::default());
        Ok(())
    }

    #[test]
    fn delayed_undelayed_and_unanswered_probes_are_partitioned() -> Result<()> {
        let mut col = Collection::new();
        let study = add_note(&mut col, "study", &["cluster::fi::duration"]);
        let study_cid = first_card(&mut col, &study);
        // cluster last studied 10 days ago
        log_answer(&mut col, study_cid, 10, 3);

        let probe_tags = &[
            "probe::held_out",
            "probe::pool::performance",
            "cluster::fi::duration",
        ];
        // answered 1 day ago → lag 9 days → delayed, correct
        let delayed_ok = add_note(&mut col, "p1", probe_tags);
        let cid = first_card(&mut col, &delayed_ok);
        log_answer(&mut col, cid, 1, 3);
        // answered 8 days ago → lag 2 days → undelayed (excluded)
        let undelayed = add_note(&mut col, "p2", probe_tags);
        let cid = first_card(&mut col, &undelayed);
        log_answer(&mut col, cid, 8, 1);
        // never answered
        add_note(&mut col, "p3", probe_tags);
        // never-studied cluster → counts as delayed; Again → incorrect
        let never = add_note(
            &mut col,
            "p4",
            &[
                "probe::held_out",
                "probe::pool::performance",
                "cluster::qm::tvm",
            ],
        );
        let cid = first_card(&mut col, &never);
        log_answer(&mut col, cid, 1, 1);

        let outcomes = col.collect_probe_outcomes()?;
        assert_eq!(outcomes.probe_cards, 4);
        assert_eq!(outcomes.delayed, 2);
        assert_eq!(outcomes.correct, 1);
        assert_eq!(outcomes.undelayed, 1);
        assert_eq!(outcomes.unanswered, 1);
        assert_eq!(outcomes.never_studied, 1);
        assert!((outcomes.mean_lag_days - 9.0).abs() < 0.1);
        assert_eq!(outcomes.probe_graded_reviews, 3);
        Ok(())
    }

    #[test]
    fn first_answer_is_the_outcome_and_pools_stay_disjoint() -> Result<()> {
        let mut col = Collection::new();
        let probe = add_note(
            &mut col,
            "p",
            &[
                "probe::held_out",
                "probe::pool::performance",
                "cluster::qm::tvm",
            ],
        );
        let cid = first_card(&mut col, &probe);
        // first answer wrong (5 days ago), later answer right (1 day ago):
        // only the first counts
        log_answer(&mut col, cid, 5, 1);
        log_answer(&mut col, cid, 1, 3);

        let calib = add_note(
            &mut col,
            "c",
            &[
                "probe::held_out",
                "probe::pool::calibration",
                "cluster::qm::tvm",
            ],
        );
        let cid = first_card(&mut col, &calib);
        log_answer(&mut col, cid, 2, 3);

        let outcomes = col.collect_probe_outcomes()?;
        assert_eq!(outcomes.delayed, 1);
        assert_eq!(outcomes.correct, 0);
        assert_eq!(outcomes.calibration_answered, 1);
        // three graded probe answers in total (2 + 1)
        assert_eq!(outcomes.probe_graded_reviews, 3);
        Ok(())
    }

    /// A probe answer must never count as the cluster's "last study touch"
    /// for other probes: only non-probe cards are study evidence.
    #[test]
    fn probe_answers_are_not_study_touches() -> Result<()> {
        let mut col = Collection::new();
        let tags = &[
            "probe::held_out",
            "probe::pool::performance",
            "cluster::fsa::inventory",
        ];
        let one = add_note(&mut col, "p1", tags);
        let cid = first_card(&mut col, &one);
        log_answer(&mut col, cid, 2, 3);
        let two = add_note(&mut col, "p2", tags);
        let cid = first_card(&mut col, &two);
        log_answer(&mut col, cid, 1, 3);

        let outcomes = col.collect_probe_outcomes()?;
        // both count as never-studied/delayed despite each other's answers
        assert_eq!(outcomes.delayed, 2);
        assert_eq!(outcomes.never_studied, 2);
        Ok(())
    }
}
