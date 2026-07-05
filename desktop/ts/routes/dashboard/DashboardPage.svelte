<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { getReadiness, topicMastery } from "@generated/backend";

    import {
        coachFacts,
        fetchAssistantStatus,
        mergeSuggestions,
        requestCoach,
        requestDebrief,
        requestTagSuggestions,
    } from "./assistant";
    import type {
        AssistantStatus,
        CoachResult,
        DebriefResult,
        TagSuggestion,
    } from "./assistant";
    import {
        AI_BACKEND_CHOICES,
        getExamDate,
        getTagTopicMap,
        IGNORE_TOPIC_VALUE,
        isValidExamDate,
        saveAiAssistFlag,
        saveAiBackend,
        saveExamDate,
        saveTagTopicMap,
    } from "./config";
    import type { AiBackendChoice } from "./config";
    import GaugeCard from "./GaugeCard.svelte";
    import { buildDashboardModel, READINESS_GATES } from "./metrics";
    import type { DashboardModel } from "./metrics";
    import SubjectTable from "./SubjectTable.svelte";
    import { EXAM_NAME, EXAM_YEAR, TOPICS, WEIGHTS_SOURCE } from "./topics";

    export let testMode = false;

    let model: DashboardModel | null = null;
    let error: string | null = null;

    /** The desktop assistant bridge, or null when absent (Android / AI
     * unavailable) - with null, no AI affordance renders at all. */
    let assistant: AssistantStatus | null = null;

    let debriefLoading = false;
    let debriefResult: DebriefResult | null = null;
    let debriefError: string | null = null;

    let coachOpen = false;
    let coachLoading = false;
    let coachResult: CoachResult | null = null;
    let coachError: string | null = null;

    let suggestLoading = false;
    let suggestNote: string | null = null;
    let suggestDisclosure: string | null = null;
    /** per-tag confidence of the last accepted suggestions, for display */
    let suggestionInfo: Record<string, TagSuggestion> = {};

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
            const [tagTopicMap, storedExamDate, status] = await Promise.all([
                getTagTopicMap(),
                getExamDate(),
                fetchAssistantStatus(),
            ]);
            savedMap = tagTopicMap;
            examDate = storedExamDate;
            examDateInput = storedExamDate;
            assistant = status;
            const [response, readiness] = await Promise.all([
                topicMastery({
                    search: "",
                    topicPrefix: "",
                    highRecallThreshold: 0,
                    tagTopicMap,
                }),
                // the readiness math + give-up gate live in the Rust
                // backend; an unreachable backend means the gauge abstains
                // (never a locally-computed fallback number)
                getReadiness({ testMode, tagTopicMap }).catch(() => null),
            ]);
            model = buildDashboardModel(response, { testMode, readiness });
            error = null;
        } catch (exc) {
            error = String(exc);
        }
    }

    refresh();

    // A feature is live only when the bridge exists, the assistant package
    // imported, the master switch is on AND the feature's own flag is on.
    $: aiOn = assistant !== null && assistant.available && assistant.aiAssist;
    $: debriefOn = aiOn && assistant!.debriefEnabled;
    $: coachOn = aiOn && assistant!.coachEnabled;
    $: suggestOn = aiOn && assistant!.tagSuggestEnabled;

    async function setAiFlag(
        flag: "aiAssist" | "debriefEnabled" | "coachEnabled" | "tagSuggestEnabled",
        value: boolean,
    ): Promise<void> {
        await saveAiAssistFlag(flag, value);
        assistant = await fetchAssistantStatus();
    }

    async function setAiBackend(value: string): Promise<void> {
        await saveAiBackend(value as AiBackendChoice);
        assistant = await fetchAssistantStatus();
    }

    async function generateDebrief(): Promise<void> {
        debriefLoading = true;
        debriefError = null;
        try {
            debriefResult = await requestDebrief();
        } catch (exc) {
            debriefResult = null;
            debriefError = String(exc);
        } finally {
            debriefLoading = false;
        }
    }

    async function askCoach(): Promise<void> {
        if (!model) {
            return;
        }
        coachLoading = true;
        coachError = null;
        try {
            coachResult = await requestCoach(coachFacts(model, examDate));
        } catch (exc) {
            coachResult = null;
            coachError = String(exc);
        } finally {
            coachLoading = false;
        }
    }

    async function suggestMappings(): Promise<void> {
        if (!model) {
            return;
        }
        suggestLoading = true;
        suggestNote = null;
        suggestDisclosure = null;
        try {
            const rows = model.unmappedTags.map((row) => ({
                tag: row.tag,
                cards: row.cards,
            }));
            const result = await requestTagSuggestions(
                rows,
                TOPICS.map((topic) => topic.id),
            );
            if (!result.enabled) {
                suggestNote = result.reason ?? "Tag suggestions are switched off.";
                return;
            }
            suggestionInfo = result.suggestions ?? {};
            const validValues = new Set([
                ...TOPICS.map((topic) => topic.id),
                IGNORE_TOPIC_VALUE,
            ]);
            const { merged, applied } = mergeSuggestions(
                pendingMap,
                suggestionInfo,
                validValues,
            );
            pendingMap = merged;
            const considered = result.consideredTags ?? rows.length;
            suggestNote = `${applied} of ${considered} tags pre-filled; the rest stay blank (unsure or low confidence). Nothing is saved until you click "Save mapping".`;
            suggestDisclosure = result.disclosure ?? null;
        } catch (exc) {
            suggestNote = `Suggestions unavailable (${exc}); the manual editor still works.`;
        } finally {
            suggestLoading = false;
        }
    }

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

        {#if model.readinessDetail}
            {@const detail = model.readinessDetail}
            <!-- The honesty contract (Phase 3 M1): evidence, what's missing
                 (in the gauge above), calibration history, the band, and
                 the best next topic - rendered even while abstaining. -->
            <section class="readiness-detail">
                <h2>Behind the Readiness gauge</h2>
                <div class="detail-grid">
                    <div class="meta-item">
                        <span class="label">Pass/fail call</span>
                        <span class="value">
                            {#if detail.call}
                                {detail.call} · confidence {detail.callConfidence.toFixed(
                                    2,
                                )} (capped at {detail.confidenceCap})
                            {:else}
                                abstaining — too close to call
                            {/if}
                        </span>
                    </div>
                    {#if detail.evidence}
                        <div class="meta-item">
                            <span class="label">Delayed probe outcomes</span>
                            <span class="value">
                                {detail.evidence.probeCorrect} correct of {detail
                                    .evidence.probeAnsweredDelayed}
                            </span>
                        </div>
                        <div class="meta-item">
                            <span class="label">Probe status</span>
                            <span class="value">
                                {detail.evidence.probeUnanswered} unanswered · {detail
                                    .evidence.probeAnsweredUndelayed} too recent (excluded)
                                · {detail.evidence.probeNeverStudied} on never-studied material
                            </span>
                        </div>
                        {#if detail.evidence.meanProbeLagDays > 0}
                            <div class="meta-item">
                                <span class="label">Mean study→probe lag</span>
                                <span class="value">
                                    {detail.evidence.meanProbeLagDays.toFixed(1)} days (≥7
                                    required)
                                </span>
                            </div>
                        {/if}
                        <div class="meta-item">
                            <span class="label">Study evidence</span>
                            <span class="value">
                                {detail.evidence.gradedReviews} graded reviews · {detail
                                    .evidence.topicsStudied}/{detail.evidence
                                    .topicsTotal} topics studied
                            </span>
                        </div>
                    {/if}
                    <div class="meta-item">
                        <span class="label">Calibration history</span>
                        <span class="value">
                            {#if detail.calibration}
                                last checked {detail.calibration.fittedAt} on {detail
                                    .calibration.n} held-out outcomes · Brier {detail.calibration.brier.toFixed(
                                    3,
                                )} · log-loss {detail.calibration.logLoss.toFixed(3)}
                            {:else}
                                never run — the offline probe harness has not scored the
                                calibration pool yet
                            {/if}
                        </span>
                    </div>
                    {#if detail.bestNextTopic}
                        <div class="meta-item best-next">
                            <span class="label">Best next topic</span>
                            <span class="value">{detail.bestNextTopic}</span>
                        </div>
                    {/if}
                    <div class="meta-item">
                        <span class="label">Pass band (MPS proxy)</span>
                        <span class="value">
                            {Math.round(detail.mpsLow * 100)}–{Math.round(
                                detail.mpsHigh * 100,
                            )}% (unpublished; carried as a band)
                        </span>
                    </div>
                </div>
            </section>
        {/if}

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
                <span class="label">Delayed probes answered</span>
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

        {#if assistant !== null}
            <!-- Desktop-only assistant layer (RUNTIME_AI_PLAN). The whole
                 block depends on the host bridge, so Android renders none
                 of it. Every feature is default-OFF and read-only: AI here
                 narrates and suggests; it never grades, schedules, or
                 feeds Readiness. -->
            {#if debriefOn}
                <section class="ai-card">
                    <div class="ai-card-header">
                        <h2>Session debrief</h2>
                        <span class="ai-badge">AI · read-only</span>
                        <button
                            class="small-button"
                            on:click={generateDebrief}
                            disabled={debriefLoading}
                        >
                            {debriefLoading ? "Working…" : "Debrief my last session"}
                        </button>
                    </div>
                    {#if debriefError}
                        <p class="ai-status">
                            Debrief unavailable ({debriefError}); your reviews are
                            unaffected.
                        </p>
                    {:else if debriefResult}
                        {#if !debriefResult.enabled}
                            <p class="ai-status">{debriefResult.reason}</p>
                        {:else if !debriefResult.report}
                            <p class="ai-status">
                                No graded reviews in the last session — study some cards
                                first.
                            </p>
                        {:else}
                            {#if debriefResult.narrative}
                                <blockquote class="ai-narrative">
                                    <p>{debriefResult.narrative.narrative}</p>
                                    <p class="next-step">
                                        Next step: {debriefResult.narrative.next_step}
                                    </p>
                                </blockquote>
                            {:else}
                                <p class="ai-status">
                                    No AI narration ({debriefResult.narrativeStatus}) —
                                    the deterministic pattern table below stands on its
                                    own.
                                </p>
                            {/if}
                            <div class="debrief-tables">
                                <div>
                                    <h3>
                                        Session ({debriefResult.report.window.n_reviews}
                                        reviews, {debriefResult.report.window.n_lapses}
                                        misses)
                                    </h3>
                                    {#if debriefResult.report.topics_missed.length}
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Topic</th>
                                                    <th>Misses</th>
                                                    <th>Reviews</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {#each debriefResult.report.topics_missed as row}
                                                    <tr>
                                                        <td>
                                                            <code>{row.topic}</code>
                                                        </td>
                                                        <td>{row.lapses}</td>
                                                        <td>{row.reviews}</td>
                                                    </tr>
                                                {/each}
                                            </tbody>
                                        </table>
                                    {:else}
                                        <p class="ai-status">
                                            No misses — clean session.
                                        </p>
                                    {/if}
                                </div>
                                {#if debriefResult.report.confusable_pairs.length}
                                    <div>
                                        <h3>Confusable pairs that co-occurred</h3>
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Pair</th>
                                                    <th>Lift</th>
                                                    <th>Session misses</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {#each debriefResult.report.confusable_pairs as row}
                                                    <tr>
                                                        <td>
                                                            <code>{row.pair[0]}</code>
                                                            vs
                                                            <code>{row.pair[1]}</code>
                                                        </td>
                                                        <td>{row.lift}</td>
                                                        <td>{row.session_lapses}</td>
                                                    </tr>
                                                {/each}
                                            </tbody>
                                        </table>
                                    </div>
                                {/if}
                                {#if debriefResult.report.misconceptions.length}
                                    <div>
                                        <h3>Misconceptions behind missed MCQs</h3>
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Misconception</th>
                                                    <th>Missed items</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {#each debriefResult.report.misconceptions as row}
                                                    <tr>
                                                        <td><code>{row.id}</code></td>
                                                        <td>{row.count}</td>
                                                    </tr>
                                                {/each}
                                            </tbody>
                                        </table>
                                    </div>
                                {/if}
                            </div>
                            <p class="best-next-line">
                                <strong>Best next:</strong>
                                {debriefResult.report.best_next}
                            </p>
                            {#if debriefResult.narrative && debriefResult.disclosure}
                                <p class="ai-disclosure">{debriefResult.disclosure}</p>
                            {/if}
                        {/if}
                    {:else}
                        <p class="ai-status">
                            Narrates your last session's error patterns from the review
                            log — counts only, never grades.
                        </p>
                    {/if}
                </section>
            {/if}

            {#if coachOn}
                <details class="ai-card coach" bind:open={coachOpen}>
                    <summary>
                        <h2>Study coach</h2>
                        <span class="ai-badge">AI · read-only</span>
                    </summary>
                    <p class="ai-status">
                        Turns the dashboard's own numbers into a "what should I do
                        today" plan. It defers to the gauges: while Readiness abstains,
                        the coach never states a pass probability.
                    </p>
                    <button
                        class="small-button"
                        on:click={askCoach}
                        disabled={coachLoading || !model}
                    >
                        {coachLoading ? "Working…" : "What should I do today?"}
                    </button>
                    {#if coachError}
                        <p class="ai-status">
                            Coach unavailable ({coachError}); the gauges above are the
                            plan.
                        </p>
                    {:else if coachResult}
                        {#if !coachResult.enabled}
                            <p class="ai-status">{coachResult.reason}</p>
                        {:else if coachResult.plan}
                            <blockquote class="ai-narrative">
                                <p>{coachResult.plan.summary}</p>
                                {#if coachResult.plan.priorities.length}
                                    <ol>
                                        {#each coachResult.plan.priorities as priority}
                                            <li>
                                                <strong>{priority.topic}</strong>
                                                {#if priority.why}— {priority.why}{/if}
                                            </li>
                                        {/each}
                                    </ol>
                                {/if}
                                {#if coachResult.plan.note}
                                    <p class="next-step">{coachResult.plan.note}</p>
                                {/if}
                            </blockquote>
                            {#if coachResult.disclosure}
                                <p class="ai-disclosure">{coachResult.disclosure}</p>
                            {/if}
                        {:else}
                            <p class="ai-status">
                                The coach abstained ({coachResult.planStatus}) — the
                                deterministic view above (weighted gaps, best next:
                                {model?.bestNext ?? "n/a"}) stands.
                            </p>
                        {/if}
                    {/if}
                </details>
            {/if}

            <details class="ai-settings">
                <summary>AI assistant settings (all default off)</summary>
                <p class="ai-status">
                    These features read the numbers this page already computed and
                    narrate or suggest. They never write: grading, scheduling and the
                    Readiness gauge stay AI-free by construction. With a non-mock
                    backend, the facts shown to you are sent to that model.
                </p>
                {#if !assistant.available}
                    <p class="ai-status unavailable">
                        Assistant runtime unavailable: {assistant.unavailableReason}
                    </p>
                {/if}
                <div class="ai-toggles">
                    <label>
                        <input
                            type="checkbox"
                            checked={assistant.aiAssist}
                            on:change={(event) =>
                                setAiFlag("aiAssist", event.currentTarget.checked)}
                        />
                        Enable AI assistant (master switch)
                    </label>
                    <label class="indented">
                        <input
                            type="checkbox"
                            checked={assistant.debriefEnabled}
                            disabled={!assistant.aiAssist}
                            on:change={(event) =>
                                setAiFlag(
                                    "debriefEnabled",
                                    event.currentTarget.checked,
                                )}
                        />
                        Session debrief
                    </label>
                    <label class="indented">
                        <input
                            type="checkbox"
                            checked={assistant.coachEnabled}
                            disabled={!assistant.aiAssist}
                            on:change={(event) =>
                                setAiFlag("coachEnabled", event.currentTarget.checked)}
                        />
                        Study coach
                    </label>
                    <label class="indented">
                        <input
                            type="checkbox"
                            checked={assistant.tagSuggestEnabled}
                            disabled={!assistant.aiAssist}
                            on:change={(event) =>
                                setAiFlag(
                                    "tagSuggestEnabled",
                                    event.currentTarget.checked,
                                )}
                        />
                        Tag→topic suggestions in the mapping editor
                    </label>
                    <label class="backend-select">
                        Backend
                        <select
                            value={assistant.backend}
                            on:change={(event) =>
                                setAiBackend(event.currentTarget.value)}
                        >
                            {#each AI_BACKEND_CHOICES as choice}
                                <option value={choice}>
                                    {choice === "" ? "(from environment)" : choice}
                                </option>
                            {/each}
                        </select>
                    </label>
                </div>
            </details>
        {/if}

        <section class="table-section">
            <SubjectTable subjects={model.subjects} />
            {#if model.aigExclusionNote}
                <p class="aig-note">{model.aigExclusionNote}.</p>
            {/if}
            {#if model.heldOutProbeCards > 0}
                <p class="aig-note">
                    {model.heldOutProbeCards} held-out probe {model.heldOutProbeCards ===
                    1
                        ? "card is"
                        : "cards are"} excluded from Memory and coverage — the measurement
                    instrument never feeds the gauges it tests.
                </p>
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
                    {#if suggestOn}
                        <div class="suggest-row">
                            <button
                                class="small-button"
                                on:click={suggestMappings}
                                disabled={suggestLoading ||
                                    (model?.unmappedTags.length ?? 0) === 0}
                            >
                                {suggestLoading
                                    ? "Suggesting…"
                                    : "AI-suggest topics (pre-fill only)"}
                            </button>
                            {#if suggestNote}
                                <span class="ai-status">{suggestNote}</span>
                            {/if}
                        </div>
                        {#if suggestDisclosure}
                            <p class="ai-disclosure">{suggestDisclosure}</p>
                        {/if}
                    {/if}
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
                                            {#if suggestionInfo[row.tag] && pendingMap[row.tag] === suggestionInfo[row.tag].topic}
                                                <span
                                                    class="suggest-confidence"
                                                    title="AI suggestion — override freely; nothing is saved until you click Save"
                                                >
                                                    AI {Math.round(
                                                        suggestionInfo[row.tag]
                                                            .confidence * 100,
                                                    )}%
                                                </span>
                                            {/if}
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
                <strong>The give-up rule (enforced by the backend):</strong>
                Readiness shows no probability until there are at least {READINESS_GATES.minGradedReviews}
                graded study reviews, {Math.round(READINESS_GATES.minCoverage * 100)}%
                topic coverage, and {READINESS_GATES.minHeldOutProbes} answered held-out
                probes taken at least 7 days after the material was last studied, and the
                resulting band is usefully narrow. The gate lives in the Rust engine, not
                in this page, so no display bug can leak an unearned number. A system that
                knows when it does not know beats a confident guess.
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

    .readiness-detail {
        margin: 1.25rem 0;
        padding: 0.75rem 1rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);

        h2 {
            font-size: 0.95rem;
            margin: 0 0 0.6rem;
        }

        .detail-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 1.5rem;
        }

        .meta-item {
            display: flex;
            flex-direction: column;
            max-width: 22rem;

            .label {
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                color: var(--fg-subtle);
            }

            .value {
                font-size: 0.9rem;
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

    .ai-card {
        margin: 0 0 1.25rem;
        padding: 0.75rem 1rem;
        border: 1px solid var(--border);
        border-radius: var(--border-radius-medium, 10px);
        background: var(--canvas-elevated);

        h2 {
            font-size: 1.05rem;
            margin: 0;
            display: inline;
        }

        h3 {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--fg-subtle);
            margin: 0.75rem 0 0.25rem;
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
                padding: 0.15rem 1rem 0.15rem 0;
            }

            td {
                padding: 0.15rem 1rem 0.15rem 0;
            }
        }
    }

    .ai-card-header {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        flex-wrap: wrap;
    }

    .ai-badge {
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        border: 1px solid var(--accent-card, #3b82f6);
        color: var(--accent-card, #3b82f6);
        white-space: nowrap;
    }

    .coach summary {
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 0.6rem;

        h2 {
            display: inline;
        }
    }

    .ai-status {
        font-size: 0.8rem;
        color: var(--fg-subtle);
        margin: 0.5rem 0 0;

        &.unavailable {
            color: var(--accent-danger, #c33);
        }
    }

    .ai-narrative {
        margin: 0.75rem 0 0;
        padding: 0.5rem 0.9rem;
        border-left: 3px solid var(--accent-card, #3b82f6);
        background: var(--canvas-inset);
        border-radius: 0 var(--border-radius, 5px) var(--border-radius, 5px) 0;
        font-size: 0.9rem;

        p {
            margin: 0.25rem 0;
        }

        ol {
            margin: 0.4rem 0 0.25rem;
            padding-left: 1.25rem;
        }

        .next-step {
            color: var(--fg-subtle);
            font-size: 0.85rem;
        }
    }

    .debrief-tables {
        display: flex;
        flex-wrap: wrap;
        gap: 0 2rem;
    }

    .best-next-line {
        font-size: 0.85rem;
        margin: 0.75rem 0 0;
    }

    .ai-disclosure {
        font-size: 0.7rem;
        color: var(--fg-subtle);
        font-style: italic;
        margin: 0.5rem 0 0;
    }

    .ai-settings {
        margin: 0 0 1.25rem;
        padding: 0.6rem 1rem;
        border: 1px dashed var(--border);
        border-radius: var(--border-radius-medium, 10px);
        font-size: 0.85rem;

        summary {
            cursor: pointer;
            color: var(--fg-subtle);
        }
    }

    .ai-toggles {
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        margin-top: 0.6rem;

        label {
            display: flex;
            align-items: center;
            gap: 0.45rem;

            &.indented {
                margin-left: 1.5rem;
            }

            &.backend-select {
                margin-top: 0.35rem;
                gap: 0.6rem;
            }
        }

        select {
            border: 1px solid var(--border);
            background: var(--canvas);
            color: var(--fg);
            border-radius: var(--border-radius, 5px);
            padding: 0.15rem 0.3rem;
        }
    }

    .suggest-row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        flex-wrap: wrap;
        margin-bottom: 0.6rem;
    }

    .suggest-confidence {
        font-size: 0.7rem;
        color: var(--accent-card, #3b82f6);
        margin-left: 0.4rem;
        white-space: nowrap;
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
