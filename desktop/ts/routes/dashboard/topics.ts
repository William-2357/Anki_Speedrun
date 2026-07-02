// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * The 10 official CFA Level I topic areas (Anki Speedrun).
 *
 * The engine is exam-agnostic: the TopicMastery RPC just aggregates by tag
 * suffix. Everything CFA-specific - the official outline, the exam weight
 * ranges, tag aliases, and the (documented, uncalibrated) transfer factors -
 * lives here in the frontend, as data.
 */

import weightsJson from "./cfa_weights_2026.json";

export interface TopicWeight {
    name: string;
    /** published percentage range for the exam year */
    min: number;
    max: number;
    midpoint: number;
}

export interface TopicInfo extends TopicWeight {
    /** canonical tag suffix under `cfa::topic::` */
    id: string;
    /**
     * Documented Phase 1 guess for how much recall transfers to exam-style
     * questions (recall-heavy topics high, computation-heavy topics lower).
     * Uncalibrated by construction; Phase 2's measured Memory->Performance
     * gap replaces it. Never displayed without an "uncalibrated" label.
     */
    transfer: number;
}

/** τ(s): documented guesses, see DASHBOARD plan. */
const TRANSFER: Record<string, number> = {
    ethics: 0.9,
    quantitative_methods: 0.65,
    economics: 0.75,
    financial_statement_analysis: 0.75,
    corporate_issuers: 0.75,
    equity_investments: 0.75,
    fixed_income: 0.65,
    derivatives: 0.6,
    alternative_investments: 0.85,
    portfolio_management: 0.75,
};

/** Common shorthand tag suffixes mapped onto the canonical topic ids. */
export const TOPIC_ALIASES: Record<string, string> = {
    quant: "quantitative_methods",
    quantitative: "quantitative_methods",
    econ: "economics",
    fsa: "financial_statement_analysis",
    financial_reporting: "financial_statement_analysis",
    corporate: "corporate_issuers",
    corporate_finance: "corporate_issuers",
    equity: "equity_investments",
    fi: "fixed_income",
    fixed: "fixed_income",
    deriv: "derivatives",
    alt: "alternative_investments",
    alternatives: "alternative_investments",
    alternative: "alternative_investments",
    pm: "portfolio_management",
    portfolio: "portfolio_management",
};

export const EXAM_NAME = "CFA Level I";
export const EXAM_YEAR: number = weightsJson.examYear;
export const WEIGHTS_SOURCE: string = weightsJson.source;

export const TOPICS: TopicInfo[] = Object.entries(
    weightsJson.topics as Record<string, TopicWeight>,
).map(([id, weight]) => ({
    id,
    ...weight,
    transfer: TRANSFER[id] ?? 0.75,
}));

const CANONICAL_IDS = new Set(TOPICS.map((topic) => topic.id));

/** Map a raw `cfa::topic::` tag suffix onto a canonical topic id, if known. */
export function canonicalTopicId(rawSuffix: string): string | null {
    const suffix = rawSuffix.toLowerCase();
    if (CANONICAL_IDS.has(suffix)) {
        return suffix;
    }
    return TOPIC_ALIASES[suffix] ?? null;
}

/** Total of the midpoint weights (should be ~100, but normalise anyway). */
export const TOTAL_MIDPOINT_WEIGHT: number = TOPICS.reduce(
    (sum, topic) => sum + topic.midpoint,
    0,
);
