// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/** d3-force wiring for the concept map. */

import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3";

import type { GraphLink, GraphNode } from "./graph";

/**
 * Run a force simulation over the nodes in place, invoking `onTick` so the
 * Svelte template re-renders as positions settle.
 *
 * The map is typically a set of disconnected islands (one per topic), so we
 * use weak x/y gravity towards the centre rather than `forceCenter`: the
 * latter only pins the centroid and lets islands drift out of the viewport.
 */
export function runSimulation(
    nodes: GraphNode[],
    links: GraphLink[],
    width: number,
    height: number,
    onTick: () => void,
): void {
    const simulation = forceSimulation(nodes as any)
        .force(
            "link",
            forceLink(links as any)
                .distance((link: any) => 80 - Math.min(link.noteCount * 6, 40))
                .strength(0.7),
        )
        .force("charge", forceManyBody().strength(-160).distanceMax(320))
        .force("x", forceX(width / 2).strength(0.14))
        .force("y", forceY(height / 2).strength(0.2))
        .force(
            "collide",
            forceCollide((node: any) => (node as GraphNode).radius + 18),
        );
    simulation.on("tick", onTick);
}
