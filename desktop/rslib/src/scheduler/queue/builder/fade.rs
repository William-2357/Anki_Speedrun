// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! FSRS-driven fade gating (Anki Speedrun, SPOV 2).
//!
//! The worked -> faded -> solve ladder: within a cluster, cards tagged
//! `rung::worked`, `rung::faded` and `rung::solve` form a dependency ladder,
//! and at queue-build time exactly one rung per cluster is admitted to the
//! queue; the others are withheld with a bury-style skip.
//!
//! Design constraints (PHASE2_PLAN_V2.md M4 + GRILLING_NOTES.md):
//!
//! * **[A1] Gate in the gather path.** Deck limits are decremented while
//!   `gather_cards()` runs, so the only place a card can be withheld without
//!   consuming a `LimitTreeMap` slot is inside `add_new_card`/`add_due_card` -
//!   exactly like sibling burying. The post-gather contrast seam only reorders
//!   and cannot gate.
//! * **[A2] The fade signal is computed BEFORE the gather query** (one batch
//!   pass over the ladder cards + one revlog query), because FSRS memory state
//!   is not in the lightweight `NewCard`/`DueCard` structs.
//! * **C1: no hand-rolled forgetting curve.** Predicted retrievability at the
//!   exam horizon is computed with `FSRS::current_retrievability_seconds` and
//!   the card's own fitted `decay`, the same primitive
//!   `extract_fsrs_retrievability` uses.
//! * **C2: re-gating is BUILD-time only.** Anki deliberately excludes
//!   `Op::AnswerCard` from queue rebuilds, so a newly-qualified prerequisite
//!   unlocks its dependent on the *next* build (day rollover / manual rebuild),
//!   exactly like sibling burying.
//! * **[R10] Fade signal = predicted retrievability at the exam horizon**
//!   (`speedrun:exam_date` collection-config key). No exam date -> fading falls
//!   back to always-worked rather than guessing a horizon.
//! * **[R11] Two-sided hysteresis** with `fade_up_r > fade_down_r`: fading up
//!   must be harder than falling back down.
//! * **[R12] Promotion gate = spaced-session count**, derived from review-log
//!   timestamps (distinct days with a correct answer, last answer correct) -
//!   never a within-session criterion.
//! * **[R13] Comprehension-first + fluency preconditions** hold the solve rung
//!   (and confusable adjacency, via [FadeContext::fluency_blocked_clusters])
//!   until the cluster has at least one successful encoding and clears the
//!   stability floor.
//! * **[R15] Fade order is mastery-driven by default** (highest predicted
//!   recall first) with backward/forward as ablation arms; applies to the
//!   progressive introduction of faded cloze siblings.
//! * **[R17] Element-interactivity scoping**: with the gate enabled, only
//!   `interactivity::high` clusters enter the ladder.
//!
//! Learning-queue cards (intraday and interday) are never gated: once a card
//! has entered (re)learning, withholding it would corrupt the learning-step
//! flow. The gate acts on new and review cards only.

use std::collections::HashMap;
use std::collections::HashSet;

use fsrs::FSRS;
use fsrs::FSRS5_DEFAULT_DECAY;

use super::contrast::DEFAULT_CONTRAST_TAG_PREFIX;
use super::contrast::TOPIC_TAG_PREFIX;
use super::QueueBuilder;
use crate::card::CardQueue;
use crate::deckconfig::FadeOrder;
use crate::deckconfig::FadeSignal;
use crate::prelude::*;
use crate::revlog::RevlogEntry;
use crate::scheduler::timing::SchedTimingToday;
use crate::storage::card::FadeLadderCardRow;

/// Tag prefix marking a card's rung within its cluster's ladder.
pub(crate) const RUNG_TAG_PREFIX: &str = "rung::";
/// Tag marking a cluster as high element interactivity (formula material,
/// where worked->faded pays off). With `element_interactivity_gate` enabled,
/// only these clusters are gated.
pub(crate) const INTERACTIVITY_HIGH_TAG: &str = "interactivity::high";
/// The collection-config key holding the exam date as `YYYY-MM-DD`.
pub(crate) const EXAM_DATE_CONFIG_KEY: &str = "speedrun:exam_date";

/// Ladder position parsed from a `rung::*` tag.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(crate) enum Rung {
    Worked = 0,
    Faded = 1,
    Solve = 2,
}

impl Rung {
    fn parse(tag_rest: &str) -> Option<Rung> {
        match tag_rest {
            "worked" => Some(Rung::Worked),
            "faded" => Some(Rung::Faded),
            "solve" => Some(Rung::Solve),
            _ => None,
        }
    }
}

/// The queue-build-time outcome of the fade pass.
#[derive(Debug, Default)]
pub(crate) struct FadeContext {
    /// Cards withheld from this build (bury-style skip; limits untouched).
    gated: HashSet<CardId>,
    /// (topic, cluster) keys that failed the comprehension/fluency
    /// preconditions; contrast must not force confusable adjacency on them.
    fluency_blocked: HashSet<(Option<String>, String)>,
}

impl FadeContext {
    pub(crate) fn is_gated(&self, card_id: CardId) -> bool {
        self.gated.contains(&card_id)
    }

    pub(crate) fn fluency_blocked_clusters(&self) -> &HashSet<(Option<String>, String)> {
        &self.fluency_blocked
    }
}

/// One card's ladder-relevant state.
#[derive(Debug)]
struct LadderCard {
    card_id: CardId,
    note_id: NoteId,
    rung: Rung,
    template_index: u16,
    queue: CardQueue,
    /// Predicted retrievability at the exam horizon, if studied.
    horizon_r: Option<f32>,
    /// Predicted retrievability now (for mastery-driven fade order).
    current_r: Option<f32>,
    stability: Option<f32>,
    studied: bool,
}

#[derive(Debug, Default)]
struct Cluster {
    cards: Vec<LadderCard>,
}

/// Per-rung review evidence derived from the revlog.
#[derive(Debug, Default, Clone, Copy)]
struct RungHistory {
    /// Distinct days with at least one correct (non-Again) graded answer.
    spaced_successful_sessions: u32,
    /// Whether the chronologically-last graded answer was correct.
    last_answer_correct: bool,
    /// Id of the last graded entry (for merging).
    last_entry_id: i64,
    /// Any correct graded answer at all (comprehension precondition).
    any_correct: bool,
}

/// Tunables snapshot, decoupled from the proto types for testability.
#[derive(Debug, Clone)]
pub(crate) struct FadeOptions {
    pub fade_signal: FadeSignal,
    pub fade_up_r: f32,
    pub fade_down_r: f32,
    pub promotion_spaced_sessions: u32,
    pub fluency_stability_floor: f32,
    pub fade_order: FadeOrder,
    pub self_explain_enabled: bool,
    pub element_interactivity_gate: bool,
}

impl Default for FadeOptions {
    fn default() -> Self {
        FadeOptions {
            fade_signal: FadeSignal::ExamHorizonR,
            fade_up_r: 0.9,
            fade_down_r: 0.8,
            promotion_spaced_sessions: 3,
            fluency_stability_floor: 0.0,
            fade_order: FadeOrder::Mastery,
            self_explain_enabled: false,
            element_interactivity_gate: true,
        }
    }
}

impl QueueBuilder {
    /// Populate the fade gate. Must run before `gather_cards()`, so the
    /// gather callbacks can consult it without disturbing deck limits.
    pub(super) fn load_fade_gate(&mut self, col: &mut Collection) -> Result<()> {
        if !self.context.sort_options.fade_enabled {
            return Ok(());
        }
        let options = FadeOptions {
            fade_signal: self.context.sort_options.fade_signal,
            fade_up_r: self.context.sort_options.fade_up_r,
            fade_down_r: self.context.sort_options.fade_down_r,
            promotion_spaced_sessions: self.context.sort_options.promotion_spaced_sessions.max(1),
            fluency_stability_floor: self.context.sort_options.fluency_stability_floor,
            fade_order: self.context.sort_options.fade_order,
            self_explain_enabled: self.context.sort_options.self_explain_enabled,
            element_interactivity_gate: self.context.sort_options.element_interactivity_gate,
        };
        // [R10]: without an exam date there is no defensible horizon; fall
        // back to always-worked instead of guessing one.
        let days_to_exam = days_until_exam(col, self.context.timing)?;

        let cluster_prefix = {
            let configured = self
                .context
                .sort_options
                .contrast_tag_prefix
                .trim()
                .to_ascii_lowercase();
            if configured.is_empty() {
                DEFAULT_CONTRAST_TAG_PREFIX.to_string()
            } else {
                configured
            }
        };

        // One pass over the ladder cards ([A2]: before the gather query).
        let mut cluster_index: HashMap<(Option<String>, String), usize> = HashMap::new();
        let mut cluster_keys: Vec<(Option<String>, String)> = Vec::new();
        let mut cluster_list: Vec<Cluster> = Vec::new();
        let mut ladder_card_ids: Vec<CardId> = Vec::new();
        let timing = self.context.timing;
        col.storage.for_each_active_deck_card_with_tag_substring(
            RUNG_TAG_PREFIX,
            |row: FadeLadderCardRow| {
                let tags: Vec<String> = row
                    .tags
                    .split_whitespace()
                    .map(str::to_ascii_lowercase)
                    .collect();
                let Some(rung) = tags
                    .iter()
                    .find_map(|tag| tag.strip_prefix(RUNG_TAG_PREFIX).and_then(Rung::parse))
                else {
                    return;
                };
                let Some(cluster) = tags.iter().find_map(|tag| {
                    tag.strip_prefix(cluster_prefix.as_str())
                        .filter(|rest| !rest.is_empty())
                        .map(ToString::to_string)
                }) else {
                    // a rung tag without a cluster has no ladder to gate
                    return;
                };
                if options.element_interactivity_gate
                    && !tags.iter().any(|tag| tag == INTERACTIVITY_HIGH_TAG)
                {
                    // [R17]: atomic-fact clusters stay on plain FSRS
                    return;
                }
                let topic = tags
                    .iter()
                    .find(|tag| tag.starts_with(TOPIC_TAG_PREFIX))
                    .cloned();

                let memory_state = row.data.memory_state();
                let decay = row.data.decay.unwrap_or(FSRS5_DEFAULT_DECAY);
                let horizon_r = memory_state.map(|state| {
                    predicted_retrievability(
                        state,
                        decay,
                        seconds_elapsed_at_horizon(&row, timing, days_to_exam.unwrap_or(0)),
                    )
                });
                let current_r = memory_state.map(|state| {
                    predicted_retrievability(
                        state,
                        decay,
                        seconds_elapsed_at_horizon(&row, timing, 0),
                    )
                });

                ladder_card_ids.push(row.card_id);
                let key = (topic, cluster);
                let idx = *cluster_index.entry(key.clone()).or_insert_with(|| {
                    cluster_keys.push(key);
                    cluster_list.push(Cluster::default());
                    cluster_list.len() - 1
                });
                cluster_list[idx].cards.push(LadderCard {
                    card_id: row.card_id,
                    note_id: row.note_id,
                    rung,
                    template_index: row.template_index,
                    queue: row.queue,
                    horizon_r,
                    current_r,
                    stability: memory_state.map(|s| s.stability),
                    studied: memory_state.is_some(),
                });
            },
        )?;

        if cluster_list.is_empty() {
            return Ok(());
        }

        // Rung-level review evidence, one batch revlog query ([R12]).
        ladder_card_ids.sort_unstable();
        let revlog = col
            .storage
            .get_revlog_entries_for_card_ids(&ladder_card_ids)?;
        let rung_of_card: HashMap<CardId, (usize, Rung)> = cluster_list
            .iter()
            .enumerate()
            .flat_map(|(idx, cluster)| {
                cluster
                    .cards
                    .iter()
                    .map(move |card| (card.card_id, (idx, card.rung)))
            })
            .collect();
        let mut histories: HashMap<(usize, Rung), RungHistory> = HashMap::new();
        accumulate_rung_histories(&revlog, &rung_of_card, &mut histories);

        let mut context = FadeContext::default();
        for (cluster_idx, cluster) in cluster_list.iter().enumerate() {
            let history_of = |rung: Rung| -> RungHistory {
                histories
                    .get(&(cluster_idx, rung))
                    .copied()
                    .unwrap_or_default()
            };
            let decision = resolve_cluster(cluster, &options, days_to_exam, history_of);
            if decision.fluency_blocked {
                context
                    .fluency_blocked
                    .insert(cluster_keys[cluster_idx].clone());
            }
            for card in &cluster.cards {
                // never gate a card that is already in a learning queue
                let in_learning = matches!(
                    card.queue,
                    CardQueue::Learn | CardQueue::DayLearn | CardQueue::PreviewRepeat
                );
                if !in_learning && !decision.admitted.contains(&card.card_id) {
                    context.gated.insert(card.card_id);
                }
            }
        }

        self.fade = context;
        Ok(())
    }
}

/// The per-cluster gating decision.
#[derive(Debug, Default)]
struct ClusterDecision {
    admitted: HashSet<CardId>,
    fluency_blocked: bool,
}

/// Decide which of the cluster's cards this build admits.
fn resolve_cluster(
    cluster: &Cluster,
    options: &FadeOptions,
    days_to_exam: Option<u32>,
    history_of: impl Fn(Rung) -> RungHistory,
) -> ClusterDecision {
    let mut decision = ClusterDecision::default();
    let existing_rungs: Vec<Rung> = {
        let mut rungs: Vec<Rung> = cluster.cards.iter().map(|card| card.rung).collect();
        rungs.sort_unstable();
        rungs.dedup();
        rungs
    };
    if existing_rungs.is_empty() {
        return decision;
    }

    // [R13] comprehension-first + minimum-fluency preconditions for the
    // solve rung and for confusable adjacency.
    let preconditions_met = {
        let any_correct_below_solve = [Rung::Worked, Rung::Faded]
            .into_iter()
            .any(|rung| history_of(rung).any_correct);
        let studied_support: Vec<&LadderCard> = cluster
            .cards
            .iter()
            .filter(|card| card.rung != Rung::Solve && card.studied)
            .collect();
        let floor = options.fluency_stability_floor;
        let above_floor = floor <= 0.0
            || (!studied_support.is_empty()
                && studied_support
                    .iter()
                    .all(|card| card.stability.unwrap_or(0.0) >= floor));
        any_correct_below_solve && above_floor
    };
    decision.fluency_blocked = !preconditions_met;

    // No exam horizon -> always-worked ([R10]): serve the lowest rung only.
    let Some(days_to_exam) = days_to_exam else {
        admit_rung(cluster, existing_rungs[0], options, &mut decision);
        return decision;
    };

    // Highest rung unlocked by the spaced-session promotion gate ([R12]).
    // Missing rungs pass vacuously; a present rung must have logged enough
    // spaced successful sessions, with the last answer correct.
    let mut unlocked = Rung::Worked;
    for rung in [Rung::Worked, Rung::Faded] {
        let present = existing_rungs.contains(&rung);
        let history = history_of(rung);
        let passed = !present
            || (history.spaced_successful_sessions >= options.promotion_spaced_sessions
                && history.last_answer_correct);
        if passed {
            unlocked = next_rung(rung);
        } else {
            break;
        }
    }

    // The learner's current rung: the highest rung with any graded history.
    let current = [Rung::Solve, Rung::Faded, Rung::Worked]
        .into_iter()
        .find(|rung| history_of(*rung).last_entry_id != 0)
        .unwrap_or(Rung::Worked);
    // A rung with real graded history has evidently been reached (possibly
    // on another device, or before the ladder tags landed): never re-lock
    // below it - the promotion gate only guards *advancing further*.
    if current > unlocked {
        unlocked = current;
    }

    // Fade signal over the current rung's studied cards ([R10]/[R11]).
    let signal = cluster_signal(cluster, current, options, days_to_exam, &history_of);

    // Two-sided hysteresis ([R11]): above the band move up to the next
    // existing rung, below it fall back to the previous existing one, inside
    // it hold. Movement is relative to the rungs that actually exist, so a
    // ladder missing a middle rung is never stuck.
    let mut served = match signal {
        Some(signal) if signal > options.fade_up_r => existing_rungs
            .iter()
            .find(|rung| **rung > current)
            .copied()
            .unwrap_or_else(|| nearest_at_or_below(&existing_rungs, current)),
        Some(signal) if signal < options.fade_down_r => existing_rungs
            .iter()
            .rev()
            .find(|rung| **rung < current)
            .copied()
            .unwrap_or(existing_rungs[0]),
        _ => nearest_at_or_below(&existing_rungs, current),
    };
    // the promotion gate is a hard cap
    if served > unlocked {
        served = nearest_at_or_below(&existing_rungs, unlocked);
    }
    // [R13]: solve stays locked until the preconditions clear
    if served == Rung::Solve && !preconditions_met {
        served = nearest_at_or_below(&existing_rungs, Rung::Faded);
    }

    admit_rung(cluster, served, options, &mut decision);
    decision
}

/// The highest existing rung at or below `limit`, else the lowest existing
/// one. `existing` must be sorted ascending and non-empty.
fn nearest_at_or_below(existing: &[Rung], limit: Rung) -> Rung {
    existing
        .iter()
        .rev()
        .find(|rung| **rung <= limit)
        .copied()
        .unwrap_or(existing[0])
}

fn next_rung(rung: Rung) -> Rung {
    match rung {
        Rung::Worked => Rung::Faded,
        _ => Rung::Solve,
    }
}

/// Admit the served rung's cards. Within a served faded rung, cloze siblings
/// are introduced progressively in `fade_order`: studied ones stay admitted,
/// plus the single next unstudied one ([R15]). Within a served solve rung,
/// notes carrying a self-explanation template variant serve exactly one
/// sibling, picked by `self_explain_enabled` ([R16], C9: a real template
/// toggle - the flag changes what the learner sees).
fn admit_rung(
    cluster: &Cluster,
    served: Rung,
    options: &FadeOptions,
    decision: &mut ClusterDecision,
) {
    let mut rung_cards: Vec<&LadderCard> = cluster
        .cards
        .iter()
        .filter(|card| card.rung == served)
        .collect();
    if served == Rung::Faded && rung_cards.len() > 1 {
        match options.fade_order {
            FadeOrder::Mastery => {
                // fade the best-known step first: highest predicted recall
                // first, unstudied steps last
                rung_cards.sort_by(|a, b| {
                    let a_key = a.current_r.unwrap_or(-1.0);
                    let b_key = b.current_r.unwrap_or(-1.0);
                    b_key
                        .partial_cmp(&a_key)
                        .unwrap_or(std::cmp::Ordering::Equal)
                        .then(a.template_index.cmp(&b.template_index))
                });
            }
            FadeOrder::Backward => {
                rung_cards.sort_by(|a, b| b.template_index.cmp(&a.template_index));
            }
            FadeOrder::Forward => {
                rung_cards.sort_by(|a, b| a.template_index.cmp(&b.template_index));
            }
        }
        let mut introduced_new_step = false;
        for card in rung_cards {
            if card.studied {
                decision.admitted.insert(card.card_id);
            } else if !introduced_new_step {
                introduced_new_step = true;
                decision.admitted.insert(card.card_id);
            }
        }
    } else if served == Rung::Solve {
        // [R16]: template 0 = plain solve MCQ, higher ordinals = the
        // self-explanation prompt variant; serve exactly one per note
        let mut by_note: HashMap<NoteId, Vec<&LadderCard>> = HashMap::new();
        for card in rung_cards {
            by_note.entry(card.note_id).or_default().push(card);
        }
        for (_, mut siblings) in by_note {
            siblings.sort_by_key(|card| card.template_index);
            let pick = if options.self_explain_enabled {
                siblings.last()
            } else {
                siblings.first()
            };
            if let Some(card) = pick {
                decision.admitted.insert(card.card_id);
            }
        }
    } else {
        for card in rung_cards {
            decision.admitted.insert(card.card_id);
        }
    }
}

/// The cluster's position signal, over the current rung's studied cards.
fn cluster_signal(
    cluster: &Cluster,
    current: Rung,
    options: &FadeOptions,
    days_to_exam: u32,
    history_of: &impl Fn(Rung) -> RungHistory,
) -> Option<f32> {
    match options.fade_signal {
        FadeSignal::ExamHorizonR => mean(
            cluster.cards.iter().filter(|card| card.rung == current),
            |card| card.horizon_r,
        ),
        FadeSignal::Stability => mean(
            cluster.cards.iter().filter(|card| card.rung == current),
            |card| {
                card.stability
                    .map(|s| (s / days_to_exam.max(1) as f32).clamp(0.0, 1.0))
            },
        ),
        FadeSignal::SuccessCount => {
            let history = history_of(current);
            if history.last_entry_id == 0 {
                None
            } else {
                Some(
                    (history.spaced_successful_sessions as f32
                        / options.promotion_spaced_sessions.max(1) as f32)
                        .clamp(0.0, 1.0),
                )
            }
        }
    }
}

fn mean<'a>(
    cards: impl Iterator<Item = &'a LadderCard>,
    value: impl Fn(&LadderCard) -> Option<f32>,
) -> Option<f32> {
    let values: Vec<f32> = cards.filter_map(value).collect();
    if values.is_empty() {
        None
    } else {
        Some(values.iter().sum::<f32>() / values.len() as f32)
    }
}

/// C1: predicted retrievability via the engine's own FSRS primitive - never
/// the legacy hand-rolled `(1+(19/81)t/S)^decay` power law.
fn predicted_retrievability(
    state: crate::card::FsrsMemoryState,
    decay: f32,
    seconds_elapsed: u32,
) -> f32 {
    FSRS::new(None)
        .unwrap()
        .current_retrievability_seconds(state.into(), seconds_elapsed, decay)
}

/// Seconds between the card's last review and `days_ahead` days from now,
/// mirroring the elapsed-time reconstruction in
/// `extract_fsrs_retrievability` (storage/sqlite.rs) with a shifted horizon.
fn seconds_elapsed_at_horizon(
    row: &FadeLadderCardRow,
    timing: SchedTimingToday,
    days_ahead: u32,
) -> u32 {
    let horizon_now = timing.now.0.saturating_add(days_ahead as i64 * 86_400);
    if let Some(last_review_time) = row.data.last_review_time {
        (horizon_now as u32).saturating_sub(last_review_time.0 as u32)
    } else if row.due > 365_000 {
        // (re)learning card: due is a raw timestamp in seconds
        let last_review_time = (row.due as u32).saturating_sub(row.interval);
        (horizon_now as u32).saturating_sub(last_review_time)
    } else {
        let review_day = (row.due as u32).saturating_sub(row.interval);
        (timing.days_elapsed + days_ahead).saturating_sub(review_day) * 86_400
    }
}

/// Sessions = distinct UTC days with >= 1 correct graded answer on the rung;
/// "correct" is any non-Again button. Manual/rescheduled/cramming entries are
/// ignored ([R12]: spaced *successful* relearning passes).
fn accumulate_rung_histories(
    revlog: &[RevlogEntry],
    rung_of_card: &HashMap<CardId, (usize, Rung)>,
    histories: &mut HashMap<(usize, Rung), RungHistory>,
) {
    let mut success_days: HashMap<(usize, Rung), HashSet<i64>> = HashMap::new();
    for entry in revlog {
        if !entry.has_rating_and_affects_scheduling() {
            continue;
        }
        let Some(&key) = rung_of_card.get(&entry.cid) else {
            continue;
        };
        let history = histories.entry(key).or_default();
        let correct = entry.button_chosen >= 2;
        if correct {
            success_days
                .entry(key)
                .or_default()
                .insert(entry.id.as_secs().0.div_euclid(86_400));
        }
        if entry.id.0 >= history.last_entry_id {
            history.last_entry_id = entry.id.0;
            history.last_answer_correct = correct;
        }
        history.any_correct |= correct;
    }
    for (key, days) in success_days {
        if let Some(history) = histories.get_mut(&key) {
            history.spaced_successful_sessions = days.len() as u32;
        }
    }
}

/// Days from today until the configured exam date, if one is set.
/// A past exam date clamps to zero (the horizon is "now").
pub(crate) fn days_until_exam(
    col: &mut Collection,
    timing: SchedTimingToday,
) -> Result<Option<u32>> {
    let Some(date_string) = col.get_config_optional::<String, _>(EXAM_DATE_CONFIG_KEY) else {
        return Ok(None);
    };
    let Ok(exam_date) = chrono::NaiveDate::parse_from_str(date_string.trim(), "%Y-%m-%d") else {
        // an unparsable date is treated as unset: abstain from guessing
        return Ok(None);
    };
    let today = timing.now.local_datetime()?.date_naive();
    Ok(Some(
        (exam_date - today).num_days().clamp(0, u32::MAX as i64) as u32,
    ))
}

#[cfg(test)]
mod test {
    use chrono::Duration;

    use super::*;
    use crate::card::FsrsMemoryState;
    use crate::revlog::RevlogId;
    use crate::tests::NoteAdder;

    const CLUSTER: &[&str] = &[
        "cluster::fi::duration",
        "cfa::topic::fixed_income",
        "interactivity::high",
    ];

    fn tags_for(rung: &str) -> Vec<String> {
        let mut tags: Vec<String> = CLUSTER.iter().map(ToString::to_string).collect();
        tags.push(format!("rung::{rung}"));
        tags
    }

    impl Collection {
        fn enable_fade(&mut self) {
            self.update_default_deck_config(|config| {
                config.fade_enabled = true;
                config.fade_up_r = 0.9;
                config.fade_down_r = 0.8;
                config.promotion_spaced_sessions = 3;
            });
        }

        fn set_exam_date_days_ahead(&mut self, days: i64) {
            let date = (TimestampSecs::now().local_datetime().unwrap().date_naive()
                + Duration::days(days))
            .format("%Y-%m-%d")
            .to_string();
            self.set_config_json(EXAM_DATE_CONFIG_KEY, &date, false)
                .unwrap();
        }

        /// Add a one-card note on the given rung. If `due_today`, the card
        /// becomes a review card due now.
        fn add_rung_note(&mut self, front: &str, rung: &str, due_today: bool) -> Note {
            let tags: Vec<String> = tags_for(rung);
            let tag_refs: Vec<&str> = tags.iter().map(String::as_str).collect();
            let note = NoteAdder::basic(self)
                .fields(&[front, "back"])
                .tags(&tag_refs)
                .add(self);
            if due_today {
                let cids = self.storage.card_ids_of_notes(&[note.id]).unwrap();
                self.set_due_date(&cids, "0", None).unwrap();
            }
            note
        }

        /// Give the note's card an FSRS memory state reviewed just now, so
        /// its exam-horizon retrievability is a pure function of stability.
        fn set_stability(&mut self, note: &Note, stability: f32) {
            let cid = self.storage.card_ids_of_notes(&[note.id]).unwrap()[0];
            self.get_and_update_card(cid, |card| {
                card.memory_state = Some(FsrsMemoryState {
                    stability,
                    difficulty: 5.0,
                });
                card.last_review_time = Some(TimestampSecs::now());
                Ok(())
            })
            .unwrap();
        }

        /// Log a graded review `days_ago` on the note's card.
        fn log_review(&mut self, note: &Note, days_ago: i64, button: u8) {
            let cid = self.storage.card_ids_of_notes(&[note.id]).unwrap()[0];
            // jitter by card so same-day entries for different cards never
            // collide (colliding ids get uniquified to max(id)+1, which
            // would silently move them to "now")
            let at_millis = (TimestampSecs::now().0 - days_ago * 86_400) * 1000 + (cid.0 & 0xff);
            self.storage
                .add_revlog_entry(
                    &RevlogEntry {
                        id: RevlogId(at_millis),
                        cid,
                        usn: Usn(0),
                        button_chosen: button,
                        interval: 1,
                        last_interval: 1,
                        ease_factor: 2500,
                        taken_millis: 1000,
                        review_kind: crate::revlog::RevlogReviewKind::Review,
                    },
                    true,
                )
                .unwrap();
        }

        /// The queue as note ids, in study order.
        fn queue_note_ids(&mut self) -> Vec<NoteId> {
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

    /// A ladder that syncs in from another device is inert until the deck
    /// config turns fading on: default OFF means no card is ever withheld.
    #[test]
    fn fade_off_by_default_gates_nothing() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", false);
        let solve = col.add_rung_note("solve", "solve", false);
        col.set_exam_date_days_ahead(60);

        let queue = col.queue_note_ids();
        for note in [&worked, &faded, &solve] {
            assert!(queue.contains(&note.id), "vanilla queue must be untouched");
        }
    }

    /// [R10]: with fading on but no exam date, there is no defensible
    /// horizon - fall back to always-worked instead of guessing one.
    #[test]
    fn missing_exam_date_serves_worked_only() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", false);
        let solve = col.add_rung_note("solve", "solve", false);
        col.enable_fade();

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(!queue.contains(&faded.id));
        assert!(!queue.contains(&solve.id));
    }

    /// An unstudied ladder starts at the worked rung.
    #[test]
    fn unstudied_ladder_starts_at_worked() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", false);
        let solve = col.add_rung_note("solve", "solve", false);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(!queue.contains(&faded.id));
        assert!(!queue.contains(&solve.id));
    }

    /// [R12]+[R11]: the faded rung unlocks only after the worked rung logs
    /// enough spaced successful sessions AND the fade signal clears the
    /// fade-up bound; re-gating happens on the next build (C2).
    #[test]
    fn promotion_needs_spaced_sessions_and_high_signal() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", true);
        let faded = col.add_rung_note("faded", "faded", false);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        // high stability -> predicted R at the 60-day horizon is > 0.9
        col.set_stability(&worked, 2000.0);

        // only two spaced successful sessions: gate holds
        col.log_review(&worked, 2, 3);
        col.log_review(&worked, 1, 3);
        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(!queue.contains(&faded.id), "gate must hold at 2 sessions");

        // third spaced session: the dependent must appear on the NEXT build
        col.log_review(&worked, 0, 3);
        let queue = col.queue_note_ids();
        assert!(queue.contains(&faded.id), "faded must unlock on next build");
        assert!(
            !queue.contains(&worked.id),
            "the served rung replaces the prerequisite"
        );
    }

    /// [R12]: three spaced sessions with a *failed* last answer do not
    /// promote.
    #[test]
    fn promotion_requires_last_answer_correct() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", true);
        let faded = col.add_rung_note("faded", "faded", false);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        col.set_stability(&worked, 2000.0);

        col.log_review(&worked, 3, 3);
        col.log_review(&worked, 2, 3);
        col.log_review(&worked, 1, 3);
        col.log_review(&worked, 0, 1); // Again
        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(!queue.contains(&faded.id));
    }

    /// [R11] hysteresis, inside the band: hold the current rung.
    #[test]
    fn signal_inside_band_holds_current_rung() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", true);
        let faded = col.add_rung_note("faded", "faded", false);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        // sessions passed, but R at the 60-day horizon sits inside
        // (0.8, 0.9): S=35d -> R ~ 0.845 under the default decay
        col.set_stability(&worked, 35.0);
        col.log_review(&worked, 2, 3);
        col.log_review(&worked, 1, 3);
        col.log_review(&worked, 0, 3);

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id), "hold inside the band");
        assert!(!queue.contains(&faded.id));
    }

    /// [R11] hysteresis, below the band: fall back to the support rung.
    #[test]
    fn low_signal_falls_back_to_worked() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", true);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        // the learner had moved on to the faded rung...
        col.log_review(&faded, 5, 3);
        // ...but its predicted R at the horizon has decayed below 0.8
        col.set_stability(&faded, 10.0);

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id), "fall back to worked");
        assert!(!queue.contains(&faded.id));
    }

    /// [R13]: even with the promotion gate and signal cleared, the solve
    /// rung stays locked while a support card sits under the stability
    /// floor.
    #[test]
    fn fluency_floor_blocks_solve() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", true);
        let solve = col.add_rung_note("solve", "solve", false);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        col.update_default_deck_config(|config| config.fluency_stability_floor = 3000.0);
        // faded rung: promoted (3 spaced sessions, correct) + high signal,
        // but stability 2000 < floor 3000
        col.set_stability(&worked, 2000.0);
        col.set_stability(&faded, 2000.0);
        for days_ago in [2, 1, 0] {
            col.log_review(&worked, days_ago, 3);
            col.log_review(&faded, days_ago, 3);
        }

        let queue = col.queue_note_ids();
        assert!(
            queue.contains(&faded.id),
            "capped at faded while the floor is unmet"
        );
        assert!(!queue.contains(&solve.id));

        // clearing the floor unlocks solve on the next build
        col.update_default_deck_config(|config| config.fluency_stability_floor = 0.0);
        let queue = col.queue_note_ids();
        assert!(queue.contains(&solve.id));
        assert!(!queue.contains(&faded.id));
    }

    /// [A1]: a gated card is a bury-style skip - it must not consume a
    /// deck-limit slot that a later card needed.
    #[test]
    fn gated_cards_do_not_consume_limits() {
        let mut col = Collection::new();
        // gather order = insertion order, new limit 2
        col.update_default_deck_config(|config| {
            config.new_per_day = 2;
            config.new_card_sort_order =
                anki_proto::deck_config::deck_config::config::NewCardSortOrder::NoSort as i32;
        });
        let worked = col.add_rung_note("worked", "worked", false);
        let faded = col.add_rung_note("faded", "faded", false);
        let background = NoteAdder::basic(&mut col)
            .fields(&["background", "back"])
            .add(&mut col);
        col.enable_fade();
        col.set_exam_date_days_ahead(60);

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(
            queue.contains(&background.id),
            "the skipped faded card must not have consumed the second slot"
        );
        assert!(!queue.contains(&faded.id));
        assert_eq!(queue.len(), 2);
    }

    /// [R17]: with the element-interactivity gate on (default), a cluster
    /// without `interactivity::high` stays on plain FSRS - nothing gated.
    #[test]
    fn interactivity_gate_scopes_the_ladder() {
        let mut col = Collection::new();
        fn add_low(col: &mut Collection, front: &str, rung: &str) -> Note {
            let rung_tag = format!("rung::{rung}");
            let tags = ["cluster::fi::facts", "cfa::topic::fixed_income", &rung_tag];
            NoteAdder::basic(col)
                .fields(&[front, "b"])
                .tags(&tags)
                .add(col)
        }
        let worked = add_low(&mut col, "w", "worked");
        let solve = add_low(&mut col, "s", "solve");
        col.enable_fade();
        col.set_exam_date_days_ahead(60);

        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(
            queue.contains(&solve.id),
            "atomic-fact cluster must not be gated"
        );

        // gate off -> the ladder applies and solve is withheld
        col.update_default_deck_config(|config| config.element_interactivity_gate = false);
        let queue = col.queue_note_ids();
        assert!(queue.contains(&worked.id));
        assert!(!queue.contains(&solve.id));
    }

    /// [R15]: a served faded rung introduces unstudied cloze siblings one at
    /// a time, in the configured fade order.
    #[test]
    fn faded_rung_introduces_one_step_at_a_time() {
        let mut col = Collection::new();
        let worked = col.add_rung_note("worked", "worked", true);
        // a two-card faded note (both templates unstudied)
        let nt = col.basic_rev_notetype();
        let mut faded = nt.new_note();
        faded.set_field(0, "step front").unwrap();
        faded.set_field(1, "step back").unwrap();
        faded.tags = tags_for("faded");
        col.add_note(&mut faded, DeckId(1)).unwrap();
        col.enable_fade();
        col.set_exam_date_days_ahead(60);
        col.update_default_deck_config(|config| {
            config.fade_order = crate::deckconfig::FadeOrder::Forward as i32;
        });
        // promote the worked rung
        col.set_stability(&worked, 2000.0);
        for days_ago in [2, 1, 0] {
            col.log_review(&worked, days_ago, 3);
        }

        let queue = col.build_queues(DeckId(1)).unwrap();
        let cards: Vec<Card> = queue
            .iter()
            .map(|entry| col.storage.get_card(entry.card_id()).unwrap().unwrap())
            .collect();
        let faded_cards: Vec<&Card> = cards
            .iter()
            .filter(|card| card.note_id == faded.id)
            .collect();
        assert_eq!(
            faded_cards.len(),
            1,
            "exactly one unstudied faded step is introduced"
        );
        assert_eq!(
            faded_cards[0].template_idx, 0,
            "forward order introduces the lowest ordinal first"
        );

        // backward order introduces the highest ordinal first
        col.update_default_deck_config(|config| {
            config.fade_order = crate::deckconfig::FadeOrder::Backward as i32;
        });
        let queue = col.build_queues(DeckId(1)).unwrap();
        let faded_ords: Vec<u16> = queue
            .iter()
            .map(|entry| col.storage.get_card(entry.card_id()).unwrap().unwrap())
            .filter(|card| card.note_id == faded.id)
            .map(|card| card.template_idx)
            .collect();
        assert_eq!(faded_ords, vec![1]);
    }

    /// [R16]/C9: the self-explanation flag is a real template toggle - a
    /// solve note with a plain + self-explain sibling serves exactly one,
    /// picked by the flag.
    #[test]
    fn self_explain_flag_picks_solve_template() {
        let mut col = Collection::new();
        // a two-template solve note: ord 0 = plain, ord 1 = self-explain
        let nt = col.basic_rev_notetype();
        let mut solve = nt.new_note();
        solve.set_field(0, "solve front").unwrap();
        solve.set_field(1, "solve back").unwrap();
        solve.tags = tags_for("solve");
        col.add_note(&mut solve, DeckId(1)).unwrap();
        col.enable_fade();
        col.set_exam_date_days_ahead(60);

        let solve_ords = |col: &mut Collection| -> Vec<u16> {
            col.build_queues(DeckId(1))
                .unwrap()
                .iter()
                .map(|entry| col.storage.get_card(entry.card_id()).unwrap().unwrap())
                .filter(|card| card.note_id == solve.id)
                .map(|card| card.template_idx)
                .collect()
        };

        assert_eq!(solve_ords(&mut col), vec![0], "flag off: plain variant");

        col.update_default_deck_config(|config| config.self_explain_enabled = true);
        assert_eq!(
            solve_ords(&mut col),
            vec![1],
            "flag on: self-explain variant"
        );
    }

    /// The exam-date key: unset and malformed dates disable the horizon;
    /// past dates clamp to zero days.
    #[test]
    fn exam_date_parsing() {
        let mut col = Collection::new();
        let timing = col.timing_today().unwrap();
        assert_eq!(days_until_exam(&mut col, timing).unwrap(), None);

        col.set_config_json(EXAM_DATE_CONFIG_KEY, &"not a date", false)
            .unwrap();
        assert_eq!(days_until_exam(&mut col, timing).unwrap(), None);

        col.set_config_json(EXAM_DATE_CONFIG_KEY, &"2000-01-01", false)
            .unwrap();
        assert_eq!(days_until_exam(&mut col, timing).unwrap(), Some(0));

        col.set_exam_date_days_ahead(45);
        assert_eq!(days_until_exam(&mut col, timing).unwrap(), Some(45));
    }

    /// Phase 3 M0 — the unified graph pass: rung dependencies GATE first,
    /// then the interference edges order the SURVIVORS by cluster, all in
    /// one `build_queues` call. A fresh ladder's locked solve rung is
    /// withheld bury-style while the surviving worked cards of the same
    /// cluster still form a contrast-adjacent run (and precedence means the
    /// gated card never occupies a contrast slot).
    #[test]
    fn gate_first_then_cluster_survivors_in_one_pass() {
        let mut col = Collection::new();
        col.enable_fade();
        col.update_default_deck_config(|config| {
            config.contrast_scheduling = true;
            config.contrast_tag_prefix = String::new();
            // legacy ungated arm: adjacency machinery under test, not R18
            config.contrast_confusable_tag = String::new();
        });
        col.set_exam_date_days_ahead(30);

        // ladder with one correct worked session: comprehension precondition
        // (R13) clears, but the 3-session promotion gate still locks solve
        let worked_a = col.add_rung_note("worked a", "worked", false);
        // ungated background noise between the cluster members
        for i in 0..3 {
            NoteAdder::basic(&mut col)
                .fields(&[&format!("noise {i}"), "back"])
                .add(&mut col);
        }
        let worked_b = col.add_rung_note("worked b", "worked", false);
        let solve = col.add_rung_note("solve locked", "solve", false);
        col.log_review(&worked_a, 3, 3);

        let queue = col.queue_note_ids();
        // gate first: the locked rung is absent…
        assert!(
            !queue.contains(&solve.id),
            "locked solve rung must be withheld: {queue:?}"
        );
        // …then cluster: both surviving worked cards adjoin
        let position = |id: NoteId| queue.iter().position(|n| *n == id).unwrap();
        assert_eq!(
            position(worked_a.id).abs_diff(position(worked_b.id)),
            1,
            "surviving cluster members must adjoin: {queue:?}"
        );
        // and nothing else was dropped: 2 rung survivors + 3 noise notes
        assert_eq!(queue.len(), 5);
    }
}
