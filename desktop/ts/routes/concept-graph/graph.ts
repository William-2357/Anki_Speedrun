// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/**
 * Concept-graph view model (Anki Speedrun).
 *
 * Pure data shaping for the knowledge map: which tags become nodes, how big
 * they are, and what colour they take. Colouring is honest by construction:
 * a node with no graded answers (difficulty mode) or no FSRS memory state
 * (recall mode) renders neutral grey instead of pretending to know.
 */

import type { ConceptGraphResponse } from "@generated/anki/stats_pb";

export interface GraphNode {
    id: number;
    tag: string;
    /** short display label (last tag segment) */
    label: string;
    cardCount: number;
    studiedCards: number;
    averageRetrievability: number;
    gradedAnswers: number;
    againHardAnswers: number;
    radius: number;
    x: number;
    y: number;
    fx?: number | null;
    fy?: number | null;
}

export interface GraphLink {
    source: GraphNode;
    target: GraphNode;
    noteCount: number;
}

/** Tags that carry no meaning on a knowledge map. */
const IGNORED_TAGS = new Set(["marked", "leech"]);

export function buildGraphModel(
    response: ConceptGraphResponse,
    width: number,
    height: number,
): { nodes: GraphNode[]; links: GraphLink[] } {
    const keptIndex = new Map<number, GraphNode>();
    const nodes: GraphNode[] = [];
    response.nodes.forEach((raw, index) => {
        if (IGNORED_TAGS.has(raw.tag)) {
            return;
        }
        const node: GraphNode = {
            id: index,
            tag: raw.tag,
            label: raw.tag.split("::").pop() ?? raw.tag,
            cardCount: raw.cardCount,
            studiedCards: raw.studiedCards,
            averageRetrievability: raw.averageRetrievability,
            gradedAnswers: raw.gradedAnswers,
            againHardAnswers: raw.againHardAnswers,
            radius: 6 + Math.sqrt(raw.cardCount) * 3,
            // deterministic initial ring layout; the simulation takes over
            x: width / 2 + (width / 4) * Math.cos((2 * Math.PI * index) / response.nodes.length),
            y: height / 2
                + (height / 4) * Math.sin((2 * Math.PI * index) / response.nodes.length),
        };
        keptIndex.set(index, node);
        nodes.push(node);
    });

    const links: GraphLink[] = [];
    for (const edge of response.edges) {
        const source = keptIndex.get(edge.first);
        const target = keptIndex.get(edge.second);
        if (source && target) {
            links.push({ source, target, noteCount: edge.noteCount });
        }
    }
    return { nodes, links };
}

const NO_DATA = "#9ca3af";

function redToGreen(value: number): string {
    // value 0 -> red, 1 -> green, via yellow
    const clamped = Math.min(1, Math.max(0, value));
    const hue = clamped * 120;
    return `hsl(${hue}, 70%, 45%)`;
}

/** Default colouring: behavioural answer difficulty from Again/Hard grades. */
export function difficultyColour(node: GraphNode): string {
    if (node.gradedAnswers === 0) {
        return NO_DATA;
    }
    const accuracy = 1 - node.againHardAnswers / node.gradedAnswers;
    return redToGreen(accuracy);
}

/** Alternative colouring: mean FSRS predicted retrievability. */
export function recallColour(node: GraphNode, fsrsEnabled: boolean): string {
    if (!fsrsEnabled || node.studiedCards === 0) {
        return NO_DATA;
    }
    return redToGreen(node.averageRetrievability);
}
