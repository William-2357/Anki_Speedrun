// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * Tag -> topic grouping for the concept map (Anki Speedrun).
 *
 * Mirrors, per tag node, the same read-time attribution the dashboard uses
 * per card (`rslib/src/stats/mastery.rs`):
 *
 * 1. a canonical `cfa::topic::<suffix>` tag resolves through the canonical
 *    ids + aliases (an unknown suffix resolves to nothing - the user map is
 *    deliberately not consulted, matching the engine's precedence);
 * 2. an exact match in the user `speedrun:tagTopicMap`;
 * 3. the longest map key that prefixes the tag at a `::` boundary;
 * 4. otherwise ungrouped. A tag resolving to "ignore" stays visible on the
 *    map but is never clustered - the map shows data, it does not hide it.
 *
 * Only the 10 canonical topics (plus their aliases) form groups; unknown
 * map values group nothing, exactly like the dashboard drops them.
 */

import { IGNORE_TOPIC_VALUE } from "../dashboard/config";
import { canonicalTopicId, TOPICS } from "../dashboard/topics";

/** Keep in sync with DEFAULT_TOPIC_TAG_PREFIX in rslib/src/stats/mastery.rs. */
export const TOPIC_TAG_PREFIX = "cfa::topic::";

/**
 * Normalize user map keys the way the engine does: trimmed, lowercased,
 * trailing "::" stripped; entries with an empty key or value are dropped.
 */
export function normalizeTagTopicMap(map: Record<string, string>): Map<string, string> {
    const normalized = new Map<string, string>();
    for (const [rawKey, rawValue] of Object.entries(map)) {
        let key = rawKey.trim().toLowerCase();
        while (key.endsWith("::")) {
            key = key.slice(0, -2);
        }
        const value = rawValue.trim();
        if (key !== "" && value !== "") {
            normalized.set(key, value);
        }
    }
    return normalized;
}

/**
 * The value of the longest map key that is a strict prefix of `tag` at a
 * `::` boundary (key K matches tag T when T starts with K + "::").
 */
function longestPrefixValue(map: Map<string, string>, tag: string): string | null {
    let end = tag.length;
    for (;;) {
        const pos = tag.lastIndexOf("::", end - 2);
        if (pos <= 0) {
            return null;
        }
        const value = map.get(tag.slice(0, pos));
        if (value !== undefined) {
            return value;
        }
        end = pos;
    }
}

/**
 * The canonical topic id a tag node belongs to, or null when it stays
 * ungrouped. `map` must come from [normalizeTagTopicMap].
 */
export function resolveTopicId(tag: string, map: Map<string, string>): string | null {
    const lower = tag.trim().toLowerCase();
    if (lower.startsWith(TOPIC_TAG_PREFIX)) {
        const suffix = lower.slice(TOPIC_TAG_PREFIX.length);
        // an empty suffix falls through to the map, like the engine's
        // non-empty-rest filter; a non-empty one never does
        if (suffix !== "") {
            return canonicalTopicId(suffix);
        }
    }
    const value = map.get(lower) ?? longestPrefixValue(map, lower);
    if (value === null || value === undefined) {
        return null;
    }
    if (value.toLowerCase() === IGNORE_TOPIC_VALUE) {
        return null;
    }
    return canonicalTopicId(value);
}

/** Display name for a canonical topic id (falls back to the raw id). */
export function topicLabel(topicId: string): string {
    return TOPICS.find((topic) => topic.id === topicId)?.name ?? topicId;
}
