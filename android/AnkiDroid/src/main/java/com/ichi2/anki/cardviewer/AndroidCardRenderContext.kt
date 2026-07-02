// SPDX-License-Identifier: GPL-3.0-or-later

package com.ichi2.anki.cardviewer

import android.content.Context
import androidx.annotation.CheckResult
import anki.config.ConfigKey
import com.ichi2.anki.common.preferences.sharedPrefs
import com.ichi2.anki.libanki.Card
import com.ichi2.anki.libanki.CardOrdinal
import com.ichi2.anki.libanki.Collection
import com.ichi2.anki.libanki.TemplateManager.TemplateRenderContext.TemplateRenderOutput
import com.ichi2.anki.libanki.template.MathJax
import com.ichi2.anki.multimedia.expandSounds
import com.ichi2.anki.reviewer.ReviewerCustomFonts
import timber.log.Timber

/**
 * Holds Android-specific context which affects how a card is rendered to HTML
 *
 * @see renderCard
 */
class AndroidCardRenderContext(
    private val typeAnswer: TypeAnswer,
    private val cardAppearance: CardAppearance,
    private val cardTemplate: CardTemplate,
    private val showAudioPlayButtons: Boolean,
) {
    /**
     * Renders Android-specific functionality to produce a [RenderedCard]
     */
    @CheckResult
    fun renderCard(
        col: Collection,
        card: Card,
        side: SingleCardSide,
    ): RenderedCard {
        // obtain the libAnki-rendered card
        var content: String = if (side == SingleCardSide.FRONT) card.question(col) else card.answer(col)
        // IRI-encodes media: `foo bar` -> `foo%20bar`
        content = col.media.escapeMediaFilenames(content)
        // produces either an <input> or <span>...</span> to denote typed input
        content = filterTypeAnswer(content, side)
        // wraps content in <div id="qa">
        content = enrichWithQADiv(content)
        // expands [anki:q:1] to a play button
        content = expandSounds(content, card.renderOutput(col), col)
        // fixes an Android bug where font-weight:600 does not display
        content = CardAppearance.fixBoldStyle(content)
        // Anki Speedrun: always display the note's tags alongside the card, rather than
        // keeping them hidden during review.
        content += renderTagsFooter(card.note(col).tags)

        // based on the content, load appropriate scripts such as MathJax, then render
        return render(content, card.ord)
    }

    private fun render(
        content: String,
        ord: CardOrdinal,
    ): RenderedCard {
        val requiresMathjax = MathJax.textContainsMathjax(content)

        val style = cardAppearance.style
        val script =
            when (requiresMathjax) {
                false -> ""
                true ->
                    """        <script src="file:///android_asset/backend/js/mathjax.js"></script>
        <script src="file:///android_asset/backend/js/vendor/mathjax/tex-chtml-full.js"></script>"""
            }
        val cardClass = cardAppearance.getCardClass(ord + 1) + if (requiresMathjax) " mathjax-needs-to-render" else ""

        Timber.v("content card = \n %s", content)
        Timber.v("::style:: / %s", style)

        return cardTemplate.render(content, style, script, cardClass)
    }

    /**
     * Adds a div html tag around the contents to have an indication, where answer/question is displayed
     *
     * @param content The content to surround with tags.
     * @return The enriched content
     */
    private fun enrichWithQADiv(content: String) =
        buildString {
            append("""<div id="qa">""")
            append(content)
            append("</div>")
        }

    /**
     * Anki Speedrun: builds an always-visible footer listing the note's tags so the learner
     * sees a card's tags during review instead of them being hidden. Styled inline with
     * theme-neutral colours so it renders legibly over any card CSS in both light and dark modes.
     *
     * @param tags the note's tags (may be empty)
     * @return an HTML `<div>` to append after the card content
     */
    private fun renderTagsFooter(tags: List<String>): String {
        val chips =
            if (tags.isEmpty()) {
                """<span style="opacity:0.6">(no tags)</span>"""
            } else {
                tags.joinToString(separator = " ") { tag ->
                    val chipStyle =
                        "display:inline-block;padding:1px 7px;margin:2px;" +
                            "border:1px solid rgba(128,128,128,0.5);border-radius:9px;white-space:nowrap;"
                    """<span class="anki-tag" style="$chipStyle">${escapeHtml(tag)}</span>"""
                }
            }
        val footerStyle =
            "margin-top:1em;padding-top:0.5em;border-top:1px solid rgba(128,128,128,0.35);" +
                "font-size:0.8em;line-height:1.9;opacity:0.85;text-align:center;word-break:break-word;"
        return """<div id="anki-tags" dir="auto" style="$footerStyle">$chips</div>"""
    }

    private fun escapeHtml(text: String): String =
        text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")

    private fun filterTypeAnswer(
        content: String,
        side: SingleCardSide,
    ): String =
        when (side) {
            SingleCardSide.FRONT -> typeAnswer.filterQuestion(content)
            SingleCardSide.BACK -> typeAnswer.filterAnswer(content)
        }

    private fun expandSounds(
        content: String,
        renderOutput: TemplateRenderOutput,
        col: Collection,
    ): String {
        val mediaDir = col.media.dir

        return expandSounds(
            content,
            renderOutput,
            showAudioPlayButtons,
            mediaDir,
        )
    }

    companion object {
        fun createInstance(
            context: Context,
            col: Collection,
            typeAnswer: TypeAnswer,
        ): AndroidCardRenderContext {
            val preferences = context.sharedPrefs()
            val cardAppearance = CardAppearance.create(ReviewerCustomFonts(), preferences)
            val cardHtmlTemplate = CardTemplate.load(context)
            val showAudioPlayButtons = !col.config.getBool(ConfigKey.Bool.HIDE_AUDIO_PLAY_BUTTONS)
            return AndroidCardRenderContext(
                typeAnswer,
                cardAppearance,
                cardHtmlTemplate,
                showAudioPlayButtons,
            )
        }
    }
}
