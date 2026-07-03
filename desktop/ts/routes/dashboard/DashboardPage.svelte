<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { topicMastery } from "@generated/backend";

    import {
        getExamDate,
        getTagTopicMap,
        IGNORE_TOPIC_VALUE,
        isValidExamDate,
        saveExamDate,
        saveTagTopicMap,
    } from "./config";
    import GaugeCard from "./GaugeCard.svelte";
    import { buildDashboardModel, READINESS_GATES } from "./metrics";
    import type { DashboardModel } from "./metrics";
    import SubjectTable from "./SubjectTable.svelte";
    import { EXAM_NAME, EXAM_YEAR, TOPICS, WEIGHTS_SOURCE } from "./topics";

    export let testMode = false;

    let model: DashboardModel | null = null;
    let error: string | null = null;

    /** the persisted tag->topic map, as loaded from collection config */
    let savedMap: Record<string, string> = {};
    /** the copy being edited in the "Map tags" editor */
    let pendingMap: Record<string, string> = {};
    let mapEditorOpen = false;
    let savingMap = false;

    /** the persisted exam date ("" = unset) and the input being edited */
    let examDate = "";
    let examDateInput = "";
    let examDateError: string | null = null;

    async function refresh(): Promise<void> {
        try {
            const [tagTopicMap, storedExamDate] = await Promise.all([
                getTagTopicMap(),
                getExamDate(),
            ]);
            savedMap = tagTopicMap;
            examDate = storedExamDate;
            examDateInput = storedExamDate;
            const response = await topicMastery({
                search: "",
                topicPrefix: "",
                highRecallThreshold: 0,
                tagTopicMap,
            });
            model = buildDashboardModel(response, { testMode });
            error = null;
        } catch (exc) {
            error = String(exc);
        }
    }

    refresh();

    interface MappingRow {
        tag: string;
        /** cards carrying this tag among unmapped cards; null for tags that
         * are already mapped (they no longer show up as unmapped) */
        cards: number | null;
    }

    /** existing mappings first (so they can be changed/removed), then the
     * deck's unmapped tags, most frequent first */
    function editorRows(
        current: DashboardModel | null,
        saved: Record<string, string>,
    ): MappingRow[] {
        const rows: MappingRow[] = Object.keys(saved)
            .sort()
            .map((tag) => ({ tag, cards: null }));
        const seen = new Set(rows.map((row) => row.tag));
        for (const unmapped of current?.unmappedTags ?? []) {
            if (!seen.has(unmapped.tag)) {
                rows.push({ tag: unmapped.tag, cards: unmapped.cards });
            }
        }
        return rows;
    }

    $: mappingRows = mapEditorOpen ? editorRows(model, savedMap) : [];

    function toggleMapEditor(): void {
        if (!mapEditorOpen) {
            pendingMap = { ...savedMap };
        }
        mapEditorOpen = !mapEditorOpen;
    }

    function setPendingMapping(tag: string, topicId: string): void {
        if (topicId === "") {
            const rest = { ...pendingMap };
            delete rest[tag];
            pendingMap = rest;
        } else {
            pendingMap = { ...pendingMap, [tag]: topicId };
        }
    }

    async function saveMappings(): Promise<void> {
        savingMap = true;
        try {
            await saveTagTopicMap(pendingMap);
            await refresh();
            pendingMap = { ...savedMap };
        } finally {
            savingMap = false;
        }
    }

    async function saveExamDateClicked(): Promise<void> {
        const value = examDateInput.trim();
        if (value !== "" && !isValidExamDate(value)) {
            examDateError = "Enter the date as YYYY-MM-DD.";
            return;
        }
        examDateError = null;
        await saveExamDate(value);
        examDate = value;
        examDateInput = value;
    }
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

        <section class="exam-date">
            <label for="exam-date-input">Exam date</label>
            <input id="exam-date-input" type="date" bind:value={examDateInput} />
            <button
                class="small-button"
                on:click={saveExamDateClicked}
                disabled={examDateInput.trim() === examDate}
            >
                Save
            </button>
            {#if examDateError}
                <span class="date-error">{examDateError}</span>
            {:else if examDate === ""}
                <span class="hint">
                    No exam date set — the fade ladder is disabled.
                </span>
            {/if}
        </section>

        <section class="table-section">
            <SubjectTable subjects={model.subjects} />
            {#if model.aigExclusionNote}
                <p class="aig-note">{model.aigExclusionNote}.</p>
            {/if}
            {#if model.cardsWithoutTopic > 0 || Object.keys(savedMap).length > 0}
                <p class="untagged-note">
                    {#if model.cardsWithoutTopic > 0}
                        {model.cardsWithoutTopic} of {model.totalCards} cards have no
                        <code>cfa::topic::*</code>
                        tag or mapped tag and are not counted towards any topic.
                    {:else}
                        All cards are attributed via
                        <code>cfa::topic::*</code>
                        tags or your tag mapping.
                    {/if}
                    <button class="small-button" on:click={toggleMapEditor}>
                        {mapEditorOpen ? "Hide tag mapping" : "Map tags"}
                    </button>
                </p>
            {/if}
            {#if mapEditorOpen}
                <div class="map-editor">
                    <p class="editor-intro">
                        Map your deck's tags onto the {TOPICS.length} topics (read-time only
                        — note tags are never rewritten). Tags left "(unmapped)" stay visible
                        as coverage gaps; "Ignore" drops noise tags from every topic. Counts
                        are per tag: a card with several unmapped tags is listed under each
                        of them.
                    </p>
                    {#if mappingRows.length === 0}
                        <p class="editor-intro">No unmapped tags to show.</p>
                    {:else}
                        <table>
                            <thead>
                                <tr>
                                    <th>Tag</th>
                                    <th>Cards</th>
                                    <th>Topic</th>
                                </tr>
                            </thead>
                            <tbody>
                                {#each mappingRows as row (row.tag)}
                                    <tr>
                                        <td><code>{row.tag}</code></td>
                                        <td>{row.cards ?? "—"}</td>
                                        <td>
                                            <select
                                                value={pendingMap[row.tag] ?? ""}
                                                on:change={(event) =>
                                                    setPendingMapping(
                                                        row.tag,
                                                        event.currentTarget.value,
                                                    )}
                                            >
                                                <option value="">(unmapped)</option>
                                                {#each TOPICS as topic}
                                                    <option value={topic.id}>
                                                        {topic.name}
                                                    </option>
                                                {/each}
                                                <option value={IGNORE_TOPIC_VALUE}>
                                                    Ignore
                                                </option>
                                            </select>
                                        </td>
                                    </tr>
                                {/each}
                            </tbody>
                        </table>
                    {/if}
                    <div class="editor-actions">
                        <button
                            class="small-button"
                            on:click={saveMappings}
                            disabled={savingMap}
                        >
                            {savingMap ? "Saving…" : "Save mapping"}
                        </button>
                    </div>
                </div>
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

    .aig-note {
        font-size: 0.8rem;
        color: var(--fg);
        margin-top: 0.5rem;
    }

    .small-button {
        border: 1px solid var(--border);
        background: var(--canvas-elevated);
        color: var(--fg);
        border-radius: var(--border-radius, 5px);
        padding: 0.15rem 0.6rem;
        font-size: 0.8rem;
        cursor: pointer;

        &:hover:not(:disabled) {
            background: var(--canvas-inset);
        }

        &:disabled {
            opacity: 0.6;
            cursor: default;
        }
    }

    .exam-date {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0.6rem;
        margin: 0 0 1.25rem;
        padding: 0.6rem 1rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);
        font-size: 0.85rem;

        label {
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--fg-subtle);
        }

        input {
            border: 1px solid var(--border);
            background: var(--canvas);
            color: var(--fg);
            border-radius: var(--border-radius, 5px);
            padding: 0.2rem 0.4rem;
        }

        .hint {
            color: var(--fg-subtle);
        }

        .date-error {
            color: var(--accent-danger, #c33);
        }
    }

    .map-editor {
        margin-top: 0.5rem;
        padding: 0.75rem 1rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);

        .editor-intro {
            font-size: 0.8rem;
            color: var(--fg-subtle);
            margin: 0 0 0.6rem;
        }

        table {
            border-collapse: collapse;
            font-size: 0.85rem;

            th {
                text-align: left;
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                color: var(--fg-subtle);
                padding: 0.2rem 1rem 0.2rem 0;
            }

            td {
                padding: 0.2rem 1rem 0.2rem 0;
            }

            select {
                border: 1px solid var(--border);
                background: var(--canvas);
                color: var(--fg);
                border-radius: var(--border-radius, 5px);
                padding: 0.15rem 0.3rem;
            }
        }

        .editor-actions {
            margin-top: 0.6rem;
        }
    }

    footer {
        margin-top: 1.5rem;
        font-size: 0.8rem;
        color: var(--fg-subtle);
        border-top: 1px solid var(--border);
        padding-top: 0.75rem;
    }
</style>
