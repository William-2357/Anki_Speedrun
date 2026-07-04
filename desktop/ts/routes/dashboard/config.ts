// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * Synced collection-config storage for the dashboard (Anki Speedrun).
 *
 * All keys are read/written through the existing config service RPCs so
 * they sync natively with the collection:
 *
 * - `speedrun:tagTopicMap`: a JSON object { tag or tag prefix -> canonical
 *   topic id, or "ignore" }. Applied by the backend at read time only -
 *   note tags are never rewritten.
 * - `speedrun:exam_date`: "YYYY-MM-DD". The scheduler's fade ladder reads
 *   this exact key; an empty or unparsable value means "no exam date set"
 *   and disables fading rather than guessing a horizon.
 * - `speedrun:aiAssist` + per-feature keys (RUNTIME_AI_PLAN S3): the
 *   default-OFF toggles for the runtime assistant layer. The desktop host
 *   bridge reads the same keys server-side, so a feature is live only when
 *   the master switch AND its own flag are true on this collection.
 */

import { getConfigJson, setConfigJson } from "@generated/backend";

export const TAG_TOPIC_MAP_KEY = "speedrun:tagTopicMap";
export const EXAM_DATE_KEY = "speedrun:exam_date";
/** Special map value: drop the tag's cards from every topic (noise tags). */
export const IGNORE_TOPIC_VALUE = "ignore";

/** Master switch for every runtime-AI feature (default OFF). */
export const AI_ASSIST_KEY = "speedrun:aiAssist";
export const DEBRIEF_ENABLED_KEY = "speedrun:debriefEnabled";
export const COACH_ENABLED_KEY = "speedrun:coachEnabled";
export const TAG_SUGGEST_ENABLED_KEY = "speedrun:tagSuggestEnabled";
/** "" = decide from the environment; else mock | claude-cli | openai-compatible. */
export const AI_BACKEND_KEY = "speedrun:aiBackend";

export const AI_BACKEND_CHOICES = ["", "mock", "claude-cli", "openai-compatible"] as const;
export type AiBackendChoice = (typeof AI_BACKEND_CHOICES)[number];

export interface AiAssistFlags {
    aiAssist: boolean;
    debriefEnabled: boolean;
    coachEnabled: boolean;
    tagSuggestEnabled: boolean;
    backend: AiBackendChoice;
}

/** Every runtime-AI toggle defaults to OFF; a missing key is OFF. */
export const AI_FLAG_KEYS: Record<keyof Omit<AiAssistFlags, "backend">, string> = {
    aiAssist: AI_ASSIST_KEY,
    debriefEnabled: DEBRIEF_ENABLED_KEY,
    coachEnabled: COACH_ENABLED_KEY,
    tagSuggestEnabled: TAG_SUGGEST_ENABLED_KEY,
};

/** A missing key is not an error - it just means "nothing stored yet". */
async function getJsonConfig(key: string): Promise<unknown> {
    try {
        const response = await getConfigJson({ val: key }, { alertOnError: false });
        return JSON.parse(new TextDecoder().decode(response.json));
    } catch {
        return null;
    }
}

async function setJsonConfig(key: string, value: unknown): Promise<void> {
    await setConfigJson({
        key,
        valueJson: new TextEncoder().encode(JSON.stringify(value)),
        undoable: false,
    });
}

/** The stored tag->topic map; a missing/malformed key yields {}. */
export async function getTagTopicMap(): Promise<Record<string, string>> {
    const value = await getJsonConfig(TAG_TOPIC_MAP_KEY);
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
        return {};
    }
    const map: Record<string, string> = {};
    for (const [tag, topic] of Object.entries(value)) {
        if (typeof topic === "string" && topic !== "") {
            map[tag] = topic;
        }
    }
    return map;
}

export async function saveTagTopicMap(map: Record<string, string>): Promise<void> {
    await setJsonConfig(TAG_TOPIC_MAP_KEY, map);
}

/** The stored exam date ("YYYY-MM-DD"), or "" when unset. */
export async function getExamDate(): Promise<string> {
    const value = await getJsonConfig(EXAM_DATE_KEY);
    return typeof value === "string" ? value : "";
}

/** True for a real calendar date in YYYY-MM-DD form (no rollover). */
export function isValidExamDate(date: string): boolean {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
    if (!match) {
        return false;
    }
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return (
        parsed.getUTCFullYear() === year
        && parsed.getUTCMonth() === month - 1
        && parsed.getUTCDate() === day
    );
}

/** Store the exam date; "" unsets it (the scheduler treats a value it
 * cannot parse as "no exam date" and disables the fade ladder). */
export async function saveExamDate(date: string): Promise<void> {
    await setJsonConfig(EXAM_DATE_KEY, date);
}

/** The stored runtime-AI toggles; every missing/malformed key reads as the
 * default-OFF state (the honest fallback: AI never turns itself on). */
export async function getAiAssistFlags(): Promise<AiAssistFlags> {
    const [aiAssist, debriefEnabled, coachEnabled, tagSuggestEnabled, backend] = await Promise.all([
        getJsonConfig(AI_ASSIST_KEY),
        getJsonConfig(DEBRIEF_ENABLED_KEY),
        getJsonConfig(COACH_ENABLED_KEY),
        getJsonConfig(TAG_SUGGEST_ENABLED_KEY),
        getJsonConfig(AI_BACKEND_KEY),
    ]);
    return {
        aiAssist: aiAssist === true,
        debriefEnabled: debriefEnabled === true,
        coachEnabled: coachEnabled === true,
        tagSuggestEnabled: tagSuggestEnabled === true,
        backend: AI_BACKEND_CHOICES.includes(backend as AiBackendChoice)
            ? (backend as AiBackendChoice)
            : "",
    };
}

/** Persist one boolean runtime-AI toggle (synced collection config). */
export async function saveAiAssistFlag(
    flag: keyof typeof AI_FLAG_KEYS,
    value: boolean,
): Promise<void> {
    await setJsonConfig(AI_FLAG_KEYS[flag], value);
}

/** Persist the runtime-AI backend choice ("" = decide from the environment). */
export async function saveAiBackend(backend: AiBackendChoice): Promise<void> {
    await setJsonConfig(AI_BACKEND_KEY, backend);
}
