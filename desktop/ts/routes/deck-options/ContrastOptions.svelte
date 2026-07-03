<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import * as tr from "@generated/ftl";
    import type Carousel from "bootstrap/js/dist/carousel";
    import type Modal from "bootstrap/js/dist/modal";

    import Col from "$lib/components/Col.svelte";
    import ConfigInput from "$lib/components/ConfigInput.svelte";
    import DynamicallySlottable from "$lib/components/DynamicallySlottable.svelte";
    import HelpModal from "$lib/components/HelpModal.svelte";
    import Item from "$lib/components/Item.svelte";
    import RevertButton from "$lib/components/RevertButton.svelte";
    import Row from "$lib/components/Row.svelte";
    import SettingTitle from "$lib/components/SettingTitle.svelte";
    import SwitchRow from "$lib/components/SwitchRow.svelte";
    import TitledContainer from "$lib/components/TitledContainer.svelte";
    import type { HelpItem } from "$lib/components/types";

    import type { DeckOptionsState } from "./lib";

    export let state: DeckOptionsState;
    export let api: Record<string, never>;

    const config = state.currentConfig;
    const defaults = state.defaults;

    const settings = {
        contrastScheduling: {
            title: tr.deckConfigContrastScheduling(),
            help: tr.deckConfigContrastSchedulingTooltip(),
        },
        contrastTagPrefix: {
            title: tr.deckConfigContrastTagPrefix(),
            help: tr.deckConfigContrastTagPrefixTooltip(),
        },
        contrastConfusableTag: {
            title: tr.deckConfigContrastConfusableTag(),
            help: tr.deckConfigContrastConfusableTagTooltip(),
        },
    };
    const helpSections: HelpItem[] = Object.values(settings);

    let modal: Modal;
    let carousel: Carousel;

    function openHelpModal(index: number): void {
        modal.show();
        carousel.to(index);
    }
</script>

<TitledContainer title={tr.deckConfigContrastTitle()}>
    <HelpModal
        title={tr.deckConfigContrastTitle()}
        url="https://github.com/William-2357/anki-speedrun"
        slot="tooltip"
        {helpSections}
        on:mount={(e) => {
            modal = e.detail.modal;
            carousel = e.detail.carousel;
        }}
    />
    <DynamicallySlottable slotHost={Item} {api}>
        <Item>
            <SwitchRow
                bind:value={$config.contrastScheduling}
                defaultValue={defaults.contrastScheduling}
            >
                <SettingTitle
                    on:click={() =>
                        openHelpModal(
                            Object.keys(settings).indexOf("contrastScheduling"),
                        )}
                >
                    {settings.contrastScheduling.title}
                </SettingTitle>
            </SwitchRow>
        </Item>

        {#if $config.contrastScheduling}
            <Item>
                <Row --cols={13}>
                    <Col --col-size={7} breakpoint="xs">
                        <SettingTitle
                            on:click={() =>
                                openHelpModal(
                                    Object.keys(settings).indexOf("contrastTagPrefix"),
                                )}
                        >
                            {settings.contrastTagPrefix.title}
                        </SettingTitle>
                    </Col>
                    <Col --col-size={6} breakpoint="xs">
                        <ConfigInput>
                            <input
                                type="text"
                                bind:value={$config.contrastTagPrefix}
                                placeholder="cluster::"
                                class="w-100 mb-1"
                            />
                            <RevertButton
                                slot="revert"
                                bind:value={$config.contrastTagPrefix}
                                defaultValue={defaults.contrastTagPrefix}
                            />
                        </ConfigInput>
                    </Col>
                </Row>
            </Item>

            <Item>
                <Row --cols={13}>
                    <Col --col-size={7} breakpoint="xs">
                        <SettingTitle
                            on:click={() =>
                                openHelpModal(
                                    Object.keys(settings).indexOf(
                                        "contrastConfusableTag",
                                    ),
                                )}
                        >
                            {settings.contrastConfusableTag.title}
                        </SettingTitle>
                    </Col>
                    <Col --col-size={6} breakpoint="xs">
                        <ConfigInput>
                            <input
                                type="text"
                                bind:value={$config.contrastConfusableTag}
                                placeholder="confusable::high"
                                class="w-100 mb-1"
                            />
                            <RevertButton
                                slot="revert"
                                bind:value={$config.contrastConfusableTag}
                                defaultValue={defaults.contrastConfusableTag}
                            />
                        </ConfigInput>
                    </Col>
                </Row>
            </Item>
        {/if}
    </DynamicallySlottable>
</TitledContainer>
