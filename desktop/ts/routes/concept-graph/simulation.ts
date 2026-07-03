// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

/** d3-force wiring for the concept map. */

import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, packSiblings } from "d3";

import type { GraphLink, GraphNode } from "./graph";

/**
 * Collision radius: the node circle plus half of the label drawn beneath
 * it, so neighbouring subtopic names stay legible instead of stacking on
 * top of each other.
 */
export function collideRadius(node: GraphNode): number {
    const halfLabelWidth = Math.min(node.label.length * 3.4, 80);
    return Math.max(node.radius, halfLabelWidth) + 14;
}

interface Island {
    nodes: GraphNode[];
    /** canonical topic id when this island is a dashboard topic cluster */
    topicId: string | null;
    /** estimated radius of the settled node cloud itself */
    cloud: number;
    /** packing radius: the cloud plus a gutter between islands */
    r: number;
    x: number;
    y: number;
}

/**
 * Group nodes into layout islands:
 *
 * - one island per canonical topic (tags attributed via `cfa::topic::*`
 *   or the dashboard's tag->topic map), so similar tags cluster under
 *   their topic regardless of note co-occurrence;
 * - the remaining ungrouped tags fall back to connected components over
 *   the links among themselves (typically one component per organic
 *   cluster of co-occurring tags).
 */
function groupIntoIslands(
    nodes: GraphNode[],
    links: GraphLink[],
): GraphNode[][] {
    const topicGroups = new Map<string, GraphNode[]>();
    const ungrouped: GraphNode[] = [];
    for (const node of nodes) {
        if (node.topicId !== null) {
            const group = topicGroups.get(node.topicId);
            if (group) {
                group.push(node);
            } else {
                topicGroups.set(node.topicId, [node]);
            }
        } else {
            ungrouped.push(node);
        }
    }

    const neighbours = new Map<GraphNode, GraphNode[]>();
    for (const node of ungrouped) {
        neighbours.set(node, []);
    }
    for (const link of links) {
        // only links between two ungrouped nodes bind components; topic
        // membership always wins over co-occurrence
        if (neighbours.has(link.source) && neighbours.has(link.target)) {
            neighbours.get(link.source)!.push(link.target);
            neighbours.get(link.target)!.push(link.source);
        }
    }
    const islands: GraphNode[][] = [...topicGroups.values()];
    const seen = new Set<GraphNode>();
    for (const start of ungrouped) {
        if (seen.has(start)) {
            continue;
        }
        const members: GraphNode[] = [];
        const queue = [start];
        seen.add(start);
        while (queue.length) {
            const node = queue.pop()!;
            members.push(node);
            for (const next of neighbours.get(node) ?? []) {
                if (!seen.has(next)) {
                    seen.add(next);
                    queue.push(next);
                }
            }
        }
        islands.push(members);
    }
    return islands;
}

/** Rough estimate of the space an island's contents need once settled. */
function islandRadius(members: GraphNode[]): number {
    const area = members.reduce(
        (total, node) => total + collideRadius(node) ** 2,
        0,
    );
    return Math.sqrt(area) * 1.9 + 30;
}

/**
 * Breathing room added around an island when packing. The gap between two
 * clusters must clearly exceed the node spacing inside them, or the
 * grouping stops being visible. It scales with the cluster itself - the
 * moat around a cluster is roughly half its width again on each side -
 * so separation reads the same at every zoom level, while lone tags keep
 * a modest floor and stay reasonably compact.
 */
function islandGutter(cloudRadius: number): number {
    return Math.max(cloudRadius * 0.55, 45);
}

/**
 * Lay the graph out in place, synchronously.
 *
 * Three departures from a plain force layout, all for readability:
 *
 * - Tags that resolve to a dashboard topic (canonical `cfa::topic::*`
 *   tags, plus the user's tag->topic map) are clustered under that topic;
 *   only the leftover tags group by note co-occurrence.
 * - Every island gets its own gravity centre (packed via `packSiblings`)
 *   instead of one shared centre that crushes them into a single clump.
 *   Links that cross islands are kept long and weak so they inform the
 *   eye without dragging clusters back together.
 * - The simulation is run to rest *before* the first paint rather than
 *   animating, so the page shows the settled map instead of nodes flying
 *   around while it loads.
 */
export function computeLayout(
    nodes: GraphNode[],
    links: GraphLink[],
    width: number,
    height: number,
): void {
    const islands: Island[] = groupIntoIslands(nodes, links).map((members) => {
        const cloudRadius = islandRadius(members);
        return {
            nodes: members,
            topicId: members[0].topicId,
            cloud: cloudRadius,
            r: cloudRadius + islandGutter(cloudRadius),
            x: 0,
            y: 0,
        };
    });
    // largest first packs more compactly
    islands.sort((a, b) => b.r - a.r);
    packSiblings(islands);

    const islandOf = new Map<GraphNode, Island>();
    for (const island of islands) {
        island.x += width / 2;
        island.y += height / 2;
        island.nodes.forEach((node, index) => {
            islandOf.set(node, island);
            // deterministic starting ring inside the cloud (not the moat),
            // so reloads settle identically
            const angle = (2 * Math.PI * index) / island.nodes.length;
            const spread = island.cloud * 0.5;
            node.x = island.x + Math.cos(angle) * spread;
            node.y = island.y + Math.sin(angle) * spread;
        });
    }
    const sameIsland = (link: GraphLink): boolean => islandOf.get(link.source) === islandOf.get(link.target);

    const simulation = forceSimulation(nodes as any)
        .force(
            "link",
            forceLink(links as any)
                .distance((link: any) => sameIsland(link) ? 90 - Math.min(link.noteCount * 5, 30) : 260)
                .strength((link: any) => (sameIsland(link) ? 0.5 : 0.04)),
        )
        .force("charge", forceManyBody().strength(-240).distanceMax(380))
        // firm enough that clusters stay tight against the wider moats
        .force("x", forceX((node: any) => islandOf.get(node)!.x).strength(0.09))
        .force("y", forceY((node: any) => islandOf.get(node)!.y).strength(0.09))
        .force(
            "collide",
            forceCollide((node: any) => collideRadius(node as GraphNode)).iterations(2),
        )
        .stop();

    // d3's documented static-layout loop: step until the simulation would
    // have stopped on its own (~300 ticks at the default decay)
    const ticks = Math.ceil(
        Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()),
    );
    for (let i = 0; i < ticks; i++) {
        simulation.tick();
    }
}
