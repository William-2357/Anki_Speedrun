// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { TopicMasteryResponse } from "@generated/anki/stats_pb";
import { expect, test } from "vitest";

import { isValidExamDate } from "./config";
import { buildDashboardModel } from "./metrics";

const emptyResponse = {
    topics: [],
    cardsWithoutTopic: 0,
    totalCards: 0,
    gradedReviews: 0n,
    fsrsEnabled: true,
    highRecallThreshold: 0.9,
    unmappedTags: [],
    ungradedAigCards: 0,
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
