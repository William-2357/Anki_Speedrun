<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { page } from "$app/stores";

    import DashboardPage from "./DashboardPage.svelte";

    // Dev-only escape hatch: ?readinessTest=1 relaxes the give-up gates so
    // the readiness pipeline can be exercised end to end. Its output is
    // loudly labelled as test data and never looks like a real prediction.
    $: testMode = $page.url.searchParams.get("readinessTest") === "1";
</script>

{#key testMode}
    <DashboardPage {testMode} />
{/key}
