<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { topicMastery } from "@generated/backend";

    import GaugeCard from "./GaugeCard.svelte";
    import { buildDashboardModel, READINESS_GATES } from "./metrics";
    import type { DashboardModel } from "./metrics";
    import SubjectTable from "./SubjectTable.svelte";
    import { EXAM_NAME, EXAM_YEAR, WEIGHTS_SOURCE } from "./topics";

    export let testMode = false;

    let model: DashboardModel | null = null;
    let error: string | null = null;

    async function refresh(): Promise<void> {
        try {
            const response = await topicMastery({
                search: "",
                topicPrefix: "",
                highRecallThreshold: 0,
            });
            model = buildDashboardModel(response, { testMode });
            error = null;
        } catch (exc) {
            error = String(exc);
        }
    }

    refresh();
</script>

<main>
    <header>
        <div>
            <h1>{EXAM_NAME} · Readiness Dashboard</h1>
            <p class="subtitle">
                Three separate questions, three separate answers - never one blended
                number. Weights: {WEIGHTS_SOURCE} ({EXAM_YEAR}).
            </p>
        </div>
        <button class="refresh" on:click={refresh}>Refresh</button>
    </header>

    {#if testMode}
        <div class="test-banner">
            TEST MODE - give-up gates relaxed; nothing on this page is a real
            prediction.
        </div>
    {/if}

    {#if error}
        <div class="error">{error}</div>
    {:else if model === null}
        <p>Loading…</p>
    {:else}
        <section class="gauges">
            <GaugeCard
                title="Memory"
                question="Can you recall the facts you studied, right now?"
                gauge={model.memory}
            />
            <GaugeCard
                title="Performance"
                question="Would you answer a new, exam-style question that uses them?"
                gauge={model.performance}
            />
            <GaugeCard
                title="Readiness"
                question="What is the probability you would pass today?"
                gauge={model.readiness}
            />
        </section>

        <section class="meta">
            <div class="meta-item">
                <span class="label">Exam coverage (studied)</span>
                <span class="value">{Math.round(model.coverage * 100)}%</span>
            </div>
            <div class="meta-item">
                <span class="label">Coverage in deck</span>
                <span class="value">{Math.round(model.deckCoverage * 100)}%</span>
            </div>
            <div class="meta-item">
                <span class="label">Graded reviews</span>
                <span class="value">{model.gradedReviews}</span>
            </div>
            <div class="meta-item">
                <span class="label">Held-out probes</span>
                <span class="value">{model.heldOutProbes}</span>
            </div>
            {#if model.bestNext}
                <div class="meta-item best-next">
                    <span class="label">Best next thing to study</span>
                    <span class="value">{model.bestNext}</span>
                </div>
            {/if}
            <div class="meta-item">
                <span class="label">Last updated</span>
                <span class="value">
                    {model.generatedAt.toLocaleTimeString()}
                </span>
            </div>
        </section>

        <section class="table-section">
            <SubjectTable subjects={model.subjects} />
            {#if model.cardsWithoutTopic > 0}
                <p class="untagged-note">
                    {model.cardsWithoutTopic} of {model.totalCards} cards have no
                    <code>cfa::topic::*</code>
                     tag and are not counted towards any topic.
                </p>
            {/if}
        </section>

        <footer>
            <p>
                <strong>The give-up rule:</strong>
                Readiness shows no probability until there are at least {READINESS_GATES.minGradedReviews}
                graded reviews, {Math.round(READINESS_GATES.minCoverage * 100)}% topic
                coverage, and {READINESS_GATES.minHeldOutProbes} answered held-out probe
                questions, and the resulting band is usefully narrow. A system that knows
                when it does not know beats a confident guess.
            </p>
        </footer>
    {/if}
</main>

<style lang="scss">
    main {
        max-width: 68rem;
        margin: 0 auto;
        padding: 1.25rem 1.5rem 3rem;
        color: var(--fg);
    }

    header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 1rem;

        h1 {
            font-size: 1.5rem;
            margin: 0;
        }

        .subtitle {
            color: var(--fg-subtle);
            font-size: 0.85rem;
            margin-top: 0.25rem;
        }

        .refresh {
            border: 1px solid var(--border);
            background: var(--canvas-elevated);
            color: var(--fg);
            border-radius: var(--border-radius, 5px);
            padding: 0.35rem 0.9rem;
            cursor: pointer;

            &:hover {
                background: var(--canvas-inset);
            }
        }
    }

    .test-banner {
        margin: 0.75rem 0;
        padding: 0.5rem 0.75rem;
        border: 1px solid var(--accent-danger, #c33);
        color: var(--accent-danger, #c33);
        border-radius: var(--border-radius, 5px);
        font-weight: 600;
        font-size: 0.85rem;
    }

    .error {
        color: var(--accent-danger, #c33);
        margin-top: 1rem;
    }

    .gauges {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
        gap: 0.9rem;
        margin-top: 1.25rem;
    }

    .meta {
        display: flex;
        flex-wrap: wrap;
        gap: 1.5rem;
        margin: 1.25rem 0;
        padding: 0.75rem 1rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);

        .meta-item {
            display: flex;
            flex-direction: column;

            .label {
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                color: var(--fg-subtle);
            }

            .value {
                font-size: 1.05rem;
                font-weight: 600;
            }

            &.best-next .value {
                color: var(--accent-card, #3b82f6);
            }
        }
    }

    .table-section {
        overflow-x: auto;
    }

    .untagged-note {
        font-size: 0.8rem;
        color: var(--fg-subtle);
        margin-top: 0.5rem;
    }

    footer {
        margin-top: 1.5rem;
        font-size: 0.8rem;
        color: var(--fg-subtle);
        border-top: 1px solid var(--border);
        padding-top: 0.75rem;
    }
</style>
