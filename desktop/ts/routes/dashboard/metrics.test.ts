// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { GetReadinessResponse, GetReadinessResponse_Kind, TopicMasteryResponse } from "@generated/anki/stats_pb";
import { expect, test } from "vitest";

import { isValidExamDate } from "./config";
import { buildDashboardModel, readinessGaugeFromRpc } from "./metrics";

const emptyResponse = {
    topics: [],
    cardsWithoutTopic: 0,
    totalCards: 0,
    gradedReviews: 0n,
    fsrsEnabled: true,
    highRecallThreshold: 0.9,
    unmappedTags: [],
    ungradedAigCards: 0,
    heldOutProbeCards: 0,
};

test("user-map buckets fold onto canonical topics alongside aliases", () => {
    // The backend applies the user map per card and returns buckets named
    // by the map's values; the editor only offers the 10 canonical ids, so
    // they fold through the same path as cfa::topic:: suffixes + aliases.
    const model = buildDashboardModel(
        new TopicMasteryResponse({
            ...emptyResponse,
            topics: [
                // canonical suffix straight from a cfa::topic:: tag
                {
                    topic: "fixed_income",
                    totalCards: 2,
                    studiedCards: 1,
                    highRecallCards: 1,
                    averageRetrievability: 0.9,
                    retrievabilityStddev: 0,
                },
                // alias suffix folds onto the same subject
                {
                    topic: "fi",
                    totalCards: 1,
                    studiedCards: 1,
                    highRecallCards: 0,
                    averageRetrievability: 0.5,
                    retrievabilityStddev: 0,
                },
                // a user-map bucket arrives under the map's value
                {
                    topic: "quantitative_methods",
                    totalCards: 3,
                    studiedCards: 0,
                    highRecallCards: 0,
                    averageRetrievability: 0,
                    retrievabilityStddev: 0,
                },
            ],
            totalCards: 6,
        }),
        { testMode: false },
    );

    const fixedIncome = model.subjects.find((row) => row.topic.id === "fixed_income")!;
    expect(fixedIncome.totalCards).toBe(3);
    expect(fixedIncome.studiedCards).toBe(2);
    // studied-count-weighted mean of 0.9 and 0.5
    expect(fixedIncome.memory).toBeCloseTo(0.7);

    const quant = model.subjects.find(
        (row) => row.topic.id === "quantitative_methods",
    )!;
    expect(quant.totalCards).toBe(3);
    // nothing studied: abstain, never a fake number
    expect(quant.memory).toBeNull();
});

test("unmapped tags and the aig exclusion are surfaced honestly", () => {
    const model = buildDashboardModel(
        new TopicMasteryResponse({
            ...emptyResponse,
            totalCards: 6,
            cardsWithoutTopic: 3,
            unmappedTags: [
                { topic: "mystery::x", totalCards: 3 },
                { topic: "noise", totalCards: 1 },
            ],
            ungradedAigCards: 2,
        }),
        { testMode: false },
    );

    expect(model.cardsWithoutTopic).toBe(3);
    expect(model.unmappedTags).toEqual([
        { tag: "mystery::x", cards: 3 },
        { tag: "noise", cards: 1 },
    ]);
    expect(model.ungradedAigCards).toBe(2);
    expect(model.aigExclusionNote).toBe(
        "2 ungraded generated cards are excluded from all gauges",
    );
});

test("no aig note when nothing is excluded", () => {
    const model = buildDashboardModel(new TopicMasteryResponse(emptyResponse), {
        testMode: false,
    });
    expect(model.ungradedAigCards).toBe(0);
    expect(model.aigExclusionNote).toBeNull();

    const single = buildDashboardModel(
        new TopicMasteryResponse({ ...emptyResponse, ungradedAigCards: 1 }),
        { testMode: false },
    );
    expect(single.aigExclusionNote).toBe(
        "1 ungraded generated card is excluded from all gauges",
    );
});

// ---- Readiness: a thin projection of the backend response ----

test("an abstaining backend response shows reasons, never numbers", () => {
    const gauge = readinessGaugeFromRpc(
        new GetReadinessResponse({
            kind: GetReadinessResponse_Kind.ABSTAIN,
            missing: ["Only 0 delayed held-out probe outcomes; need at least 50."],
        }),
    );
    expect(gauge.kind).toBe("abstain");
    expect(gauge.value).toBeUndefined();
    expect(gauge.range).toBeUndefined();
    expect(gauge.missing).toHaveLength(1);
});

test("a value response maps the band, call confidence and calibration badge", () => {
    const gauge = readinessGaugeFromRpc(
        new GetReadinessResponse({
            kind: GetReadinessResponse_Kind.VALUE,
            pPassLow: 0.81,
            pPassHigh: 0.95,
            pPassCenter: 0.9,
            call: "pass",
            callConfidence: 0.85,
            reasons: ["Method: Beta-Binomial …"],
            calibration: {
                fittedAt: "2026-07-04",
                brier: 0.18,
                logLoss: 0.5,
                n: 20,
                temperature: 1.1,
            },
        }),
    );
    expect(gauge.kind).toBe("value");
    expect(gauge.value).toBeCloseTo(0.9);
    expect(gauge.range).toEqual({ low: 0.81, high: 0.95 });
    // the certainty cap makes "high" unreachable by design
    expect(gauge.confidence).toBe("medium");
    expect(gauge.badge).toContain("2026-07-04");
    expect(gauge.reasons).toHaveLength(1);
});

test("test-mode responses stay loudly labelled and keep the gate list", () => {
    const gauge = readinessGaugeFromRpc(
        new GetReadinessResponse({
            kind: GetReadinessResponse_Kind.TEST,
            pPassLow: 0.02,
            pPassHigh: 0.98,
            pPassCenter: 0.5,
            callConfidence: 0,
            missing: ["Only 12 graded study reviews; need at least 300."],
            reasons: ["TEST MODE: …"],
        }),
    );
    expect(gauge.kind).toBe("test");
    expect(gauge.badge).toContain("TEST DATA");
    expect(gauge.missing).toHaveLength(1);
    expect(gauge.confidence).toBe("low");
});

test("an unreachable readiness backend abstains instead of computing locally", () => {
    const model = buildDashboardModel(new TopicMasteryResponse(emptyResponse), {
        testMode: false,
    });
    expect(model.readinessDetail).toBeNull();
    expect(model.readiness.kind).toBe("abstain");
    expect(model.readiness.missing[0]).toContain("did not respond");
    expect(model.heldOutProbes).toBe(0);
});

test("the model carries the backend evidence through unchanged", () => {
    const readiness = new GetReadinessResponse({
        kind: GetReadinessResponse_Kind.ABSTAIN,
        missing: ["…"],
        evidence: {
            probeCorrect: 3,
            probeAnsweredDelayed: 4,
            probeAnsweredUndelayed: 1,
            probeUnanswered: 45,
            gradedReviews: 12n,
            coverage: 0.2,
            topicsStudied: 2,
            topicsTotal: 10,
            meanProbeLagDays: 9.5,
            probeNeverStudied: 1,
            calibrationOutcomes: 0,
            fsrsEnabled: true,
        },
    });
    const model = buildDashboardModel(
        new TopicMasteryResponse({ ...emptyResponse, heldOutProbeCards: 70 }),
        { testMode: false, readiness },
    );
    expect(model.heldOutProbes).toBe(4);
    expect(model.heldOutProbeCards).toBe(70);
    expect(model.readinessDetail?.evidence?.probeUnanswered).toBe(45);
});

test("exam dates must be real YYYY-MM-DD dates", () => {
    expect(isValidExamDate("2026-11-15")).toBe(true);
    expect(isValidExamDate("2026-01-01")).toBe(true);
    // no rollover: February 30th must not silently become March
    expect(isValidExamDate("2026-02-30")).toBe(false);
    expect(isValidExamDate("2026-13-01")).toBe(false);
    expect(isValidExamDate("15/11/2026")).toBe(false);
    expect(isValidExamDate("2026-1-1")).toBe(false);
    expect(isValidExamDate("")).toBe(false);
});
