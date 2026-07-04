# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The runtime assistant layer (RUNTIME_AI_PLAN.md) - OUTSIDE the review loop.

Grounded, abstaining, single-call completions for the three runtime-AI
features: the post-session debrief (A), the study coach (B) and the
tag->topic suggester (C). Reuses the pluggable model backends from
``aig/models.py`` (claude-cli / openai-compatible / parse_json_reply) and
adds:

- ``core.make_backend``          - backend from config/env, default ``mock``;
- ``core.grounded_complete``     - single-call JSON completion that abstains
  (returns ``None``) whenever the reply is unparseable, fails its schema,
  or states a number not present in the supplied facts;
- one module per feature (``debrief`` / ``coach`` / ``tag_suggest``).

THE WALL: nothing in this package may write to the collection - no grading,
no scheduling, no Readiness input. Every public function only reads
already-computed facts and returns text/JSON for a human to look at.
Callers fall back to their deterministic view whenever a function returns
``None`` / abstains / raises.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `import assistant` self-sufficient: `aig` (the reused backends) lives
# next to this package under tools/speedrun/.
_SPEEDRUN_DIR = str(Path(__file__).resolve().parent.parent)
if _SPEEDRUN_DIR not in sys.path:
    sys.path.insert(0, _SPEEDRUN_DIR)
