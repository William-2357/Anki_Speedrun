// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { TopicMasteryResponse } from "@generated/anki/stats_pb";
import { afterEach, expect, test, vi } from "vitest";

import { coachFacts, daysToExam, fetchAssistantStatus, mergeSuggestions } from "./assistant";
import { buildDashboardModel } from "./metrics";

afterEach(() => {
    vi.unstubAllGlobals();
});

const response = new TopicMasteryResponse({
    topics: [
        {
            topic: "fixed_income",
            totalCards: 10,
            studiedCards: 4,
            highRecallCards: 2,
            averageRetrievability: 0.8123,
            retrievabilityStddev: 0.1,
        },
        {
            topic: "derivatives",
            totalCards: 8,
            studiedCards: 2,
            highRecallCards: 0,
            averageRetrievability: 0.4567,
            retrievabilityStddev: 0.2,
        },
    ],
    cardsWithoutTopic: 0,
    totalCards: 18,
    gradedReviews: 42n,
    fsrsEnabled: true,
    highRecallThreshold: 0.9,
    unmappedTags: [],
    ungradedAigCards: 0,
});

test("coach facts carry only already-computed, rounded dashboard numbers", () => {
    const model = buildDashboardModel(response, { testMode: false });
    const facts = coachFacts(model, "2026-08-06", new Date(Date.UTC(2026, 6, 3)));

    expect(facts.exam).toBe("CFA Level I");
    expect(facts.days_to_exam).toBe(34);
    expect(facts.graded_reviews).toBe(42);
    expect(facts.best_next).toBe(model.bestNext);
    // rounded to what the page displays; no extra precision leaks to the model
    expect(facts.coverage).toBe(Math.round(model.coverage * 100) / 100);
    for (const subject of facts.subjects) {
        if (subject.memory !== null) {
            expect(subject.memory).toBe(Math.round(subject.memory * 100) / 100);
        }
    }
    // subjects arrive worst-gap first so "prioritise by weighted gap" is
    // readable straight off the facts
    const gaps = facts.subjects.map((row) => row.weighted_gap);
    expect([...gaps].sort((a, b) => b - a)).toEqual(gaps);
});

test("readiness abstention reasons ride along verbatim", () => {
    const model = buildDashboardModel(response, { testMode: false });
    const facts = coachFacts(model, "", new Date(Date.UTC(2026, 6, 3)));

    expect(model.readiness.kind).toBe("abstain");
    expect(facts.readiness.kind).toBe("abstain");
    expect(facts.readiness.missing).toEqual(model.readiness.missing);
    expect(facts.readiness.value).toBeUndefined();
    // no exam date -> null, never a guessed horizon
    expect(facts.days_to_exam).toBeNull();
});

test("daysToExam counts whole UTC days and rejects malformed dates", () => {
    const now = new Date(Date.UTC(2026, 6, 3, 23, 59));
    expect(daysToExam("2026-07-04", now)).toBe(1);
    expect(daysToExam("2026-07-03", now)).toBe(0);
    expect(daysToExam("2026-07-01", now)).toBe(-2);
    expect(daysToExam("", now)).toBeNull();
    expect(daysToExam("garbage", now)).toBeNull();
});

// Android serves the same page but has no desktop bridge route: the fetch
// fails (or an old desktop build 404s), the status resolves to null, and the
// page's `assistant !== null` gate hides every AI affordance.
test("fetchAssistantStatus resolves null when the bridge is absent", async () => {
    vi.stubGlobal(
        "fetch",
        vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    expect(await fetchAssistantStatus()).toBeNull();

    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
            new Response("not found", { status: 404 }),
        ),
    );
    expect(await fetchAssistantStatus()).toBeNull();
});

test("fetchAssistantStatus returns the status only when the bridge answers", async () => {
    const status = {
        bridge: true,
        available: true,
        unavailableReason: null,
        aiAssist: false,
        debriefEnabled: false,
        coachEnabled: false,
        tagSuggestEnabled: false,
        backend: "mock",
    };
    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
            new Response(JSON.stringify(status), { status: 200 }),
        ),
    );
    expect(await fetchAssistantStatus()).toEqual(status);

    // a reply without the bridge marker is treated as absent, not trusted
    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
            new Response(JSON.stringify({ ...status, bridge: false }), { status: 200 }),
        ),
    );
    expect(await fetchAssistantStatus()).toBeNull();
});

test("mergeSuggestions pre-fills blanks only and validates topics", () => {
    const pending = { "user::chosen": "ethics" };
    const suggestions = {
        "user::chosen": { topic: "derivatives", confidence: 0.9 },
        "fi::notes": { topic: "fixed_income", confidence: 0.8 },
        "noise::tag": { topic: "ignore", confidence: 0.7 },
        "weird::tag": { topic: "astrology", confidence: 0.99 },
    };
    const valid = new Set(["ethics", "derivatives", "fixed_income", "ignore"]);

    const { merged, applied } = mergeSuggestions(pending, suggestions, valid);

    // the user's existing choice is never clobbered
    expect(merged["user::chosen"]).toBe("ethics");
    // valid suggestions fill blanks (including "ignore")
    expect(merged["fi::notes"]).toBe("fixed_income");
    expect(merged["noise::tag"]).toBe("ignore");
    // invented topic ids never reach the map
    expect(merged["weird::tag"]).toBeUndefined();
    expect(applied).toBe(2);
    // pure: the input map is untouched
    expect(pending["fi::notes"]).toBeUndefined();
});
