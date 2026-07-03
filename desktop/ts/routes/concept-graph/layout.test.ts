// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { expect, test } from "vitest";

import type { GraphLink, GraphNode } from "./graph";
import { collideRadius, computeLayout } from "./simulation";

const WIDTH = 1200;
const HEIGHT = 800;

function makeNode(
    id: number,
    label: string,
    cardCount = 4,
    topicId: string | null = null,
): GraphNode {
    return {
        id,
        tag: `reading::${label}`,
        label,
        topicId,
        cardCount,
        studiedCards: 0,
        averageRetrievability: 0,
        gradedAnswers: 0,
        againHardAnswers: 0,
        radius: 6 + Math.sqrt(cardCount) * 3,
        x: 0,
        y: 0,
    };
}

function makeLink(source: GraphNode, target: GraphNode, noteCount = 2): GraphLink {
    return { source, target, noteCount };
}

/** Two 4-node topic islands plus a lone tag, like a small tagged deck. */
function buildFixture(): { nodes: GraphNode[]; links: GraphLink[] } {
    const a = [0, 1, 2, 3].map((i) => makeNode(i, `alpha_${i}`));
    const b = [4, 5, 6, 7].map((i) => makeNode(i, `beta_${i}`));
    const lone = makeNode(8, "gamma");
    const links = [
        makeLink(a[0], a[1]),
        makeLink(a[1], a[2]),
        makeLink(a[2], a[3]),
        makeLink(a[3], a[0]),
        makeLink(b[0], b[1]),
        makeLink(b[1], b[2]),
        makeLink(b[2], b[3]),
    ];
    return { nodes: [...a, ...b, lone], links };
}

function distance(a: GraphNode, b: GraphNode): number {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

function centroid(nodes: GraphNode[]): { x: number; y: number } {
    return {
        x: nodes.reduce((sum, node) => sum + node.x, 0) / nodes.length,
        y: nodes.reduce((sum, node) => sum + node.y, 0) / nodes.length,
    };
}

test("layout finishes settled, with no overlapping nodes", () => {
    const { nodes, links } = buildFixture();
    computeLayout(nodes, links, WIDTH, HEIGHT);

    for (const node of nodes) {
        expect(Number.isFinite(node.x)).toBe(true);
        expect(Number.isFinite(node.y)).toBe(true);
    }

    // every pair keeps enough room for both circles and labels (small
    // slack for not-quite-converged collisions)
    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
            const required = (collideRadius(nodes[i]) + collideRadius(nodes[j])) * 0.85;
            expect(distance(nodes[i], nodes[j])).toBeGreaterThan(required);
        }
    }
});

test("disconnected topics land on separate centres instead of one clump", () => {
    const { nodes, links } = buildFixture();
    computeLayout(nodes, links, WIDTH, HEIGHT);

    const islandA = nodes.slice(0, 4);
    const islandB = nodes.slice(4, 8);
    const centreA = centroid(islandA);
    const centreB = centroid(islandB);
    const centreGap = Math.hypot(centreA.x - centreB.x, centreA.y - centreB.y);
    expect(centreGap).toBeGreaterThan(400);

    // each island stays together: nodes sit closer to their own centroid
    // than to the other island's
    for (const node of islandA) {
        const own = Math.hypot(node.x - centreA.x, node.y - centreA.y);
        const other = Math.hypot(node.x - centreB.x, node.y - centreB.y);
        expect(own).toBeLessThan(other);
    }
});

test("layout is deterministic across reloads", () => {
    const first = buildFixture();
    computeLayout(first.nodes, first.links, WIDTH, HEIGHT);
    const second = buildFixture();
    computeLayout(second.nodes, second.links, WIDTH, HEIGHT);

    for (let i = 0; i < first.nodes.length; i++) {
        expect(second.nodes[i].x).toBeCloseTo(first.nodes[i].x, 6);
        expect(second.nodes[i].y).toBeCloseTo(first.nodes[i].y, 6);
    }
});

test("tags mapped to the same topic cluster together, even without links", () => {
    // four fixed-income tags and four economics tags that never co-occur
    // on a note, plus one co-occurrence edge crossing the two topics
    const fi = [0, 1, 2, 3].map((i) => makeNode(i, `bond_${i}`, 4, "fixed_income"));
    const econ = [4, 5, 6, 7].map((i) => makeNode(i, `macro_${i}`, 4, "economics"));
    const nodes = [...fi, ...econ];
    const links = [makeLink(fi[0], econ[0])];
    computeLayout(nodes, links, WIDTH, HEIGHT);

    const centreFi = centroid(fi);
    const centreEcon = centroid(econ);
    const gap = Math.hypot(centreFi.x - centreEcon.x, centreFi.y - centreEcon.y);
    expect(gap).toBeGreaterThan(400);

    // every tag sits with its own topic, including the linked pair - the
    // cross-topic edge must not drag them out of their clusters
    for (const node of nodes) {
        const own = node.topicId === "fixed_income" ? centreFi : centreEcon;
        const other = node.topicId === "fixed_income" ? centreEcon : centreFi;
        const ownDist = Math.hypot(node.x - own.x, node.y - own.y);
        const otherDist = Math.hypot(node.x - other.x, node.y - other.y);
        expect(ownDist).toBeLessThan(otherDist);
    }

    // the moat between clusters must clearly exceed the node spacing
    // inside them, or the grouping is not visible at a glance
    const nearestSameCluster = (node: GraphNode, cluster: GraphNode[]): number =>
        Math.min(
            ...cluster.filter((other) => other !== node).map((other) => distance(node, other)),
        );
    const maxIntraSpacing = Math.max(
        ...fi.map((node) => nearestSameCluster(node, fi)),
        ...econ.map((node) => nearestSameCluster(node, econ)),
    );
    const minCrossDistance = Math.min(
        ...fi.flatMap((a) => econ.map((b) => distance(a, b))),
    );
    expect(minCrossDistance).toBeGreaterThan(maxIntraSpacing * 2.5);
});

test("topic membership beats co-occurrence for grouped tags", () => {
    // a topic tag heavily linked to unmapped tags stays with its topic
    // cluster; the unmapped tags form their own component island
    const topicTag = makeNode(0, "fixed_income", 9, "fixed_income");
    const sibling = makeNode(1, "duration", 4, "fixed_income");
    const readings = [2, 3, 4].map((i) => makeNode(i, `reading_${i}`));
    const nodes = [topicTag, sibling, ...readings];
    const links = readings.map((reading) => makeLink(topicTag, reading, 5));
    // readings also co-occur with each other, forming one component
    links.push(makeLink(readings[0], readings[1]), makeLink(readings[1], readings[2]));
    computeLayout(nodes, links, WIDTH, HEIGHT);

    const topicCentre = centroid([topicTag, sibling]);
    const readingCentre = centroid(readings);
    const topicDist = Math.hypot(topicTag.x - topicCentre.x, topicTag.y - topicCentre.y);
    const readingDist = Math.hypot(
        topicTag.x - readingCentre.x,
        topicTag.y - readingCentre.y,
    );
    expect(topicDist).toBeLessThan(readingDist);
});
