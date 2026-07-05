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
 *   factor), always labelled as such; the Phase 3 held-out probe harness
 *   measures the real memory->performance gap offline.
 * - Readiness: P(pass) for the pass/fail CFA Level I exam. Phase 3 moved
 *   the math and the give-up gate into the Rust backend (`GetReadiness`,
 *   `rslib/src/readiness/`): a Beta-Binomial band over DELAYED held-out
 *   probe outcomes, a second honest number (confidence of the pass/fail
 *   call), and backend-enforced abstention. This file is now a thin
 *   DISPLAY layer for Readiness - it renders what the backend sent and
 *   computes nothing, so no display bug can leak an unearned number.
 *
 * The give-up rule (R1, enforced in the backend, echoed here for display):
 *   Readiness is shown only when ALL hold:
 *     graded study reviews >= 300,
 *     topic coverage >= 70% (weighted, studied),
 *     delayed held-out probe outcomes >= 50,
 *     and the resulting band half-width <= 0.20.
 *   Otherwise the backend names exactly which inputs are missing, and the
 *   dashboard shows that list. The labelled test mode (?readinessTest=1)
 *   asks the backend to relax the gates; its output is marked TEST data.
 */

import type { GetReadinessResponse, TopicMasteryResponse } from "@generated/anki/stats_pb";
import { GetReadinessResponse_Kind } from "@generated/anki/stats_pb";

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
    /** probe::held_out cards, excluded from Memory/coverage (held-out hygiene) */
    heldOutProbeCards: number;
    /** weighted share of the exam whose topics have >= 1 studied card, 0-1 */
    coverage: number;
    /** weighted share of the exam whose topics exist in the deck at all, 0-1 */
    deckCoverage: number;
    subjects: SubjectRow[];
    memory: Gauge;
    performance: Gauge;
    readiness: Gauge;
    /** the raw GetReadiness response backing the readiness gauge (the full
     * honesty contract: evidence, calibration history, second number), or
     * null when the backend was unreachable */
    readinessDetail: GetReadinessResponse | null;
    bestNext: string | null;
    generatedAt: Date;
}

/** The written-down give-up thresholds — enforced by the Rust backend
 * (`rslib/src/readiness/`); echoed here only for the footer documentation.
 * Test mode asks the backend to relax them; every resulting number is
 * labelled test data. */
export const READINESS_GATES = {
    minGradedReviews: 300,
    minCoverage: 0.7,
    minHeldOutProbes: 50,
    maxIntervalHalfWidth: 0.2,
};

/** Display parameters for the Memory and Performance gauges (unchanged
 * from Phase 1). Readiness no longer reads any of these — its math lives
 * in the Rust backend. */
export const PROXY_DISPLAY = {
    /** per-topic recall target used for the weighted-gap column */
    performanceTarget: 0.8,
    /** extra widening on the performance proxy: tau is an assumption */
    proxyExtraWidth: 0.15,
    /** z for a ~90% band on the memory mean */
    z90: 1.645,
};

function clamp01(x: number): number {
    return Math.min(1, Math.max(0, x));
}

/** Map the backend's readiness response onto the display gauge — a pure
 * projection. When the backend abstains the numbers are already zeroed
 * server-side and none are shown; when it is unreachable (`null`, e.g. an
 * old backend build) the gauge abstains too, rather than falling back to
 * any local computation. */
export function readinessGaugeFromRpc(
    response: GetReadinessResponse | null | undefined,
): Gauge {
    if (!response) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: [
                "The readiness backend did not respond; no number is shown in its place.",
            ],
        };
    }
    if (response.kind === GetReadinessResponse_Kind.ABSTAIN) {
        return {
            kind: "abstain",
            confidence: "none",
            reasons: [],
            missing: [...response.missing],
        };
    }
    const test = response.kind === GetReadinessResponse_Kind.TEST;
    // the certainty cap means "high" is unreachable by design
    const confidence: Confidence = response.callConfidence >= 0.75 ? "medium" : "low";
    const calibration = response.calibration;
    let badge: string;
    if (test) {
        badge = "TEST DATA \u2014 not a real prediction";
    } else if (calibration) {
        badge = `calibration checked ${calibration.fittedAt}`;
    } else {
        badge = "band from held-out probes; calibration never run";
    }
    return {
        kind: test ? "test" : "value",
        value: response.pPassCenter,
        range: { low: response.pPassLow, high: response.pPassHigh },
        confidence,
        badge,
        reasons: [...response.reasons],
        missing: test ? [...response.missing] : [],
    };
}

export function buildDashboardModel(
    response: TopicMasteryResponse,
    options: {
        testMode: boolean;
        now?: Date;
        readiness?: GetReadinessResponse | null;
    } = { testMode: false },
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
                low: clamp01(memory - PROXY_DISPLAY.z90 * sem),
                high: clamp01(memory + PROXY_DISPLAY.z90 * sem),
            };
        }
        const performance = memory !== null ? clamp01(memory * topic.transfer) : null;
        const weightedGap = topic.midpoint
            * (PROXY_DISPLAY.performanceTarget - (performance ?? 0));
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
    // thin layer: the readiness gauge is a pure projection of the backend
    // response — nothing is computed locally
    const readinessDetail = options.readiness ?? null;
    const readiness = readinessGaugeFromRpc(readinessDetail);

    const ungradedAigCards = response.ungradedAigCards;

    return {
        fsrsEnabled,
        gradedReviews,
        heldOutProbes: readinessDetail?.evidence?.probeAnsweredDelayed ?? 0,
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
        heldOutProbeCards: response.heldOutProbeCards,
        coverage,
        deckCoverage,
        subjects,
        memory,
        performance,
        readiness,
        readinessDetail,
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
    const width = PROXY_DISPLAY.proxyExtraWidth;
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
            "The Phase 3 probe harness measures the real memory\u2192performance gap on delayed held-out MCQs; until it reports, this proxy stays uncalibrated.",
            `Range widened by \u00b1${width} to reflect that the transfer factor is an assumption.`,
            `Coverage: ${Math.round(coverage * 100)}% of exam weight has studied cards.`,
        ],
        missing: [],
    };
}
