<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { conceptGraph } from "@generated/backend";
    import { onMount } from "svelte";

    import { getTagTopicMap } from "../dashboard/config";
    import {
        buildGraphModel,
        difficultyColour,
        type GraphLink,
        type GraphNode,
        recallColour,
    } from "./graph";
    import { topicLabel } from "./grouping";
    import { computeLayout } from "./simulation";

    export let deckId = 0n;

    type ColourMode = "difficulty" | "recall";

    let colourMode: ColourMode = "difficulty";
    let nodes: GraphNode[] = [];
    let links: GraphLink[] = [];
    let fsrsEnabled = false;
    let error: string | null = null;
    let loaded = false;

    /** heading drawn over each dashboard-topic cluster */
    interface TopicHeading {
        id: string;
        name: string;
        x: number;
        y: number;
    }
    let topicHeadings: TopicHeading[] = [];

    /** centred above each topic cluster once the layout has settled */
    function computeTopicHeadings(settled: GraphNode[]): TopicHeading[] {
        const byTopic = new Map<string, GraphNode[]>();
        for (const node of settled) {
            if (node.topicId !== null) {
                const group = byTopic.get(node.topicId);
                if (group) {
                    group.push(node);
                } else {
                    byTopic.set(node.topicId, [node]);
                }
            }
        }
        return [...byTopic.entries()].map(([id, members]) => ({
            id,
            name: topicLabel(id),
            x: members.reduce((sum, node) => sum + node.x, 0) / members.length,
            y: Math.min(...members.map((node) => node.y - node.radius)) - 30,
        }));
    }

    const width = 1200;
    const height = 800;

    let viewGroup: SVGGElement;
    let svgElement: SVGSVGElement;
    // fitted to the node extents once the layout is computed, frozen once
    // the user starts zooming/panning
    let viewBox = `0 0 ${width} ${height}`;
    let userNavigated = false;

    function fitViewBox(): void {
        if (userNavigated || nodes.length === 0) {
            return;
        }
        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;
        for (const node of nodes) {
            minX = Math.min(minX, node.x - node.radius);
            maxX = Math.max(maxX, node.x + node.radius);
            minY = Math.min(minY, node.y - node.radius);
            maxY = Math.max(maxY, node.y + node.radius);
        }
        for (const heading of topicHeadings) {
            // approximate extents of the 20px uppercase heading text so
            // long topic names are not clipped at the view edge
            const halfWidth = heading.name.length * 7.5;
            minX = Math.min(minX, heading.x - halfWidth);
            maxX = Math.max(maxX, heading.x + halfWidth);
            minY = Math.min(minY, heading.y - 21);
        }
        const pad = 60;
        viewBox = `${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${
            maxY - minY + pad * 2
        }`;
    }

    onMount(async () => {
        try {
            const [response, tagTopicMap] = await Promise.all([
                conceptGraph({ deckId }),
                getTagTopicMap(),
            ]);
            fsrsEnabled = response.fsrsEnabled;
            const model = buildGraphModel(response, width, height, tagTopicMap);
            // settle the layout before the first paint - the map appears
            // in its final position instead of jostling into place
            computeLayout(model.nodes, model.links, width, height);
            nodes = model.nodes;
            links = model.links;
            topicHeadings = computeTopicHeadings(nodes);
            loaded = true;
            fitViewBox();
            const { select, zoom } = await import("d3");
            const group = select(viewGroup);
            select(svgElement).call(
                zoom<SVGSVGElement, unknown>()
                    .scaleExtent([0.25, 6])
                    .on("zoom", (event) => {
                        userNavigated = true;
                        group.attr("transform", event.transform.toString());
                    }) as any,
            );
        } catch (exc) {
            error = String(exc);
        }
    });

    // `mode` is a parameter (not read from component state inside the
    // function) so the template visibly depends on it and re-colours on
    // toggle under any reactivity semantics
    function colourOf(node: GraphNode, mode: ColourMode): string {
        return mode === "difficulty"
            ? difficultyColour(node)
            : recallColour(node, fsrsEnabled);
    }

    function tooltip(node: GraphNode): string {
        const difficulty = node.gradedAnswers
            ? `${Math.round((node.againHardAnswers / node.gradedAnswers) * 100)}% Again/Hard over ${node.gradedAnswers} answers`
            : "no graded answers yet";
        const recall = node.studiedCards
            ? `${Math.round(node.averageRetrievability * 100)}% mean recall over ${node.studiedCards} studied cards`
            : "no FSRS memory state yet";
        const topic =
            node.topicId === null ? "" : `\ntopic: ${topicLabel(node.topicId)}`;
        return `${node.tag}\n${node.cardCount} cards\n${difficulty}\n${recall}${topic}`;
    }
</script>

<main>
    <header>
        <div>
            <h1>Concept map</h1>
            <p class="subtitle">
                One node per tag; an edge when two tags share a note. Node size = cards;
                colour is honest: grey means "no data yet", never a guess. Tags
                attributed to a topic (via <code>cfa::topic::*</code>
                 or the dashboard's tag mapping) cluster under that topic's heading.
            </p>
        </div>
        <div class="controls">
            <button
                class:active={colourMode === "difficulty"}
                on:click={() => (colourMode = "difficulty")}
            >
                Colour by answer difficulty
            </button>
            <button
                class:active={colourMode === "recall"}
                on:click={() => (colourMode = "recall")}
                title={fsrsEnabled ? "" : "FSRS is disabled; recall colouring abstains"}
            >
                Colour by FSRS recall
            </button>
        </div>
    </header>

    <div class="legend">
        {#if colourMode === "difficulty"}
            <span>
                <i class="swatch" style:background="#22c55e"></i>
                answered well
            </span>
            <span>
                <i class="swatch" style:background="#ef4444"></i>
                often Again/Hard
            </span>
        {:else}
            <span>
                <i class="swatch" style:background="#ef4444"></i>
                low predicted recall
            </span>
            <span>
                <i class="swatch" style:background="#22c55e"></i>
                high predicted recall
            </span>
        {/if}
        <span>
            <i class="swatch" style:background="#9ca3af"></i>
            no data yet
        </span>
        <span class="hint">
            scroll to zoom · drag to pan · hover a node for details
        </span>
    </div>

    {#if error}
        <div class="error">{error}</div>
    {:else if !loaded}
        <p>Loading…</p>
    {:else if nodes.length === 0}
        <p class="empty">
            No tags found. Import a tagged deck (e.g. the CFA sample deck) to see the
            knowledge map.
        </p>
    {:else}
        <svg bind:this={svgElement} {viewBox} preserveAspectRatio="xMidYMid meet">
            <g bind:this={viewGroup}>
                {#each links as link}
                    <line
                        x1={link.source.x}
                        y1={link.source.y}
                        x2={link.target.x}
                        y2={link.target.y}
                        stroke-width={Math.min(1 + link.noteCount * 0.6, 6)}
                    />
                {/each}
                {#each topicHeadings as heading (heading.id)}
                    <text class="topic-heading" x={heading.x} y={heading.y}>
                        {heading.name}
                    </text>
                {/each}
                {#each nodes as node}
                    <g
                        class="node"
                        transform="translate({node.x}, {node.y})"
                        role="img"
                    >
                        <circle r={node.radius} fill={colourOf(node, colourMode)}>
                            <title>{tooltip(node)}</title>
                        </circle>
                        <text dy={node.radius + 14}>{node.label}</text>
                    </g>
                {/each}
            </g>
        </svg>
    {/if}
</main>

<style lang="scss">
    main {
        max-width: 80rem;
        margin: 0 auto;
        padding: 1rem 1.5rem 2rem;
        color: var(--fg);
    }

    header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 1rem;
        flex-wrap: wrap;

        h1 {
            font-size: 1.4rem;
            margin: 0;
        }

        .subtitle {
            color: var(--fg-subtle);
            font-size: 0.85rem;
            margin-top: 0.25rem;
            max-width: 40rem;
        }
    }

    .controls button {
        border: 1px solid var(--border);
        background: var(--canvas-elevated);
        color: var(--fg);
        border-radius: var(--border-radius, 5px);
        padding: 0.35rem 0.8rem;
        cursor: pointer;

        &.active {
            background: var(--accent-card, #3b82f6);
            border-color: var(--accent-card, #3b82f6);
            color: white;
        }
    }

    .legend {
        display: flex;
        gap: 1.25rem;
        align-items: center;
        flex-wrap: wrap;
        font-size: 0.8rem;
        color: var(--fg-subtle);
        margin: 0.75rem 0;

        .swatch {
            display: inline-block;
            width: 0.7rem;
            height: 0.7rem;
            border-radius: 50%;
            margin-right: 0.3rem;
            vertical-align: -1px;
        }

        .hint {
            margin-left: auto;
        }
    }

    svg {
        width: 100%;
        height: calc(100vh - 12rem);
        min-height: 30rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);

        line {
            stroke: var(--border);
            stroke-opacity: 0.7;
        }

        .topic-heading {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            text-anchor: middle;
            fill: var(--fg-subtle);
            pointer-events: none;
            user-select: none;
            paint-order: stroke;
            stroke: var(--canvas-elevated);
            stroke-width: 4px;
            stroke-linejoin: round;
        }

        .node {
            circle {
                stroke: var(--canvas);
                stroke-width: 1.5;
            }

            text {
                font-size: 12px;
                text-anchor: middle;
                fill: var(--fg);
                pointer-events: none;
                user-select: none;
                // halo so names stay legible where edges pass underneath
                paint-order: stroke;
                stroke: var(--canvas-elevated);
                stroke-width: 3px;
                stroke-linejoin: round;
            }
        }
    }

    .error {
        color: var(--accent-danger, #c33);
    }

    .empty {
        color: var(--fg-subtle);
    }
</style>
