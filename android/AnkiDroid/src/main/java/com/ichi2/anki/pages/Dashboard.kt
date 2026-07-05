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
import com.ichi2.anki.SingleFragmentActivity

/**
 * Anki Speedrun: the CFA Level I readiness dashboard.
 *
 * Hosts the shared SvelteKit `dashboard` page - the Memory / Performance /
 * Readiness gauges, per-topic table, coverage map and the give-up rule -
 * for the whole collection. The page and the engine RPCs behind it
 * (`topicMastery` and `getReadiness` - the Phase 3 banded, abstaining
 * Readiness estimate whose give-up gate lives in the shared Rust engine -
 * plus the config service for the tag->topic map and exam date) ship
 * inside the rsdroid `.aar`, so this fragment only provides the WebView
 * host; the rendering is identical to the desktop app.
 */
class Dashboard : PageFragment() {
    override val pagePath: String = "dashboard"

    companion object {
        fun getIntent(context: Context): Intent = SingleFragmentActivity.getIntent(context, Dashboard::class)
    }
}
