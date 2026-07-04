# RUNTIME_AI notes — lessons from the implementing agent

One lesson per entry; summary line first. Corrections and confirmed approaches
only — nothing the spec or the repo already states.

- **The plan's `AnkiWebViewKind.CFA_DASHBOARD` is actually `SPEEDRUN_DASHBOARD` in source.**
  `qt/aqt/webview.py` names the API-enabled dashboard kind `SPEEDRUN_DASHBOARD`
  (it is in the `have_api_access` list, so the page gets the Bearer token via
  `AuthInterceptor`); the dialog lives in `qt/aqt/speedrun_dashboard.py`, not a
  toolbar-owned webview.

- **mediasrv POSTs must send `Content-Type: application/binary` even for JSON.**
  `_check_dynamic_request_permissions` rejects any other content type as a
  cross-origin guard, so the page's assistant client posts JSON bytes under
  that header and the bridge just `json.loads`s `request.data`. Route names are
  camelcased handler names (`speedrun_assistant` → `/_anki/speedrunAssistant`).

- **"confidence" must be exempt from the number-grounding check.** The S1
  prompt template invites a 0–1 confidence in replies, and the tag suggester's
  per-tag entries carry one; without a metadata exemption every confident
  real-backend reply would abstain as "ungrounded number". Fixed in
  `assistant/core.py` (`_METADATA_KEYS`); facts-side numbers are unaffected.

- **Feature-flag writes go through the standard config RPC, not the bridge.**
  First draft had a `setFlags` bridge action; dropped it. The page writes
  `speedrun:*` keys with `setConfigJson` (same as `tagTopicMap`/`exam_date`),
  which keeps the bridge 100% read-only and reuses the synced-config path the
  dashboard already has.

- **`tools/speedrun/assistant` joins `aig`/`tests` in the mypy exclude.**
  `check:mypy` runs over the whole `tools/` folder, and the assistant package
  resolves `aig` imports via a `sys.path` bootstrap that mypy can't follow.
  `qt/aqt/speedrun_assistant.py` (the bridge) IS strictly checked; its dynamic
  import of the tools package carries `type: ignore[import-not-found]`.

- **Ease 0 = manual/reschedule entries; graded reviews are `ease >= 1`.** The
  debrief's revlog SQL filters on that (same convention as `mastery.rs`'s
  graded-review count), so rescheduling noise never counts as a "mistake".

- **Solve-MCQ note fields are (Title, Stem, …) and are `html.escape`d.** To
  match a missed note back to its `speedrun-item-v1` record, normalize by
  HTML-unescaping and whitespace-collapsing the note's leading fields and index
  item records by both title and stem.

- **`sessionize` must define "session" by wall-clock gap, not UTC day.** The
  confusability miner buckets by UTC day for base rates (fine for lift), but a
  post-session debrief needs the trailing contiguous run of reviews (gap
  ≤ 60 min), or a late-night session would be split by the day rollover.

- **AI-off acceptance for the debrief = narration off, table on.** The card
  itself is default-OFF (both flags); once the feature is on, any backend
  failure/abstention still renders the deterministic pattern table — narrative
  and table are separate fields in the bridge reply, never merged.

- **The offline mock must model COMPLIANT behavior, or honest tests can't
  pass.** The first `_mock_coach` summary said "no pass probability exists
  yet" — which itself matches the coach's pass-claim ban (the reject hook
  rightly rejected it). The mock now echoes the gauge's abstention reasons
  verbatim and never mentions pass likelihood, mirroring what a correct
  model reply must look like.

- **Ban pass-probability by pattern class, not by enumerated phrases.** The
  coach's `_PASS_CLAIM_RE` groups (probability…pass, chance/odds/likelihood
  …pass, will/would/probably pass) with a bounded `[^.]{0,60}` gap catch
  rewordings; invented figures are already dead via number-grounding, so the
  regex only needs the numberless claims. Tests enumerate six wordings.

- **Parallel implementer subagents on disjoint modules worked; one stalled
  and was stood down.** A (debrief) and C (tag suggester) landed clean with
  strong tests in ~15 min; B (coach) produced no file writes for 30+ min and
  its transcript stopped moving — it was interrupted with a stand-down order
  and the coach was implemented by the coordinator instead. Detection signal:
  compare transcript mtimes against sibling agents, not just elapsed time.

- **`sys.modules` stubs leak across a shared pytest process.** The bridge
  test stubs `anki`/`aqt` module-globally, so sibling tests must not assert
  "anki not in sys.modules" as a purity check — scan the module's own source
  or `vars()` for pylib references instead (WallTests do exactly that).

- **Verifier subagents can die to environment failures; keep the matrix
  runnable inline.** All three fresh-eyes verifiers were killed by a Cursor
  billing error before doing any work. Because the plan's Build/verify
  section is a list of plain commands (pytest, dmypy, vitest, check:svelte,
  ./check), the coordinator re-ran the whole matrix inline in minutes —
  gate on commands, not on subagent availability.

- **An acceptance line without a test is a gap: Android degradation was
  code-only until verification.** The `assistant !== null` gate existed but
  nothing executed the bridge-absent path; two vitest cases were added
  proving `fetchAssistantStatus` resolves null on fetch failure/404/missing
  bridge marker, which is the property the whole no-broken-buttons
  acceptance rests on.
