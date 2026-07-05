// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! The CFA Level I exam blueprint, as FIXED priors ([R4]/[R25]).
//!
//! The published topic-weight ranges (2025–2026 curriculum) are data, not
//! fitted coefficients: allocation and coverage budget by the midpoint and
//! carry the (min, max) range as uncertainty. This mirrors
//! `ts/routes/dashboard/cfa_weights_2026.json` — the two must be kept in
//! sync (versioned by exam year; add a new table for a new year rather than
//! editing in place).
//!
//! Everything else in the engine stays exam-agnostic; this module is the
//! single deliberately CFA-specific corner of `rslib`, because the Phase 3
//! plan places blueprint priors in the backend where the gate lives.

pub(crate) struct BlueprintTopic {
    /// canonical tag suffix under `cfa::topic::`
    pub id: &'static str,
    pub name: &'static str,
    /// Published range bounds: versioned data carried alongside the
    /// midpoint so the range can be disclosed as uncertainty (budgeting
    /// itself always uses the midpoint).
    #[allow(dead_code)]
    pub min: f32,
    #[allow(dead_code)]
    pub max: f32,
    pub midpoint: f32,
}

pub(crate) const EXAM_YEAR: u32 = 2026;
/// The exam has 180 standalone MCQs; the MPS map propagates the posterior
/// through a Binomial(180, p) score model.
pub(crate) const EXAM_QUESTIONS: u32 = 180;

pub(crate) const TOPICS: &[BlueprintTopic] = &[
    BlueprintTopic {
        id: "ethics",
        name: "Ethical & Professional Standards",
        min: 15.0,
        max: 20.0,
        midpoint: 17.5,
    },
    BlueprintTopic {
        id: "quantitative_methods",
        name: "Quantitative Methods",
        min: 6.0,
        max: 9.0,
        midpoint: 7.5,
    },
    BlueprintTopic {
        id: "economics",
        name: "Economics",
        min: 6.0,
        max: 9.0,
        midpoint: 7.5,
    },
    BlueprintTopic {
        id: "financial_statement_analysis",
        name: "Financial Statement Analysis",
        min: 11.0,
        max: 14.0,
        midpoint: 12.5,
    },
    BlueprintTopic {
        id: "corporate_issuers",
        name: "Corporate Issuers",
        min: 6.0,
        max: 9.0,
        midpoint: 7.5,
    },
    BlueprintTopic {
        id: "equity_investments",
        name: "Equity Investments",
        min: 11.0,
        max: 14.0,
        midpoint: 12.5,
    },
    BlueprintTopic {
        id: "fixed_income",
        name: "Fixed Income",
        min: 11.0,
        max: 14.0,
        midpoint: 12.5,
    },
    BlueprintTopic {
        id: "derivatives",
        name: "Derivatives",
        min: 5.0,
        max: 8.0,
        midpoint: 6.5,
    },
    BlueprintTopic {
        id: "alternative_investments",
        name: "Alternative Investments",
        min: 7.0,
        max: 10.0,
        midpoint: 8.5,
    },
    BlueprintTopic {
        id: "portfolio_management",
        name: "Portfolio Management",
        min: 8.0,
        max: 12.0,
        midpoint: 10.0,
    },
];

/// Shorthand tag suffixes folded onto the canonical ids — the same alias
/// table the dashboard uses (`ts/routes/dashboard/topics.ts`), so the
/// backend coverage gate and the frontend table agree on attribution.
const ALIASES: &[(&str, &str)] = &[
    ("quant", "quantitative_methods"),
    ("quantitative", "quantitative_methods"),
    ("econ", "economics"),
    ("fsa", "financial_statement_analysis"),
    ("financial_reporting", "financial_statement_analysis"),
    ("corporate", "corporate_issuers"),
    ("corporate_finance", "corporate_issuers"),
    ("equity", "equity_investments"),
    ("fi", "fixed_income"),
    ("fixed", "fixed_income"),
    ("deriv", "derivatives"),
    ("alt", "alternative_investments"),
    ("alternatives", "alternative_investments"),
    ("alternative", "alternative_investments"),
    ("pm", "portfolio_management"),
    ("portfolio", "portfolio_management"),
];

pub(crate) fn total_midpoint_weight() -> f32 {
    TOPICS.iter().map(|t| t.midpoint).sum()
}

/// Map a raw topic-tag suffix (already lowercased) onto a canonical
/// blueprint id, if known.
pub(crate) fn canonical_topic_id(raw: &str) -> Option<&'static str> {
    if let Some(topic) = TOPICS.iter().find(|t| t.id == raw) {
        return Some(topic.id);
    }
    ALIASES
        .iter()
        .find(|(alias, _)| *alias == raw)
        .map(|(_, id)| *id)
}

pub(crate) fn topic(id: &str) -> Option<&'static BlueprintTopic> {
    TOPICS.iter().find(|t| t.id == id)
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn midpoints_sum_and_aliases_fold() {
        assert!((total_midpoint_weight() - 102.5).abs() < 1e-4);
        assert_eq!(canonical_topic_id("fi"), Some("fixed_income"));
        assert_eq!(canonical_topic_id("ethics"), Some("ethics"));
        assert_eq!(canonical_topic_id("unknown"), None);
        assert_eq!(TOPICS.len(), 10);
    }
}
