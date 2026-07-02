<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { conceptGraph } from "@generated/backend";
    import { onMount } from "svelte";

    import {
        buildGraphModel,
        difficultyColour,
        type GraphLink,
        type GraphNode,
        recallColour,
    } from "./graph";
    import { runSimulation } from "./simulation";

    export let deckId = 0n;

    type ColourMode = "difficulty" | "recall";

    let colourMode: ColourMode = "difficulty";
    let nodes: GraphNode[] = [];
    let links: GraphLink[] = [];
    let fsrsEnabled = false;
    let error: string | null = null;
    let loaded = false;

    const width = 1200;
    const height = 800;

    let viewGroup: SVGGElement;
    let svgElement: SVGSVGElement;
    // auto-fitted to the node extents while the simulation settles, frozen
    // once the user starts zooming/panning
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
        const pad = 60;
        viewBox = `${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${
            maxY - minY + pad * 2
        }`;
    }

    onMount(async () => {
        try {
            const response = await conceptGraph({ deckId });
            fsrsEnabled = response.fsrsEnabled;
            const model = buildGraphModel(response, width, height);
            nodes = model.nodes;
            links = model.links;
            loaded = true;
            runSimulation(nodes, links, width, height, () => {
                nodes = nodes;
                links = links;
                fitViewBox();
            });
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

    function colourOf(node: GraphNode): string {
        return colourMode === "difficulty"
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
        return `${node.tag}\n${node.cardCount} cards\n${difficulty}\n${recall}`;
    }
</script>

<main>
    <header>
        <div>
            <h1>Concept map</h1>
            <p class="subtitle">
                One node per tag; an edge when two tags share a note. Node size = cards;
                colour is honest: grey means "no data yet", never a guess.
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
                {#each nodes as node}
                    <g
                        class="node"
                        transform="translate({node.x}, {node.y})"
                        role="img"
                    >
                        <circle r={node.radius} fill={colourOf(node)}>
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

        .node {
            circle {
                stroke: var(--canvas);
                stroke-width: 1.5;
            }

            text {
                font-size: 11px;
                text-anchor: middle;
                fill: var(--fg-subtle);
                pointer-events: none;
                user-select: none;
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
