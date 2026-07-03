# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Prompt templates for the LLM drafter / critic / solver path.

FEW-SHOT SEED NOTE (style exemplars, NOT templates, NOT quoted):
if ``desktop/tools/speedrun/reference/cfa_l1_official_sample_2025.pdf`` is
present locally, the 30 official CFA Institute "Are you ready for Level I?"
sample MCQs (3 per topic x 10 topics) are the STYLE exemplars this prompt
describes: exam-congruent A/B/C single-best format, one concise
application-style stem per item, a short answer rationale, and a per-
distractor explanation of the specific error each wrong choice embodies.
That PDF is (c) CFA Institute, kept as a local, git-ignored authoring-time
reference. Its text is NEVER extracted into prompts, never copied, and never
committed; the prompts below DESCRIBE the format instead of quoting any
exemplar, and the leakage wall (gates.gate_leakage) n-gram-checks every
generated stem against the PDF text (extracted at runtime only) to reject
verbatim or near-verbatim overlap (>= 8-gram).

The generated output must be net-new, original items - the exemplars inform
shape, tone, length and difficulty only.
"""

from __future__ import annotations

STYLE_NOTE = (
    "Style: match the official CFA Level I sample-question format - a single "
    "concise application-style stem (two to four sentences, realistic "
    "figures, no fluff), exactly three answer options labelled A, B and C, "
    "one single-best answer, stems that end with 'is closest to:' for "
    "numeric items or a direct question for conceptual ones. Do NOT copy or "
    "paraphrase any existing exam question; write a completely new item."
)

DRAFTER_PROMPT = """You are drafting ONE original CFA Level I practice item.

{style_note}

Topic area: {topic}
Concept cluster: {cluster}
Concept to test: {concept}

Requirements:
- The stem must be an APPLICATION of the concept (compute or discriminate),
  not a definition lookup.
- Exactly three choices A/B/C; exactly one is correct.
- Each wrong choice must embody a specific, named student misconception from
  this list (use the ids verbatim): {misconceptions}
- Include a rationale explaining the correct answer, and for EACH wrong
  letter an explanation of the specific error that produces it.
- All numbers must be internally consistent; show your computation in the
  rationale.

Return ONLY a JSON object (no markdown fence, no commentary):
{{
  "stem": "...",
  "choices": {{"A": "...", "B": "...", "C": "..."}},
  "correct": "A" | "B" | "C",
  "rationale": "...",
  "distractor_rationales": {{"<wrong letter>": "...", "<wrong letter>": "..."}},
  "misconceptions": {{"<wrong letter>": "<misconception id>", "<wrong letter>": "<misconception id>"}},
  "title": "short human-readable name"
}}"""

CRITIC_PROMPT = """You are an independent, adversarial reviewer of one CFA
Level I practice item. You did NOT write it. Hunt for reasons to REJECT it.

Checks, in order:
1. Factual accuracy: is the labelled correct answer actually correct? Redo
   any computation from scratch.
2. Single-best-answer: could a well-prepared candidate defend ANY other
   choice? If more than one choice is defensible, reject (this is the #1
   defect in generated items).
3. Functioning distractors: each wrong choice must be plausibly produced by
   the named misconception, and must not equal the correct value.
4. The rationale must justify the correct answer and each distractor
   explanation must match its choice.

Item JSON:
{item_json}

Return ONLY a JSON object:
{{"verdict": "accept" | "reject", "reasons": ["..."]}}"""

SOLVER_PROMPT = """Solve this CFA Level I multiple-choice question
independently. Work the computation yourself; do not guess.

{stem}

A. {choice_a}
B. {choice_b}
C. {choice_c}

Return ONLY a JSON object: {{"answer": "A" | "B" | "C"}}"""


def drafter_prompt(
    topic: str, cluster: str, concept: str, misconceptions: list[str]
) -> str:
    return DRAFTER_PROMPT.format(
        style_note=STYLE_NOTE,
        topic=topic,
        cluster=cluster,
        concept=concept,
        misconceptions=", ".join(misconceptions),
    )


def critic_prompt(item_json: str) -> str:
    return CRITIC_PROMPT.format(item_json=item_json)


def solver_prompt(stem: str, choices: dict[str, str]) -> str:
    return SOLVER_PROMPT.format(
        stem=stem,
        choice_a=choices["A"],
        choice_b=choices["B"],
        choice_c=choices["C"],
    )
