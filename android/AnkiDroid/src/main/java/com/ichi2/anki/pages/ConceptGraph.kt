/*
 *  Copyright (c) 2026 Anki Speedrun contributors
 *
 *  This program is free software; you can redistribute it and/or modify it under
 *  the terms of the GNU General Public License as published by the Free Software
 *  Foundation; either version 3 of the License, or (at your option) any later
 *  version.
 *
 *  This program is distributed in the hope that it will be useful, but WITHOUT ANY
 *  WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
 *  PARTICULAR PURPOSE. See the GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License along with
 *  this program.  If not, see <http://www.gnu.org/licenses/>.
 */
package com.ichi2.anki.pages

import android.content.Context
import android.content.Intent
import android.os.Bundle
import com.ichi2.anki.SingleFragmentActivity
import com.ichi2.anki.libanki.DeckId

/**
 * Anki Speedrun: the concept-graph knowledge map for a deck.
 *
 * Hosts the shared SvelteKit `concept-graph/{deckId}` page — one node per
 * tag, edges where two tags co-occur on a note, coloured by answer
 * difficulty (with an FSRS-recall toggle). The page and the engine RPC
 * behind it ship inside the rsdroid `.aar`, so this fragment only provides
 * the WebView host; the rendering is identical to the desktop app.
 */
class ConceptGraph : PageFragment() {
    override val pagePath: String by lazy {
        val deckId = requireArguments().getLong(ARG_DECK_ID)
        "concept-graph/$deckId"
    }

    companion object {
        private const val ARG_DECK_ID = "arg_deck_id"

        /** [deckId] 0 shows the whole collection; child decks are included. */
        fun getIntent(
            context: Context,
            deckId: DeckId,
        ): Intent =
            SingleFragmentActivity.getIntent(
                context,
                ConceptGraph::class,
                Bundle().apply { putLong(ARG_DECK_ID, deckId) },
            )
    }
}
