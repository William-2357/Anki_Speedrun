// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * Client for the desktop runtime-AI assistant bridge (RUNTIME_AI_PLAN S2).
 *
 * The dashboard page POSTs JSON to `/_anki/speedrunAssistant`; the desktop
 * host (qt/aqt/speedrun_assistant.py) answers. The route exists ONLY on
 * desktop: on Android (the same Svelte page served from the .aar) the
 * request fails, `fetchAssistantStatus` resolves to null, and every AI
 * affordance stays hidden - graceful degradation, no broken buttons.
 *
 * Nothing in this module writes to the collection. The bridge itself is
 * read-only; toggles are persisted separately through the standard config
 * RPCs (see config.ts), and the tag suggester only pre-fills an editor the
 * user must still Save.
 */

import type { DashboardModel, Gauge } from "./metrics";

export const ASSISTANT_ENDPOINT = "/_anki/speedrunAssistant";

export interface AssistantStatus {
    bridge: boolean;
    /** the tools/speedrun assistant package imported OK on the host */
    available: boolean;
    unavailableReason: string | null;
    aiAssist: boolean;
    debriefEnabled: boolean;
    coachEnabled: boolean;
    tagSuggestEnabled: boolean;
    backend: string;
}

export interface DebriefTopicRow {
    topic: string;
    lapses: number;
    reviews: number;
}

export interface DebriefPairRow {
    pair: [string, string];
    lift: number;
    session_lapses: number;
}

export interface DebriefMisconceptionRow {
    id: string;
    count: number;
}

/** The deterministic pattern report (Feature A milestone A2). */
export interface DebriefReport {
    window: {
        start_ms: number;
        end_ms: number;
        n_reviews: number;
        n_lapses: number;
        gap_minutes: number;
    };
    topics_missed: DebriefTopicRow[];
    confusable_pairs: DebriefPairRow[];
    misconceptions: DebriefMisconceptionRow[];
    best_next: string;
}

export interface DebriefResult {
    enabled: boolean;
    reason?: string;
    report?: DebriefReport | null;
    narrative?: { narrative: string; next_step: string } | null;
    narrativeStatus?: string;
    disclosure?: string;
}

export interface CoachPriority {
    topic: string;
    why?: string;
}

export interface CoachResult {
    enabled: boolean;
    reason?: string;
    plan?: { summary: string; priorities: CoachPriority[]; note?: string } | null;
    planStatus?: string;
    disclosure?: string;
}

export interface TagSuggestion {
    topic: string;
    confidence: number;
}

export interface SuggestResult {
    enabled: boolean;
    reason?: string;
    suggestions?: Record<string, TagSuggestion>;
    consideredTags?: number;
    totalTags?: number;
    suggestStatus?: string;
    disclosure?: string;
}

/** JSON POST to the bridge. mediasrv only accepts POST bodies declared as
 * application/binary (its cross-origin guard), so the JSON rides under that
 * content type and the host parses the raw bytes. */
async function callAssistant<T>(action: string, payload: Record<string, unknown> = {}): Promise<T> {
    const response = await fetch(ASSISTANT_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/binary" },
        body: JSON.stringify({ action, ...payload }),
    });
    if (!response.ok) {
        throw new Error(`${response.status}: ${await response.text().catch(() => "")}`);
    }
    const reply = (await response.json()) as T & { error?: string };
    if (reply.error) {
        throw new Error(reply.error);
    }
    return reply;
}

/** Feature-detect the desktop bridge. null = absent (Android, or an old
 * desktop build): the page must render no AI affordances at all. */
export async function fetchAssistantStatus(): Promise<AssistantStatus | null> {
    try {
        const status = await callAssistant<AssistantStatus>("status");
        return status.bridge ? status : null;
    } catch {
        return null;
    }
}

export async function requestDebrief(): Promise<DebriefResult> {
    return callAssistant<DebriefResult>("debrief");
}

export async function requestCoach(facts: CoachFacts): Promise<CoachResult> {
    return callAssistant<CoachResult>("coach", { facts });
}

export interface SuggestTagInput {
    tag: string;
    cards: number;
}

export async function requestTagSuggestions(
    tags: SuggestTagInput[],
    topics: string[],
): Promise<SuggestResult> {
    return callAssistant<SuggestResult>("suggestTags", { tags, topics });
}

// ---------------------------------------------------------------------------
// B1 - serialize the already-computed DashboardModel into the facts dict
// ---------------------------------------------------------------------------

export interface GaugeFacts {
    kind: "value" | "abstain" | "test";
    value?: number;
    low?: number;
    high?: number;
    /** abstention reasons, passed through VERBATIM so the coach can echo
     * them without inventing anything */
    missing?: string[];
}

export interface SubjectFacts {
    name: string;
    memory: number | null;
    performance: number | null;
    studied: number;
    total: number;
    weight_pct: number;
    weighted_gap: number;
}

export interface CoachFacts {
    exam: string;
    days_to_exam: number | null;
    graded_reviews: number;
    coverage: number;
    deck_coverage: number;
    best_next: string | null;
    memory: GaugeFacts;
    performance: GaugeFacts;
    readiness: GaugeFacts;
    subjects: SubjectFacts[];
}

function round(x: number, places: number): number {
    const factor = 10 ** places;
    return Math.round(x * factor) / factor;
}

function gaugeFacts(gauge: Gauge): GaugeFacts {
    if (gauge.kind === "abstain") {
        return { kind: "abstain", missing: [...gauge.missing] };
    }
    const facts: GaugeFacts = { kind: gauge.kind, value: round(gauge.value ?? 0, 2) };
    if (gauge.range) {
        facts.low = round(gauge.range.low, 2);
        facts.high = round(gauge.range.high, 2);
    }
    return facts;
}

/** Whole days until the stored exam date, or null when it is unset. A past
 * date yields a negative count rather than a guess. */
export function daysToExam(examDate: string, now: Date = new Date()): number | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(examDate);
    if (!match) {
        return null;
    }
    const exam = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    return Math.round((exam - today) / 86_400_000);
}

/**
 * The coach's facts: nothing the dashboard has not already computed and
 * shown. Gauge abstentions ride along verbatim; every number is rounded to
 * what the page itself displays, so the grounding check in the assistant
 * core can match the model's echoes exactly.
 */
export function coachFacts(
    model: DashboardModel,
    examDate: string,
    now: Date = new Date(),
): CoachFacts {
    const subjects = [...model.subjects]
        .sort((a, b) => b.weightedGap - a.weightedGap)
        .map((row) => ({
            name: row.topic.name,
            memory: row.memory === null ? null : round(row.memory, 2),
            performance: row.performance === null ? null : round(row.performance, 2),
            studied: row.studiedCards,
            total: row.totalCards,
            weight_pct: row.topic.midpoint,
            weighted_gap: round(row.weightedGap, 3),
        }));
    return {
        exam: "CFA Level I",
        days_to_exam: daysToExam(examDate, now),
        graded_reviews: model.gradedReviews,
        coverage: round(model.coverage, 2),
        deck_coverage: round(model.deckCoverage, 2),
        best_next: model.bestNext,
        memory: gaugeFacts(model.memory),
        performance: gaugeFacts(model.performance),
        readiness: gaugeFacts(model.readiness),
        subjects,
    };
}

// ---------------------------------------------------------------------------
// C3 - apply suggestions to the Map-tags editor's pending map (pre-fill only)
// ---------------------------------------------------------------------------

/**
 * Pre-fill dropdowns with validated suggestions without clobbering anything
 * the user (or a previous Save) already chose: only tags with no pending
 * entry are filled, and only with a known topic id or "ignore". Returns the
 * new map plus how many entries were actually applied - nothing is saved.
 */
export function mergeSuggestions(
    pending: Record<string, string>,
    suggestions: Record<string, TagSuggestion>,
    validValues: Set<string>,
): { merged: Record<string, string>; applied: number } {
    const merged = { ...pending };
    let applied = 0;
    for (const [tag, suggestion] of Object.entries(suggestions)) {
        if (tag in merged) {
            continue;
        }
        if (!validValues.has(suggestion.topic)) {
            continue;
        }
        merged[tag] = suggestion.topic;
        applied += 1;
    }
    return { merged, applied };
}
