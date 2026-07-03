# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Authoring-time AIG pipeline for Anki Speedrun (Phase 2 M1/M1b).

Fully automated generate->validate tooling: parameterized numeric generators,
machine validation gates, pluggable LLM drafter/critic backends,
retrieval-for-grounding, and the computed confusability signal. Everything
here runs offline at authoring time; the review loop stays AI-free by
construction (items are baked into JSONL and consumed by the deck builder).
"""

__all__ = [
    "confusability",
    "gates",
    "generators",
    "models",
    "pdf_text",
    "prompts",
    "retrieval",
    "run_pipeline",
]
