// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * Dashboard gauge computation (Anki Speedrun).
 *
 * Three gauges, kept strictly separate and honest:
 *
 * - Memory: real, from FSRS predicted retrievability (via the TopicMastery
 *   RPC). Abstains when FSRS is off or nothing has been studied.
 * - Performance: an *uncalibrated proxy* (Memory x documented transfer
 *   factor), always labelled as such. Phase 2's held-out exam-style question
 *   bank replaces it with a measurement.
 * - Readiness: P(pass) for the pass/fail CFA Level I exam. ABSTAINS by
 *   default under the give-up rule below; the underlying method is
 *   documented here and exercisable only in an explicitly-labelled test
 *   mode. A confident number with no evidence behind it is worse than no
 *   number.
 *
 * The give-up rule (R1, written down):
 *   Readiness is shown only when ALL hold:
 *     graded reviews >= 300,
 *     topic coverage >= 70% (weighted, studied),
 *     held-out performance probes answered >= 50,
 *     and the resulting interval half-width <= 0.20.
 *   Otherwise the dashboard names exactly which inputs are missing.
 *   Phase 1 ships no held-out probe bank, so Readiness always abstains in
 *   real use - which is the honest state until Phase 2/3 land.
 */

import type { TopicMasteryResponse } from "@generated/anki/stats_pb";

import { canonicalTopicId, TOPICS, TOTAL_MIDPOINT_WEIGHT } from "./topics";
import type { TopicInfo } from "./topics";

export type Confidence = "none" | "low" | "medium" | "high";

export interface Range {
    low: number;
    high: number;
}

export interface Gauge {
    /** "value" = show it; "abstain" = show reasons instead; "test" = labelled test data */
    kind: "value" | "abstain" | "test";
    /** 0-1 */
    value?: number;
    range?: Range;
    confidence: Confidence;
    /** the main reasons behind the number (honesty rule) */
    reasons: string[];
    /** named missing inputs when abstaining (give-up rule) */
    missing: string[];
    /** e.g. "uncalibrated estimate" */
    badge?: string;
}

export interface SubjectRow {
    topic: TopicInfo;
    totalCards: number;
    studiedCards: number;
    highRecallCards: number;
    /** mean FSRS retrievability over studied cards, or null if none */
    memory: number | null;
    memoryRange: Range | null;
    /** memory x transfer, uncalibrated, or null */
    performance: number | null;
    /** midpoint exam weight x (target - performance): how much fixing this topic matters */
    weightedGap: number;
}

/** A raw tag carried by cards that resolved to no topic (for the mapping
 * editor). Counts are tag-frequency buckets, not disjoint card counts: a
 * card with several unmapped tags appears under each of them. */
export interface UnmappedTag {
    tag: string;
    cards: number;
}

export interface DashboardModel {
    fsrsEnabled: boolean;
    gradedReviews: number;
    heldOutProbes: number;
    totalCards: number;
    cardsWithoutTopic: number;
    /** most frequent raw tags on unmapped cards, for the "Map tags" editor */
    unmappedTags: UnmappedTag[];
    /** aig::ungraded cards, excluded from every gauge per R24 */
    ungradedAigCards: number;
    /** one-line disclosure of the aig exclusion, or null when nothing is excluded */
    aigExclusionNote: string | null;
    /** weighted share of the exam whose topics have >= 1 studied card, 0-1 */
    coverage: number;
    /** weighted share of the exam whose topics exist in the deck at all, 0-1 */
    deckCoverage: number;
    subjects: SubjectRow[];
    memory: Gauge;
    performance: Gauge;
    readiness: Gauge;
    bestNext: string | null;
    generatedAt: Date;
}

/** The written-down give-up thresholds. Test mode relaxes them but labels
 * every resulting number as test data. */
export const READINESS_GATES = {
    minGradedReviews: 300,
    minCoverage: 0.7,
    minHeldOutProbes: 50,
    maxIntervalHalfWidth: 0.2,
};

/** Documented readiness mapping (method shipped, number abstains):
 * P(pass) = logistic(k * (weightedPerformance - MPS)), with the unpublished
 * minimum passing standard carried as a band rather than a point. */
export const READINESS_METHOD = {
    mpsLow: 0.6,
    mpsHigh: 0.7,
    logisticSlope: 14,
    performanceTarget: 0.8,
    /** extra widening on the performance proxy: tau is an assumption */
    proxyExtraWidth: 0.15,
    /** z for a ~90% band on the memory mean */
    z90: 1.645,
};

function logistic(x: number): number {
    return 1 / (1 + Math.exp(-x));
}

function clamp01(x: number): number {
    return Math.min(1, Math.max(0, x));
}

/** Phase 1 has no held-out probe bank; the count is 0 by construction. */
export const HELD_OUT_PROBES_ANSWERED = 0;

export function buildDashboardModel(
    response: TopicMasteryResponse,
    options: { testMode: boolean; now?: Date } = { testMode: false },
): DashboardModel {
    const fsrsEnabled = response.fsrsEnabled;
    const gradedReviews = Number(response.gradedReviews);

    // fold raw tag suffixes (incl. aliases) onto the 10 canonical topics.
    // Buckets attributed through the user tag->topic map arrive named by
    // the map's values; the mapping editor only offers the 10 canonical
    // ids, so those fold cleanly through the same path.
    const byTopic = new Map<
        string,
        { total: number; studied: number; highRecall: number; sum: number; varSum: number }
    >();
    for (const raw of response.topics) {
        const id = canonicalTopicId(raw.topic);
        if (id === null) {
            continue;
        }
        const acc = byTopic.get(id) ?? {
            total: 0,
            studied: 0,
            highRecall: 0,
            sum: 0,
            varSum: 0,
        };
        acc.total += raw.totalCards;
        acc.studied += raw.studiedCards;
        acc.highRecall += raw.highRecallCards;
        acc.sum += raw.averageRetrievability * raw.studiedCards;
        // pooled variance contribution (approximate; fine for a range display)
        acc.varSum += raw.retrievabilityStddev ** 2 * Math.max(raw.studiedCards - 1, 0);
        byTopic.set(id, acc);
    }

    const subjects: SubjectRow[] = TOPICS.map((topic) => {
        const acc = byTopic.get(topic.id);
        const studied = acc?.studied ?? 0;
        const memory = fsrsEnabled && acc && studied > 0 ? acc.sum / studied : null;
        let memoryRange: Range | null = null;
        if (memory !== null && acc) {
            const pooledSd = studied > 1 ? Math.sqrt(acc.varSum / (studied - 1)) : 0.25;
            const sem = pooledSd / Math.sqrt(studied);
            memoryRange = {
                low: clamp01(memory - READINESS_METHOD.z90 * sem),
                high: clamp01(memory + READINESS_METHOD.z90 * sem),
            };
        }
        const performance = memory !== null ? clamp01(memory * topic.transfer) : null;
        const weightedGap = topic.midpoint
            * (READINESS_METHOD.performanceTarget - (performance ?? 0));
        return {
            topic,
            totalCards: acc?.total ?? 0,
            studiedCards: studied,
            highRecallCards: acc?.highRecall ?? 0,
            memory,
            memoryRange,
            performance,
            weightedGap,
        };
    });

    const coverage = subjects.reduce(
        (sum, row) => sum + (row.studiedCards > 0 ? row.topic.midpoint : 0),
        0,
    ) / TOTAL_MIDPOINT_WEIGHT;
    const deckCoverage = subjects.reduce(
        (sum, row) => sum + (row.totalCards > 0 ? row.topic.midpoint : 0),
        0,
    ) / TOTAL_MIDPOINT_WEIGHT;

    const bestNextRow = [...subjects].sort((a, b) => b.weightedGap - a.weightedGap)[0];
    const bestNext = bestNextRow ? bestNextRow.topic.name : null;

    const memory = memoryGauge(subjects, fsrsEnabled, coverage);
    const performance = performanceGauge(subjects, memory, coverage);
    const readiness = readinessGauge(
        subjects,
        coverage,
        gradedReviews,
        HELD_OUT_PROBES_ANSWERED,
        options.testMode,
    );

    const ungradedAigCards = response.ungradedAigCards;

    return {
        fsrsEnabled,
        gradedReviews,
        heldOutProbes: HELD_OUT_PROBES_ANSWERED,
        totalCards: response.totalCards,
        cardsWithoutTopic: response.cardsWithoutTopic,
        unmappedTags: response.unmappedTags.map((tag) => ({
            tag: tag.topic,
            cards: tag.totalCards,
        })),
        ungradedAigCards,
        // honesty note (R24): excluded cards must be disclosed, not hidden
        aigExclusionNote: ungradedAigCards > 0
            ? `${ungradedAigCards} ungraded generated ${
                ungradedAigCards === 1 ? "card is" : "cards are"
            } excluded from all gauges`
            : null,
        coverage,
        deckCoverage,
        subjects,
        memory,
        performance,
        readiness,
        bestNext,
        generatedAt: options.now ?? new Date(),
    };
}

function memoryGauge(
    subjects: SubjectRow[],
    fsrsEnabled: boolean,
    coverage: number,
): Gauge {
    if (!fsrsEnabled) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: [
                "FSRS is disabled. Enable FSRS in deck options so recall can be estimated; no proxy is shown in its place.",
            ],
        };
    }
    const studiedRows = subjects.filter((row) => row.memory !== null);
    const studiedCards = studiedRows.reduce((sum, row) => sum + row.studiedCards, 0);
    if (studiedCards === 0) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: ["No cards with FSRS memory state yet - study some cards first."],
        };
    }
    // studied-count-weighted mean and range across subjects
    const mean = studiedRows.reduce((sum, row) => sum + row.memory! * row.studiedCards, 0)
        / studiedCards;
    const low = studiedRows.reduce(
        (sum, row) => sum + (row.memoryRange?.low ?? row.memory!) * row.studiedCards,
        0,
    ) / studiedCards;
    const high = studiedRows.reduce(
        (sum, row) => sum + (row.memoryRange?.high ?? row.memory!) * row.studiedCards,
        0,
    ) / studiedCards;
    let confidence: Confidence = "low";
    if (studiedCards >= 300) {
        confidence = "high";
    } else if (studiedCards >= 50) {
        confidence = "medium";
    }
    return {
        kind: "value",
        value: mean,
        range: { low: clamp01(low), high: clamp01(high) },
        confidence,
        reasons: [
            `Mean FSRS predicted retrievability over ${studiedCards} studied cards.`,
            `Unstudied cards are excluded here and show up as coverage (${Math.round(coverage * 100)}%) instead.`,
            "Labelled \u201chigh recall probability\u201d, not \u201cmastered\u201d: retrievability is a scheduling target, not a competence threshold.",
        ],
        missing: [],
    };
}

function performanceGauge(
    subjects: SubjectRow[],
    memory: Gauge,
    coverage: number,
): Gauge {
    if (memory.kind === "abstain") {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: [
                "Performance is derived from Memory in Phase 1, and Memory is abstaining.",
                ...memory.missing,
            ],
        };
    }
    const rows = subjects.filter((row) => row.performance !== null);
    const studiedCards = rows.reduce((sum, row) => sum + row.studiedCards, 0);
    const mean = rows.reduce((sum, row) => sum + row.performance! * row.studiedCards, 0)
        / studiedCards;
    const width = READINESS_METHOD.proxyExtraWidth;
    const low = clamp01(
        rows.reduce(
                    (sum, row) =>
                        sum
                        + (row.memoryRange?.low ?? row.memory!) * row.topic.transfer * row.studiedCards,
                    0,
                ) / studiedCards - width,
    );
    const high = clamp01(
        rows.reduce(
                    (sum, row) =>
                        sum
                        + (row.memoryRange?.high ?? row.memory!) * row.topic.transfer * row.studiedCards,
                    0,
                ) / studiedCards + width,
    );
    return {
        kind: "value",
        value: mean,
        range: { low, high },
        confidence: "low",
        badge: "uncalibrated estimate",
        reasons: [
            "Memory x a documented per-topic transfer factor (a stated assumption, not a measurement).",
            "No held-out exam-style questions have been answered yet, so real transfer cannot be measured in Phase 1.",
            `Range widened by \u00b1${width} to reflect that the transfer factor is an assumption.`,
            `Coverage: ${Math.round(coverage * 100)}% of exam weight has studied cards.`,
        ],
        missing: [],
    };
}

function readinessGauge(
    subjects: SubjectRow[],
    coverage: number,
    gradedReviews: number,
    heldOutProbes: number,
    testMode: boolean,
): Gauge {
    const gates = READINESS_GATES;
    const missing: string[] = [];
    if (gradedReviews < gates.minGradedReviews) {
        missing.push(
            `Only ${gradedReviews} graded reviews; need at least ${gates.minGradedReviews}.`,
        );
    }
    if (coverage < gates.minCoverage) {
        const uncovered = subjects
            .filter((row) => row.studiedCards === 0)
            .map((row) => row.topic.name);
        missing.push(
            `Topic coverage is ${Math.round(coverage * 100)}%; need at least ${Math.round(gates.minCoverage * 100)}%.`
                + (uncovered.length
                    ? ` Not studied yet: ${uncovered.slice(0, 3).join(", ")}${uncovered.length > 3 ? ", \u2026" : ""}.`
                    : ""),
        );
    }
    if (heldOutProbes < gates.minHeldOutProbes) {
        missing.push(
            `Only ${heldOutProbes} held-out performance probes answered; need at least ${gates.minHeldOutProbes}. The probe bank ships in a later phase.`,
        );
    }

    if (missing.length > 0 && !testMode) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing,
        };
    }

    // Documented method - only reachable when the gates pass, or in the
    // explicitly-labelled test mode.
    const rows = subjects.filter((row) => row.performance !== null);
    if (rows.length === 0) {
        return { kind: "abstain", confidence: "none", reasons: [], missing: [...missing, "No studied topics at all."] };
    }
    const weightSum = rows.reduce((sum, row) => sum + row.topic.midpoint, 0);
    const weightedPerformance = rows.reduce(
        (sum, row) => sum + row.performance! * row.topic.midpoint,
        0,
    ) / weightSum;
    // pessimistic corner folds in the uncovered exam weight as zeroes
    const pessimisticPerformance = rows.reduce(
        (sum, row) => sum + row.performance! * row.topic.midpoint,
        0,
    ) / TOTAL_MIDPOINT_WEIGHT;

    const method = READINESS_METHOD;
    const mpsMid = (method.mpsLow + method.mpsHigh) / 2;
    const value = logistic(method.logisticSlope * (weightedPerformance - mpsMid));
    const low = logistic(
        method.logisticSlope * (pessimisticPerformance - method.mpsHigh),
    );
    const high = logistic(
        method.logisticSlope * (weightedPerformance + method.proxyExtraWidth - method.mpsLow),
    );
    const halfWidth = (high - low) / 2;

    if (halfWidth > gates.maxIntervalHalfWidth && !testMode) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: [
                `The probability band is too wide to be useful (half-width ${
                    halfWidth.toFixed(2)
                } > ${gates.maxIntervalHalfWidth}).`,
            ],
        };
    }

    return {
        kind: testMode ? "test" : "value",
        value,
        range: { low, high },
        confidence: coverage >= 0.9 && gradedReviews >= 1000 ? "medium" : "low",
        badge: testMode ? "TEST DATA \u2014 not a real prediction" : "uncalibrated",
        reasons: [
            "CFA Level I is pass/fail, so this is a pass probability, not an invented score.",
            `Method: logistic(${method.logisticSlope} x (weighted performance \u2212 MPS)), MPS carried as a band [${method.mpsLow}, ${method.mpsHigh}] because CFA never publishes it.`,
            "Built on the uncalibrated Performance proxy; no held-out mock results back this number yet.",
        ],
        missing: testMode ? missing : [],
    };
}
