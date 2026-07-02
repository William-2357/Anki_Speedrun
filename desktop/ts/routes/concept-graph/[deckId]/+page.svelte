<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { page } from "$app/stores";

    import ConceptGraphPage from "../ConceptGraphPage.svelte";

    $: deckId = BigInt($page.params.deckId ?? "0");
</script>

{#key deckId}
    <ConceptGraphPage {deckId} />
{/key}
