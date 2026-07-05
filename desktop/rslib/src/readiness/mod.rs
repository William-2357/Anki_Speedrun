// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! The banded, two-number, abstaining Readiness estimate (Anki Speedrun,
//! Phase 3 M1).
//!
//! Answers "what is the probability you would pass CFA Level I today?"
//! with a BAND, never a point ([R2]), plus a SECOND honest number — the
//! confidence of the pass/fail call ([R5]) — and it ABSTAINS loudly when
//! the evidence is thin ([R1]). The abstention gate is enforced here, in
//! the backend, so no display layer can bypass it; the labelled test mode
//! is an explicit request flag whose output is marked test data.
//!
//! Method of record ([R2], C5/C6-corrected):
//! * Outcomes are DELAYED held-out probe answers (x correct of n) from the
//!   `probe::pool::performance` pool — real application-MCQ results, never FSRS
//!   recall (deriving outcomes from the model under test would be fabrication).
//! * Posterior over the true probe-success rate p: Beta(x+0.5, n−x+0.5)
//!   (Jeffreys prior; Brown, Cai & DasGupta 2001 recommend the matching
//!   interval for n ≤ 40).
//! * P(pass) maps p through a Binomial(180, p) exam-score model against the
//!   minimum passing standard, which CFA never publishes: it is carried as a
//!   configurable mock-proxy band (default [0.68, 0.75], `speedrun:passBand`).
//!   - centre  = P(BetaBin(180, a, b) ≥ ceil(180·MPS_mid)) — the posterior
//!     predictive, the point of record INSIDE the band;
//!   - low     = P(Bin(180, q05) ≥ ceil(180·MPS_high)) (pessimistic corner);
//!   - high    = P(Bin(180, q95) ≥ ceil(180·MPS_low)) (optimistic corner).
//! * [R25] honesty caps: the band is floored to a minimum half-width and
//!   clamped into [0.02, 0.98]; call confidence is capped at 0.85 — mocks
//!   predict the real exam only moderately (r ≈ 0.7, Castro 2025), so no amount
//!   of probe data may read as near-certainty.
//! * Descoped to future work (GRILLING C6): IRT/PFA/LKT, Rudner CA/CC,
//!   Venn-Abers, conformal backstops. The shipped backstop is the gate + width
//!   floor; the shipped second number is the Beta-posterior classification
//!   confidence.
//!
//! The give-up rule ([R1], written down): a band is emitted only when ALL
//! hold — graded study reviews ≥ 300, weighted topic coverage ≥ 70%,
//! delayed held-out probe outcomes ≥ 50, and band half-width ≤ 0.20.
//! Otherwise the response names exactly which inputs are missing, while
//! still rendering the full honesty contract (evidence, calibration
//! history, best next topic).

mod beta;
pub(crate) mod blueprint;
mod probes;

use std::collections::HashMap;

use anki_proto::stats::get_readiness_response::Calibration;
use anki_proto::stats::get_readiness_response::Evidence;
use anki_proto::stats::get_readiness_response::Kind;
use anki_proto::stats::GetReadinessResponse;
use serde::Deserialize;
use serde::Serialize;

pub use self::probes::MIN_PROBE_DELAY_DAYS;
pub use self::probes::PROBE_HELD_OUT_TAG;
pub use self::probes::PROBE_POOL_CALIBRATION_TAG;
pub use self::probes::PROBE_POOL_PERFORMANCE_TAG;
use crate::prelude::*;

/// [R1] gate thresholds, coordinated with the dashboard's written give-up
/// rule.
pub const MIN_GRADED_REVIEWS: u64 = 300;
pub const MIN_COVERAGE: f32 = 0.7;
pub const MIN_DELAYED_PROBES: u32 = 50;
pub const MAX_HALF_WIDTH: f32 = 0.20;
/// [R25] the band never collapses below this half-width (irreducible
/// residual error)…
pub const MIN_HALF_WIDTH: f32 = 0.10;
/// …never leaves [0.02, 0.98]…
pub const BAND_FLOOR: f32 = 0.02;
pub const BAND_CEIL: f32 = 0.98;
/// …and the call confidence never exceeds this (mock↔exam r ≈ 0.7 ceiling,
/// documented heuristic: (1 + r) / 2).
pub const CONFIDENCE_CAP: f32 = 0.85;
/// Default mock-proxy pass band ([R25]; 300Hours revised the L1 target to
/// ~68% in Nov 2025). Configurable via `speedrun:passBand`.
pub const DEFAULT_MPS_LOW: f32 = 0.68;
pub const DEFAULT_MPS_HIGH: f32 = 0.75;
/// Per-topic recall target used for gap ranking (best-next-topic here, and
/// the M2 readiness-allocation pass); same value the dashboard table uses.
pub(crate) const PERFORMANCE_TARGET: f32 = 0.8;

pub const PASS_BAND_CONFIG_KEY: &str = "speedrun:passBand";
pub const CALIBRATION_CONFIG_KEY: &str = "speedrun:readinessCalibration";

/// Optional user override for the MPS band ({"low": .., "high": ..}).
#[derive(Debug, Serialize, Deserialize)]
pub struct PassBandConfig {
    pub low: f32,
    pub high: f32,
}

/// Written by the offline M3 harness (`probe_harness.py --apply`); the RPC
/// only surfaces it as the honesty contract's calibration history.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalibrationRecord {
    pub fitted_at: String,
    pub brier: f32,
    pub log_loss: f32,
    pub n: u32,
    pub temperature: f32,
}

pub(crate) struct TopicEvidence {
    pub(crate) studied_cards: u32,
    pub(crate) mean_retrievability: f32,
}

/// Fold the mastery response's raw topic buckets (which may use aliases or
/// user-map values) onto the canonical blueprint ids, studied-count
/// weighted. Shared by the gauge and the M2 allocation pass so both agree
/// with the dashboard's attribution.
pub(crate) fn fold_mastery_topics(
    mastery: &anki_proto::stats::TopicMasteryResponse,
) -> HashMap<&'static str, TopicEvidence> {
    let mut by_topic: HashMap<&'static str, TopicEvidence> = HashMap::new();
    for topic in &mastery.topics {
        let Some(id) = blueprint::canonical_topic_id(&topic.topic) else {
            continue;
        };
        let entry = by_topic.entry(id).or_insert(TopicEvidence {
            studied_cards: 0,
            mean_retrievability: 0.0,
        });
        let total_studied = entry.studied_cards + topic.studied_cards;
        if total_studied > 0 {
            entry.mean_retrievability = (entry.mean_retrievability * entry.studied_cards as f32
                + topic.average_retrievability * topic.studied_cards as f32)
                / total_studied as f32;
        }
        entry.studied_cards = total_studied;
    }
    by_topic
}

impl Collection {
    pub fn get_readiness(
        &mut self,
        test_mode: bool,
        tag_topic_map: &HashMap<String, String>,
    ) -> Result<GetReadinessResponse> {
        let mastery = self.topic_mastery("", "", 0.0, tag_topic_map)?;
        let probes = self.collect_probe_outcomes()?;
        let (mps_low, mps_high) = self.pass_band();
        let calibration = self.calibration_record();

        // fold mastery buckets (which may use aliases) onto the blueprint
        let by_topic = fold_mastery_topics(&mastery);
        let total_weight = blueprint::total_midpoint_weight();
        let studied_weight: f32 = blueprint::TOPICS
            .iter()
            .filter(|t| {
                by_topic
                    .get(t.id)
                    .map(|e| e.studied_cards > 0)
                    .unwrap_or(false)
            })
            .map(|t| t.midpoint)
            .sum();
        let coverage = studied_weight / total_weight;
        let topics_studied = blueprint::TOPICS
            .iter()
            .filter(|t| {
                by_topic
                    .get(t.id)
                    .map(|e| e.studied_cards > 0)
                    .unwrap_or(false)
            })
            .count() as u32;

        // graded STUDY reviews: probe answers are measurement, not study
        let graded_reviews = mastery
            .graded_reviews
            .saturating_sub(probes.probe_graded_reviews);

        // ---- the posterior and the MPS map ----
        let x = probes.correct;
        let n = probes.delayed;
        let a = x as f64 + 0.5;
        let b = (n - x) as f64 + 0.5;
        let questions = blueprint::EXAM_QUESTIONS;
        let k_low = (questions as f64 * mps_low as f64).ceil() as u32;
        let k_mid = (questions as f64 * ((mps_low + mps_high) / 2.0) as f64).ceil() as u32;
        let k_high = (questions as f64 * mps_high as f64).ceil() as u32;
        let q05 = beta::beta_quantile(0.05, a, b);
        let q95 = beta::beta_quantile(0.95, a, b);
        let center = beta::beta_binomial_tail(questions, k_mid, a, b) as f32;
        let mut low = beta::binomial_tail(questions, k_high, q05) as f32;
        let mut high = beta::binomial_tail(questions, k_low, q95) as f32;
        // [R25] floor the half-width, then clamp into the honesty band; the
        // ceiling may re-narrow it — that is the certainty cap working, not
        // false precision
        if (high - low) / 2.0 < MIN_HALF_WIDTH {
            low = center - MIN_HALF_WIDTH;
            high = center + MIN_HALF_WIDTH;
        }
        low = low.clamp(BAND_FLOOR, BAND_CEIL);
        high = high.clamp(BAND_FLOOR, BAND_CEIL);
        if low > high {
            std::mem::swap(&mut low, &mut high);
        }
        let half_width = (high - low) / 2.0;

        // ---- the second number: confidence of the pass/fail call ----
        let call_straddles = low < 0.5 && high > 0.5;
        let (call, call_confidence) = if call_straddles {
            (String::new(), 0.0)
        } else {
            let confidence = center.max(1.0 - center).min(CONFIDENCE_CAP);
            let call = if center >= 0.5 { "pass" } else { "fail" };
            (call.into(), confidence)
        };

        // ---- the give-up rule ----
        let mut missing = Vec::new();
        if !mastery.fsrs_enabled {
            missing.push(
                "FSRS is disabled, so no topic has study evidence. Enable FSRS in deck options; no proxy is used in its place."
                    .into(),
            );
        }
        if graded_reviews < MIN_GRADED_REVIEWS {
            missing.push(format!(
                "Only {graded_reviews} graded study reviews; need at least {MIN_GRADED_REVIEWS}.",
            ));
        }
        if coverage < MIN_COVERAGE {
            let mut unstudied: Vec<&str> = blueprint::TOPICS
                .iter()
                .filter(|t| {
                    by_topic
                        .get(t.id)
                        .map(|e| e.studied_cards == 0)
                        .unwrap_or(true)
                })
                .map(|t| t.name)
                .collect();
            unstudied.truncate(3);
            missing.push(format!(
                "Topic coverage is {}%; need at least {}%.{}",
                (coverage * 100.0).round(),
                (MIN_COVERAGE * 100.0).round(),
                if unstudied.is_empty() {
                    String::new()
                } else {
                    format!(" Not studied yet: {}, \u{2026}.", unstudied.join(", "))
                }
            ));
        }
        if n < MIN_DELAYED_PROBES {
            let mut line = format!(
                "Only {n} delayed held-out probe outcomes; need at least {MIN_DELAYED_PROBES}.",
            );
            if probes.probe_cards == 0 {
                line.push_str(" The probe bank is not imported (tools/speedrun/build_probe_deck.py builds it).");
            } else if probes.undelayed > 0 {
                line.push_str(&format!(
                    " {} more were answered too soon after study and are excluded (\u{2265}{}-day rule).",
                    probes.undelayed, MIN_PROBE_DELAY_DAYS as u32
                ));
            }
            missing.push(line);
        }
        if half_width > MAX_HALF_WIDTH {
            missing.push(format!(
                "The probability band is too wide to be useful (half-width {half_width:.2} > {MAX_HALF_WIDTH}). Near the pass boundary this may never clear: the unpublished MPS is irreducible uncertainty.",
            ));
        }

        let evidence = Evidence {
            probe_correct: x,
            probe_answered_delayed: n,
            probe_answered_undelayed: probes.undelayed,
            probe_unanswered: probes.unanswered,
            graded_reviews,
            coverage,
            topics_studied,
            topics_total: blueprint::TOPICS.len() as u32,
            mean_probe_lag_days: probes.mean_lag_days,
            probe_never_studied: probes.never_studied,
            calibration_outcomes: probes.calibration_answered,
            fsrs_enabled: mastery.fsrs_enabled,
        };

        let best_next_topic = best_next_topic(&by_topic, call_straddles);

        let abstaining = !missing.is_empty() && !test_mode;
        let kind = if abstaining {
            Kind::Abstain
        } else if test_mode {
            Kind::Test
        } else {
            Kind::Value
        };

        let mut reasons = Vec::new();
        if !abstaining {
            reasons.push(
                "CFA Level I is pass/fail, so this is a pass-probability band, not an invented score."
                    .into(),
            );
            reasons.push(format!(
                "Method: Beta-Binomial with Jeffreys prior Beta(0.5, 0.5) over {x} correct of {n} delayed held-out probe outcomes, propagated through a Binomial({questions}) score model at the MPS band [{mps_low:.2}, {mps_high:.2}] (CFA never publishes the MPS).",
            ));
            reasons.push(format!(
                "Band: Jeffreys 90% interval at the pessimistic/optimistic MPS corners; half-width floored at {MIN_HALF_WIDTH} and clamped into [{BAND_FLOOR}, {BAND_CEIL}] \u{2014} mocks predict the exam only moderately (r\u{2248}0.7), so certainty is capped.",
            ));
            reasons.push(format!(
                "Coverage weights: CFA {} blueprint midpoints, fixed priors (never fitted).",
                blueprint::EXAM_YEAR
            ));
            if call_straddles {
                reasons.push(
                    "Call: abstaining \u{2014} the band straddles 50% (too close to call).".into(),
                );
            } else {
                reasons.push(format!(
                    "Call: {call} (confidence {call_confidence:.2}, capped at {CONFIDENCE_CAP})."
                ));
            }
            match &calibration {
                Some(record) => reasons.push(format!(
                    "Calibration: last fitted {} on {} calibration-pool outcomes (Brier {:.3}, log-loss {:.3}).",
                    record.fitted_at, record.n, record.brier, record.log_loss
                )),
                None => reasons.push(
                    "Calibration: never run \u{2014} the offline harness has not scored the calibration pool yet."
                        .into(),
                ),
            }
            if test_mode {
                reasons.push(
                    "TEST MODE: the give-up gates were relaxed for pipeline testing; this output is test data, not a prediction."
                        .into(),
                );
            }
        }

        Ok(GetReadinessResponse {
            kind: kind as i32,
            p_pass_low: if abstaining { 0.0 } else { low },
            p_pass_high: if abstaining { 0.0 } else { high },
            p_pass_center: if abstaining { 0.0 } else { center },
            call_confidence: if abstaining { 0.0 } else { call_confidence },
            call: if abstaining { String::new() } else { call },
            evidence: Some(evidence),
            missing,
            reasons,
            calibration: calibration.map(|record| Calibration {
                fitted_at: record.fitted_at,
                brier: record.brier,
                log_loss: record.log_loss,
                n: record.n,
                temperature: record.temperature,
            }),
            best_next_topic,
            mps_low,
            mps_high,
            min_graded_reviews: MIN_GRADED_REVIEWS as u32,
            min_coverage: MIN_COVERAGE,
            min_delayed_probes: MIN_DELAYED_PROBES,
            max_half_width: MAX_HALF_WIDTH,
            min_half_width: MIN_HALF_WIDTH,
            confidence_cap: CONFIDENCE_CAP,
        })
    }

    /// The configurable mock-proxy pass band, defaulting to [0.68, 0.75];
    /// invalid overrides fall back to the default rather than guessing.
    fn pass_band(&self) -> (f32, f32) {
        if let Some(band) = self.get_config_optional::<PassBandConfig, _>(PASS_BAND_CONFIG_KEY) {
            if band.low > 0.0 && band.low < band.high && band.high < 1.0 {
                return (band.low, band.high);
            }
        }
        (DEFAULT_MPS_LOW, DEFAULT_MPS_HIGH)
    }

    fn calibration_record(&self) -> Option<CalibrationRecord> {
        self.get_config_optional::<CalibrationRecord, _>(CALIBRATION_CONFIG_KEY)
    }

    /// The dashboard needs to know FSRS state for the mastery response;
    /// mirrored here for tests.
    #[cfg(test)]
    fn enable_fsrs(&mut self) {
        self.set_config(crate::config::BoolKey::Fsrs, &true)
            .unwrap();
    }
}

/// The single best next topic: largest `weight × (target − mean recall)`
/// gap, with unstudied topics counting a zero mean (so big unstudied
/// topics lead). Ethics tie-break near the boundary (T8): when the call is
/// too close to call and the Ethics gap is within 10% of the best gap,
/// Ethics wins — it has the largest blueprint weight AND CFA applies an
/// ethics adjustment for candidates near the MPS.
fn best_next_topic(by_topic: &HashMap<&'static str, TopicEvidence>, near_boundary: bool) -> String {
    let gap = |id: &str| -> f32 {
        let topic = blueprint::topic(id).unwrap();
        let mean = by_topic
            .get(id)
            .map(|e| {
                if e.studied_cards > 0 {
                    e.mean_retrievability
                } else {
                    0.0
                }
            })
            .unwrap_or(0.0);
        (topic.midpoint / blueprint::total_midpoint_weight()) * (PERFORMANCE_TARGET - mean).max(0.0)
    };
    let best = blueprint::TOPICS
        .iter()
        .max_by(|a, b| gap(a.id).total_cmp(&gap(b.id)))
        .unwrap();
    if near_boundary && best.id != "ethics" {
        let ethics_gap = gap("ethics");
        if ethics_gap > 0.0 && ethics_gap >= 0.9 * gap(best.id) {
            return blueprint::topic("ethics").unwrap().name.into();
        }
    }
    if gap(best.id) <= 0.0 {
        // everything at/above target: no advice is better than fake advice
        return String::new();
    }
    best.name.into()
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::card::FsrsMemoryState;
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

    fn make_studied(col: &mut Collection, note: &Note) {
        let cid = col.storage.card_ids_of_notes(&[note.id]).unwrap()[0];
        col.get_and_update_card(cid, |card| {
            card.memory_state = Some(FsrsMemoryState {
                stability: 100.0,
                difficulty: 5.0,
            });
            card.last_review_time = Some(TimestampSecs::now());
            Ok(())
        })
        .unwrap();
    }

    fn log_graded_review(col: &mut Collection, cid: CardId, days_ago: i64, button: u8) {
        let now = TimestampMillis::now().0;
        let entry = RevlogEntry {
            id: RevlogId(now - days_ago * 86_400_000),
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
    fn abstains_by_default_and_names_every_missing_input() -> Result<()> {
        let mut col = Collection::new();
        add_note(&mut col, "q", &["cfa::topic::ethics"]);
        let response = col.get_readiness(false, &HashMap::new())?;

        assert_eq!(response.kind, Kind::Abstain as i32);
        // never a number while abstaining
        assert_eq!(response.p_pass_low, 0.0);
        assert_eq!(response.p_pass_high, 0.0);
        assert_eq!(response.p_pass_center, 0.0);
        assert_eq!(response.call, "");
        assert_eq!(response.call_confidence, 0.0);
        // fsrs + reviews + coverage + probes + width all fail
        assert!(response.missing.len() >= 4, "{:?}", response.missing);
        // the honesty contract still renders
        let evidence = response.evidence.unwrap();
        assert_eq!(evidence.topics_total, 10);
        assert_eq!(response.best_next_topic, "Ethical & Professional Standards");
        assert_eq!(response.mps_low, DEFAULT_MPS_LOW);
        assert_eq!(response.mps_high, DEFAULT_MPS_HIGH);
        assert!(response.calibration.is_none());
        Ok(())
    }

    #[test]
    fn test_mode_emits_labelled_wide_band_and_keeps_missing_list() -> Result<()> {
        let mut col = Collection::new();
        let response = col.get_readiness(true, &HashMap::new())?;

        assert_eq!(response.kind, Kind::Test as i32);
        // prior-only posterior: the band is honestly wide open
        assert!(response.p_pass_high - response.p_pass_low > 0.5);
        assert!(response.p_pass_low >= BAND_FLOOR);
        assert!(response.p_pass_high <= BAND_CEIL);
        // straddles 50% → the call abstains even in test mode
        assert_eq!(response.call, "");
        assert_eq!(response.call_confidence, 0.0);
        assert!(!response.missing.is_empty());
        assert!(response
            .reasons
            .iter()
            .any(|reason| reason.contains("TEST MODE")));
        Ok(())
    }

    #[test]
    fn emits_value_band_when_every_gate_passes() -> Result<()> {
        let mut col = Collection::new();
        col.enable_fsrs();
        // coverage: studied cards on 6 topics worth 73.5/102.5 of the exam
        for topic in [
            "ethics",
            "financial_statement_analysis",
            "equity_investments",
            "fixed_income",
            "portfolio_management",
            "alternative_investments",
        ] {
            let note = add_note(&mut col, topic, &[&format!("cfa::topic::{topic}")]);
            make_studied(&mut col, &note);
        }
        // 300 graded study reviews on one card
        let study = add_note(&mut col, "grind", &["cfa::topic::ethics"]);
        let study_cid = col.storage.card_ids_of_notes(&[study.id])?[0];
        for i in 0..300 {
            log_graded_review(&mut col, study_cid, 30 + (i % 10), 3);
        }
        // 60 delayed probe outcomes, 55 correct (never-studied clusters
        // count as delayed)
        for i in 0..60 {
            let probe = add_note(
                &mut col,
                &format!("probe{i}"),
                &[
                    "probe::held_out",
                    "probe::pool::performance",
                    &format!("cluster::probeonly::c{i}"),
                    "cfa::topic::ethics",
                ],
            );
            let cid = col.storage.card_ids_of_notes(&[probe.id])?[0];
            log_graded_review(&mut col, cid, 1, if i < 55 { 3 } else { 1 });
        }

        let response = col.get_readiness(false, &HashMap::new())?;
        assert_eq!(response.kind, Kind::Value as i32, "{:?}", response.missing);
        assert!(response.p_pass_low > 0.5);
        assert!(response.p_pass_high <= BAND_CEIL);
        assert!(response.p_pass_center > response.p_pass_low);
        assert_eq!(response.call, "pass");
        // capped by the mock↔exam ceiling, never higher
        assert!(response.call_confidence <= CONFIDENCE_CAP);
        assert!(response.call_confidence > 0.5);
        assert!(response.missing.is_empty());
        let evidence = response.evidence.unwrap();
        assert_eq!(evidence.probe_answered_delayed, 60);
        assert_eq!(evidence.probe_correct, 55);
        // probe answers do not count as study reviews
        assert_eq!(evidence.graded_reviews, 300);
        Ok(())
    }

    #[test]
    fn near_the_cut_the_width_gate_abstains_even_with_rich_data() -> Result<()> {
        let mut col = Collection::new();
        col.enable_fsrs();
        for topic in [
            "ethics",
            "financial_statement_analysis",
            "equity_investments",
            "fixed_income",
            "portfolio_management",
            "alternative_investments",
        ] {
            let note = add_note(&mut col, topic, &[&format!("cfa::topic::{topic}")]);
            make_studied(&mut col, &note);
        }
        let study = add_note(&mut col, "grind", &["cfa::topic::ethics"]);
        let study_cid = col.storage.card_ids_of_notes(&[study.id])?[0];
        for i in 0..300 {
            log_graded_review(&mut col, study_cid, 30 + (i % 10), 3);
        }
        // 60 probes at ~72% accuracy: dead inside the MPS band
        for i in 0..60 {
            let probe = add_note(
                &mut col,
                &format!("probe{i}"),
                &[
                    "probe::held_out",
                    "probe::pool::performance",
                    &format!("cluster::probeonly::c{i}"),
                ],
            );
            let cid = col.storage.card_ids_of_notes(&[probe.id])?[0];
            log_graded_review(&mut col, cid, 1, if i < 43 { 3 } else { 1 });
        }

        let response = col.get_readiness(false, &HashMap::new())?;
        // near the boundary the honest answer stays "abstain": the
        // unpublished MPS dominates
        assert_eq!(response.kind, Kind::Abstain as i32);
        assert!(response
            .missing
            .iter()
            .any(|line| line.contains("too wide")));
        Ok(())
    }

    #[test]
    fn pass_band_is_configurable_and_calibration_surfaces() -> Result<()> {
        let mut col = Collection::new();
        col.set_config_json(
            PASS_BAND_CONFIG_KEY,
            &PassBandConfig {
                low: 0.60,
                high: 0.70,
            },
            false,
        )?;
        col.set_config_json(
            CALIBRATION_CONFIG_KEY,
            &CalibrationRecord {
                fitted_at: "2026-07-04".into(),
                brier: 0.18,
                log_loss: 0.52,
                n: 30,
                temperature: 1.2,
            },
            false,
        )?;
        let response = col.get_readiness(false, &HashMap::new())?;
        assert_eq!(response.mps_low, 0.60);
        assert_eq!(response.mps_high, 0.70);
        let calibration = response.calibration.unwrap();
        assert_eq!(calibration.fitted_at, "2026-07-04");
        assert_eq!(calibration.n, 30);

        // nonsense override falls back to the default
        col.set_config_json(
            PASS_BAND_CONFIG_KEY,
            &PassBandConfig {
                low: 0.9,
                high: 0.2,
            },
            false,
        )?;
        let response = col.get_readiness(false, &HashMap::new())?;
        assert_eq!(response.mps_low, DEFAULT_MPS_LOW);
        Ok(())
    }

    #[test]
    fn ethics_tie_break_applies_near_the_boundary() {
        // fixed income slightly behind ethics in raw gap terms, call too
        // close: ethics wins the tie-break
        let mut by_topic = HashMap::new();
        by_topic.insert(
            "ethics",
            TopicEvidence {
                studied_cards: 10,
                mean_retrievability: 0.25,
            },
        );
        by_topic.insert(
            "fixed_income",
            TopicEvidence {
                studied_cards: 10,
                mean_retrievability: 0.05,
            },
        );
        // give every other topic full recall so they never lead
        for topic in blueprint::TOPICS {
            if topic.id != "ethics" && topic.id != "fixed_income" {
                by_topic.insert(
                    topic.id,
                    TopicEvidence {
                        studied_cards: 1,
                        mean_retrievability: 1.0,
                    },
                );
            }
        }
        // gaps: fi = (12.5/102.5)·0.75 ≈ 0.0915; ethics = (17.5/102.5)·0.55 ≈ 0.0939
        // ethics already leads here, so shift it: make ethics second
        by_topic.get_mut("ethics").unwrap().mean_retrievability = 0.30;
        // now ethics ≈ (17.5/102.5)·0.50 ≈ 0.0854 < fi ≈ 0.0915, within 10%
        assert_eq!(
            best_next_topic(&by_topic, false),
            "Fixed Income",
            "without the boundary flag the raw gap wins"
        );
        assert_eq!(
            best_next_topic(&by_topic, true),
            "Ethical & Professional Standards",
            "near the boundary ethics takes the tie-break"
        );
    }
}
