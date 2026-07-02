<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import type { SubjectRow } from "./metrics";

    export let subjects: SubjectRow[];

    $: rows = [...subjects].sort((a, b) => b.weightedGap - a.weightedGap);

    function pct(x: number | null): string {
        return x === null ? "—" : `${Math.round(x * 100)}%`;
    }
</script>

<table class="subjects">
    <thead>
        <tr>
            <th class="name">Topic (sorted by weighted gap)</th>
            <th>Exam weight</th>
            <th>Cards</th>
            <th>Studied</th>
            <th class="memory-col">Memory (recall probability)</th>
            <th>High recall</th>
            <th>Performance*</th>
        </tr>
    </thead>
    <tbody>
        {#each rows as row}
            <tr class:uncovered={row.studiedCards === 0}>
                <td class="name">{row.topic.name}</td>
                <td>{row.topic.min}–{row.topic.max}% (mid {row.topic.midpoint}%)</td>
                <td>{row.totalCards}</td>
                <td>{row.studiedCards}</td>
                <td class="memory-col">
                    {#if row.memory !== null}
                        <div class="bar-outer">
                            <div
                                class="bar-inner"
                                style:width={`${Math.round(row.memory * 100)}%`}
                            ></div>
                        </div>
                        <span class="bar-label">
                            {pct(row.memory)}
                            {#if row.memoryRange}
                                <span class="range-label">
                                    ({pct(row.memoryRange.low)}–{pct(
                                        row.memoryRange.high,
                                    )})
                                </span>
                            {/if}
                        </span>
                    {:else}
                        <span class="abstain">no data</span>
                    {/if}
                </td>
                <td>
                    {#if row.studiedCards > 0}
                        {row.highRecallCards}/{row.studiedCards}
                    {:else}
                        —
                    {/if}
                </td>
                <td>
                    {#if row.performance !== null}
                        {pct(row.performance)}
                    {:else}
                        <span class="abstain">—</span>
                    {/if}
                </td>
            </tr>
        {/each}
    </tbody>
</table>
<p class="footnote">
    *Performance = Memory x a documented transfer factor; an uncalibrated estimate until
    held-out exam-style questions exist. "High recall" counts cards at or above 90%
    predicted retrievability - a scheduling target, not a claim of mastery.
</p>

<style lang="scss">
    .subjects {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;

        th,
        td {
            text-align: left;
            padding: 0.4rem 0.6rem;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }

        th {
            color: var(--fg-subtle);
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .name {
            white-space: normal;
            min-width: 12rem;
        }

        .memory-col {
            min-width: 12rem;
        }

        tr.uncovered td {
            opacity: 0.65;
        }
    }

    .bar-outer {
        background: var(--canvas-inset);
        border: 1px solid var(--border);
        border-radius: 4px;
        height: 0.5rem;
        width: 100%;
        overflow: hidden;
        margin-bottom: 0.15rem;
    }

    .bar-inner {
        background: var(--accent-card, #3b82f6);
        height: 100%;
    }

    .bar-label {
        font-variant-numeric: tabular-nums;
    }

    .range-label,
    .abstain,
    .footnote {
        color: var(--fg-subtle);
    }

    .footnote {
        font-size: 0.75rem;
        margin-top: 0.6rem;
    }
</style>
