<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import type { Gauge } from "./metrics";

    export let title: string;
    export let question: string;
    export let gauge: Gauge;
    /** §4 contract: every score carries exam coverage + its update time. */
    export let coverage: number | null = null;
    export let updatedAt: Date | null = null;

    function pct(x: number): string {
        return `${Math.round(x * 100)}%`;
    }
</script>

<div class="gauge-card" class:abstaining={gauge.kind === "abstain"}>
    <div class="header">
        <h2>{title}</h2>
        {#if gauge.badge}
            <span class="badge" class:test={gauge.kind === "test"}>{gauge.badge}</span>
        {/if}
    </div>
    <p class="question">{question}</p>

    {#if gauge.kind === "abstain"}
        <div class="value abstain">no score</div>
        <p class="abstain-label">Not enough data — and this app does not guess.</p>
        <ul class="missing">
            {#each gauge.missing as item}
                <li>{item}</li>
            {/each}
        </ul>
    {:else}
        <div class="value">
            {pct(gauge.value ?? 0)}
        </div>
        {#if gauge.range}
            <div class="range">
                likely range {pct(gauge.range.low)} – {pct(gauge.range.high)}
            </div>
        {/if}
        <div class="confidence confidence-{gauge.confidence}">
            confidence: {gauge.confidence}
        </div>
        {#if gauge.reasons.length}
            <ul class="reasons">
                {#each gauge.reasons as reason}
                    <li>{reason}</li>
                {/each}
            </ul>
        {/if}
        {#if gauge.missing.length}
            <ul class="missing">
                {#each gauge.missing as item}
                    <li>{item}</li>
                {/each}
            </ul>
        {/if}
    {/if}
    {#if coverage !== null || updatedAt !== null}
        <div class="card-meta">
            {#if coverage !== null}
                <span>exam covered: {pct(coverage)}</span>
            {/if}
            {#if updatedAt !== null}
                <span>updated {updatedAt.toLocaleTimeString()}</span>
            {/if}
        </div>
    {/if}
</div>

<style lang="scss">
    .gauge-card {
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);
        padding: 1rem 1.25rem;
        display: flex;
        flex-direction: column;
        min-width: 0;

        &.abstaining {
            border-style: dashed;
        }
    }

    .header {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;

        h2 {
            font-size: 1.1rem;
            margin: 0;
        }
    }

    .badge {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        border: 1px solid var(--border);
        color: var(--fg-subtle);
        white-space: nowrap;

        &.test {
            border-color: var(--accent-danger, #c33);
            color: var(--accent-danger, #c33);
        }
    }

    .question {
        color: var(--fg-subtle);
        font-size: 0.85rem;
        margin: 0.25rem 0 0.75rem;
        min-height: 2.2em;
    }

    .value {
        font-size: 2.4rem;
        font-weight: 700;
        line-height: 1.1;

        &.abstain {
            font-size: 1.6rem;
            color: var(--fg-subtle);
        }
    }

    .abstain-label {
        color: var(--fg-subtle);
        font-size: 0.85rem;
        margin: 0.35rem 0 0.5rem;
    }

    .range {
        margin-top: 0.25rem;
        font-size: 0.9rem;
        color: var(--fg-subtle);
    }

    .confidence {
        margin-top: 0.35rem;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;

        &.confidence-low {
            color: var(--accent-danger, #c33);
        }
        &.confidence-medium {
            color: var(--fg-subtle);
        }
        &.confidence-high {
            color: var(--fg);
        }
    }

    ul {
        margin: 0.6rem 0 0;
        padding-left: 1.1rem;
        font-size: 0.8rem;
        color: var(--fg-subtle);

        li {
            margin-bottom: 0.25rem;
        }
    }

    .missing li {
        color: var(--fg);
    }

    .card-meta {
        margin-top: auto;
        padding-top: 0.6rem;
        display: flex;
        justify-content: space-between;
        gap: 0.5rem;
        font-size: 0.7rem;
        color: var(--fg-subtle);
    }
</style>
