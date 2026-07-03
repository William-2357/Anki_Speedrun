<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import * as tr from "@generated/ftl";
    import type Carousel from "bootstrap/js/dist/carousel";
    import type Modal from "bootstrap/js/dist/modal";

    import DynamicallySlottable from "$lib/components/DynamicallySlottable.svelte";
    import EnumSelectorRow from "$lib/components/EnumSelectorRow.svelte";
    import HelpModal from "$lib/components/HelpModal.svelte";
    import Item from "$lib/components/Item.svelte";
    import SettingTitle from "$lib/components/SettingTitle.svelte";
    import SwitchRow from "$lib/components/SwitchRow.svelte";
    import TitledContainer from "$lib/components/TitledContainer.svelte";
    import type { HelpItem } from "$lib/components/types";

    import { fadeOrderChoices, fadeSignalChoices } from "./choices";
    import type { DeckOptionsState } from "./lib";
    import SpinBoxFloatRow from "./SpinBoxFloatRow.svelte";
    import SpinBoxRow from "./SpinBoxRow.svelte";

    export let state: DeckOptionsState;
    export let api: Record<string, never>;

    const config = state.currentConfig;
    const defaults = state.defaults;

    const settings = {
        fadeEnabled: {
            title: tr.deckConfigFadeEnabled(),
            help: tr.deckConfigFadeEnabledTooltip(),
        },
        fadeSignal: {
            title: tr.deckConfigFadeSignal(),
            help: tr.deckConfigFadeSignalTooltip(),
        },
        fadeUpR: {
            title: tr.deckConfigFadeUpR(),
            help: tr.deckConfigFadeUpRTooltip(),
        },
        fadeDownR: {
            title: tr.deckConfigFadeDownR(),
            help: tr.deckConfigFadeDownRTooltip(),
        },
        promotionSpacedSessions: {
            title: tr.deckConfigPromotionSpacedSessions(),
            help: tr.deckConfigPromotionSpacedSessionsTooltip(),
        },
        fluencyStabilityFloor: {
            title: tr.deckConfigFluencyStabilityFloor(),
            help: tr.deckConfigFluencyStabilityFloorTooltip(),
        },
        fadeOrder: {
            title: tr.deckConfigFadeOrder(),
            help: tr.deckConfigFadeOrderTooltip(),
        },
        selfExplain: {
            title: tr.deckConfigSelfExplain(),
            help: tr.deckConfigSelfExplainTooltip(),
        },
        elementInteractivityGate: {
            title: tr.deckConfigElementInteractivityGate(),
            help: tr.deckConfigElementInteractivityGateTooltip(),
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

<TitledContainer title={tr.deckConfigFadeTitle()}>
    <HelpModal
        title={tr.deckConfigFadeTitle()}
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
                bind:value={$config.fadeEnabled}
                defaultValue={defaults.fadeEnabled}
            >
                <SettingTitle
                    on:click={() =>
                        openHelpModal(Object.keys(settings).indexOf("fadeEnabled"))}
                >
                    {settings.fadeEnabled.title}
                </SettingTitle>
            </SwitchRow>
        </Item>

        {#if $config.fadeEnabled}
            <Item>
                <EnumSelectorRow
                    bind:value={$config.fadeSignal}
                    defaultValue={defaults.fadeSignal}
                    choices={fadeSignalChoices()}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(Object.keys(settings).indexOf("fadeSignal"))}
                    >
                        {settings.fadeSignal.title}
                    </SettingTitle>
                </EnumSelectorRow>
            </Item>

            <Item>
                <SpinBoxFloatRow
                    bind:value={$config.fadeUpR}
                    defaultValue={defaults.fadeUpR}
                    min={0.5}
                    max={0.99}
                    step={0.01}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(Object.keys(settings).indexOf("fadeUpR"))}
                    >
                        {settings.fadeUpR.title}
                    </SettingTitle>
                </SpinBoxFloatRow>
            </Item>

            <Item>
                <SpinBoxFloatRow
                    bind:value={$config.fadeDownR}
                    defaultValue={defaults.fadeDownR}
                    min={0.5}
                    max={0.99}
                    step={0.01}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(Object.keys(settings).indexOf("fadeDownR"))}
                    >
                        {settings.fadeDownR.title}
                    </SettingTitle>
                </SpinBoxFloatRow>
            </Item>

            <Item>
                <SpinBoxRow
                    bind:value={$config.promotionSpacedSessions}
                    defaultValue={defaults.promotionSpacedSessions}
                    min={1}
                    max={20}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(
                                Object.keys(settings).indexOf(
                                    "promotionSpacedSessions",
                                ),
                            )}
                    >
                        {settings.promotionSpacedSessions.title}
                    </SettingTitle>
                </SpinBoxRow>
            </Item>

            <Item>
                <SpinBoxFloatRow
                    bind:value={$config.fluencyStabilityFloor}
                    defaultValue={defaults.fluencyStabilityFloor}
                    min={0}
                    max={36500}
                    step={1}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(
                                Object.keys(settings).indexOf("fluencyStabilityFloor"),
                            )}
                    >
                        {settings.fluencyStabilityFloor.title}
                    </SettingTitle>
                </SpinBoxFloatRow>
            </Item>

            <Item>
                <EnumSelectorRow
                    bind:value={$config.fadeOrder}
                    defaultValue={defaults.fadeOrder}
                    choices={fadeOrderChoices()}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(Object.keys(settings).indexOf("fadeOrder"))}
                    >
                        {settings.fadeOrder.title}
                    </SettingTitle>
                </EnumSelectorRow>
            </Item>

            <Item>
                <SwitchRow
                    bind:value={$config.selfExplainEnabled}
                    defaultValue={defaults.selfExplainEnabled}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(Object.keys(settings).indexOf("selfExplain"))}
                    >
                        {settings.selfExplain.title}
                    </SettingTitle>
                </SwitchRow>
            </Item>

            <Item>
                <SwitchRow
                    bind:value={$config.elementInteractivityGate}
                    defaultValue={defaults.elementInteractivityGate}
                >
                    <SettingTitle
                        on:click={() =>
                            openHelpModal(
                                Object.keys(settings).indexOf(
                                    "elementInteractivityGate",
                                ),
                            )}
                    >
                        {settings.elementInteractivityGate.title}
                    </SettingTitle>
                </SwitchRow>
            </Item>
        {/if}
    </DynamicallySlottable>
</TitledContainer>
