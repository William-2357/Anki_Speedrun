# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The fade-ladder card variants (Anki Speedrun, Phase 2 M3) as pure data.

Defines the worked / solve / compare note types (name, fields, templates,
css) plus the text layout for the faded rung, which deliberately is NOT a
custom note type: faded cards ride Anki's stock "Cloze" note type so the
engine's cloze machinery (one card per cloze index, template ordinals =
fading order) works unmodified. ``faded_cloze_fields`` builds the stock
Text / Back Extra strings from an item record.

Design constraints, from PHASE2_PLAN_V2.md M3:

* [R9] every template ends in a reveal/feedback step that renders the
  Rationale (Back Extra for cloze) - enforced by
  ``ladder_schema.lint_notetype_feedback`` at build time, not convention.
* The solve MCQ is self-contained HTML/CSS/JS: no pycmd, no JS bridge, no
  external deps, self-graded via the normal answer buttons. It must render
  in both the desktop webview and AnkiDroid's webview, so the script is
  plain ES5 and the back side is a complete no-JS fallback.
* [R16] the self-explanation variant is template ordinal 1, wrapped in a
  {{#SelfExplainPrompt}} conditional over the WHOLE front, so the card only
  generates when the field is non-empty (the engine serves ord 0 or ord 1
  per the ``self_explain_enabled`` deck-config flag).
* Honesty: the front never renders Correct/Wrong* visibly before reveal.
  The correct letter lives in a hidden data attribute; the per-choice
  rationales sit inside a [hidden] feedback panel.
* CSS is minimal, embedded per note type, and readable in light and dark
  mode (.nightMode like stock templates, plus AnkiDroid's .night_mode).

Pure Python, stdlib only - no anki imports. The deck builder
(build_ladder_deck.py) turns these specs into real note types via pylib.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

WORKED_NOTETYPE_NAME = "Speedrun Worked"
SOLVE_NOTETYPE_NAME = "Speedrun Solve MCQ"
COMPARE_NOTETYPE_NAME = "Speedrun Compare"
#: The faded rung uses the stock cloze note type (looked up by name in the
#: fresh build collection, where it carries the English field names).
STOCK_CLOZE_NOTETYPE_NAME = "Cloze"

CHOICE_LETTERS = ("A", "B", "C")

#: [R16] Shown above the stem by the ord-1 solve variant only. The builder
#: leaves the field empty under --no-self-explain, so ord 1 never generates.
DEFAULT_SELF_EXPLAIN_PROMPT = (
    "Before answering, explain to yourself why the correct approach applies "
    "here - and what would have to be true for each alternative to be right."
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

# Palette via custom properties on .card so one night-mode block re-skins
# everything. Desktop puts .nightMode on the card element; AnkiDroid uses
# .night_mode - cover both, like widely-used shared decks do.
_BASE_CSS = """\
.card {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
    font-size: 20px;
    line-height: 1.45;
    text-align: left;
    color: #1d2129;
    background-color: #fbfbfd;
    --sr-muted: #5b6572;
    --sr-border: #c9d1d9;
    --sr-panel: #f0f3f6;
    --sr-accent: #2264dc;
    --sr-good: #1a7f37;
    --sr-good-bg: #e3f5e9;
    --sr-bad: #b93325;
    --sr-bad-bg: #fcedea;
}
.card.nightMode,
.card.night_mode,
.nightMode .card,
.night_mode .card {
    color: #e4e6e8;
    background-color: #23272b;
    --sr-muted: #9aa4ae;
    --sr-border: #454e57;
    --sr-panel: #2d3339;
    --sr-accent: #6ea8ff;
    --sr-good: #5cbe77;
    --sr-good-bg: #253c2c;
    --sr-bad: #e08373;
    --sr-bad-bg: #402b26;
}
.sr-card {
    max-width: 34em;
    margin: 0 auto;
}
.sr-kicker {
    font-size: 0.7em;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--sr-muted);
    margin-bottom: 0.6em;
}
.sr-title {
    font-weight: 700;
    margin-bottom: 0.4em;
}
.sr-prompt {
    margin-bottom: 0.8em;
}
.sr-feedback {
    margin-top: 1em;
    border-top: 1px solid var(--sr-border);
    padding-top: 0.8em;
}
.sr-feedback[hidden] {
    display: none;
}
.sr-rationale {
    background: var(--sr-panel);
    border-left: 4px solid var(--sr-accent);
    border-radius: 4px;
    padding: 0.5em 0.7em;
    margin: 0.5em 0;
}
.sr-why-label {
    font-weight: 700;
}
.sr-source {
    font-size: 0.72em;
    color: var(--sr-muted);
    margin-top: 0.7em;
}
.sr-source-label {
    font-weight: 700;
}
.sr-source-passage {
    font-style: italic;
}
"""

_WORKED_CSS = (
    _BASE_CSS
    + """\
.sr-instruction {
    font-size: 0.8em;
    font-style: italic;
    color: var(--sr-muted);
}
.sr-steps-list {
    margin: 0.4em 0 0.4em 1.5em;
    padding: 0;
}
.sr-steps-list li {
    margin: 0.35em 0;
}
"""
)

_SOLVE_CSS = (
    _BASE_CSS
    + """\
.sr-self-explain {
    background: var(--sr-panel);
    border-left: 4px solid var(--sr-accent);
    border-radius: 4px;
    padding: 0.5em 0.7em;
    margin-bottom: 0.9em;
    font-style: italic;
}
.sr-stem {
    margin-bottom: 0.9em;
}
.sr-choices {
    display: flex;
    flex-direction: column;
    gap: 0.55em;
}
.sr-choice {
    display: flex;
    align-items: flex-start;
    gap: 0.6em;
    width: 100%;
    box-sizing: border-box;
    text-align: left;
    font: inherit;
    color: inherit;
    background: var(--sr-panel);
    border: 1px solid var(--sr-border);
    border-radius: 8px;
    padding: 0.55em 0.75em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
}
.sr-solve-front:not(.sr-revealed) .sr-choice:hover {
    border-color: var(--sr-accent);
}
.sr-revealed .sr-choice,
.sr-choices-static .sr-choice {
    cursor: default;
}
.sr-choice-letter {
    font-weight: 700;
    min-width: 1.2em;
}
.sr-choice.sr-correct {
    border-color: var(--sr-good);
    background: var(--sr-good-bg);
    box-shadow: inset 3px 0 0 var(--sr-good);
}
.sr-choice.sr-wrong-pick {
    border-color: var(--sr-bad);
    background: var(--sr-bad-bg);
    box-shadow: inset 3px 0 0 var(--sr-bad);
}
.sr-verdict {
    font-weight: 700;
    margin-bottom: 0.3em;
}
.sr-verdict.sr-good {
    color: var(--sr-good);
}
.sr-verdict.sr-bad {
    color: var(--sr-bad);
}
.sr-wrong {
    background: var(--sr-panel);
    border-left: 4px solid var(--sr-bad);
    border-radius: 4px;
    padding: 0.5em 0.7em;
    margin: 0.5em 0;
}
.sr-wrong:empty {
    display: none;
}
.sr-wrong::before {
    content: attr(data-for) " is wrong: ";
    font-weight: 700;
}
.sr-answer-line {
    margin: 0.8em 0 0.6em;
}
.sr-answer-letter {
    font-weight: 700;
    color: var(--sr-good);
}
.sr-selfgrade {
    font-size: 0.75em;
    color: var(--sr-muted);
    margin-top: 0.8em;
}
"""
)

_COMPARE_CSS = (
    _BASE_CSS
    + """\
.sr-columns {
    display: flex;
    align-items: stretch;
    gap: 0.8em;
    flex-wrap: wrap;
    margin-bottom: 0.9em;
}
.sr-col {
    flex: 1 1 12em;
    min-width: 11em;
    background: var(--sr-panel);
    border: 1px solid var(--sr-border);
    border-radius: 8px;
    padding: 0.6em 0.75em;
}
.sr-col-title {
    font-weight: 700;
    border-bottom: 1px solid var(--sr-border);
    padding-bottom: 0.25em;
    margin-bottom: 0.35em;
}
.sr-discriminator {
    font-weight: 600;
    background: var(--sr-panel);
    border-left: 4px solid var(--sr-accent);
    border-radius: 4px;
    padding: 0.5em 0.7em;
}
"""
)

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_WORKED_FRONT = """\
<div class="sr-card sr-worked">
<div class="sr-kicker">Worked example</div>
<div class="sr-title">{{Title}}</div>
<div class="sr-prompt">{{Prompt}}</div>
<div class="sr-instruction">Try each step yourself, then reveal the full worked solution.</div>
</div>
"""

# [R9]: the reveal side shows the numbered steps AND the rationale block.
_WORKED_BACK = """\
<div class="sr-card sr-worked">
<div class="sr-kicker">Worked example</div>
<div class="sr-title">{{Title}}</div>
<div class="sr-prompt">{{Prompt}}</div>
<div class="sr-steps">{{Steps}}</div>
<div class="sr-feedback">
<div class="sr-rationale"><span class="sr-why-label">Why this works:</span> {{Rationale}}</div>
{{#Source}}<div class="sr-source">{{Source}}</div>{{/Source}}
</div>
</div>
"""


def _solve_front(self_explain: bool) -> str:
    """The solve front. Tap a choice -> mark it, reveal correct vs wrong
    (green/red) and the per-choice rationales; grading stays with the normal
    answer buttons. The correct letter is only exposed as a hidden data
    attribute, and the Wrong*/Rationale fields sit inside a [hidden] panel,
    so nothing leaks visibly before the learner commits.
    """
    self_explain_banner = (
        '<div class="sr-self-explain">{{SelfExplainPrompt}}</div>\n'
        if self_explain
        else ""
    )
    body = (
        '<div class="sr-card sr-solve sr-solve-front" data-correct="{{Correct}}">\n'
        "{{#Title}}"
        '<div class="sr-kicker">{{Title}}</div>'
        "{{/Title}}\n" + self_explain_banner + '<div class="sr-stem">{{Stem}}</div>\n'
        '<div class="sr-choices">\n'
        '<button type="button" class="sr-choice" data-letter="A"><span class="sr-choice-letter">A</span><span class="sr-choice-text">{{ChoiceA}}</span></button>\n'
        '<button type="button" class="sr-choice" data-letter="B"><span class="sr-choice-letter">B</span><span class="sr-choice-text">{{ChoiceB}}</span></button>\n'
        '<button type="button" class="sr-choice" data-letter="C"><span class="sr-choice-letter">C</span><span class="sr-choice-text">{{ChoiceC}}</span></button>\n'
        "</div>\n"
        '<div class="sr-feedback" hidden>\n'
        '<div class="sr-verdict"></div>\n'
        '<div class="sr-rationale"><span class="sr-why-label">Why {{Correct}} is correct:</span> {{Rationale}}</div>\n'
        '<div class="sr-wrong" data-for="A">{{WrongA}}</div>\n'
        '<div class="sr-wrong" data-for="B">{{WrongB}}</div>\n'
        '<div class="sr-wrong" data-for="C">{{WrongC}}</div>\n'
        '<div class="sr-selfgrade">Show the answer, then grade yourself with the usual buttons.</div>\n'
        "</div>\n"
        "</div>\n" + _SOLVE_FRONT_SCRIPT
    )
    if self_explain:
        # The conditional wraps the WHOLE front: with an empty
        # SelfExplainPrompt the front renders blank and Anki generates no
        # card for this ordinal - --no-self-explain decks stay
        # duplicate-free even in vanilla Anki.
        return "{{#SelfExplainPrompt}}\n" + body + "{{/SelfExplainPrompt}}\n"
    return body


# Plain ES5 in an IIFE: runs unmodified in the desktop webview and
# AnkiDroid's webview; re-execution is guarded by data-sr-bound; no pycmd,
# no bridge, no external deps.
_SOLVE_FRONT_SCRIPT = """\
<script>
(function () {
    var root = document.querySelector(".sr-solve-front:not([data-sr-bound])");
    if (!root) { return; }
    root.setAttribute("data-sr-bound", "1");
    var correct = (root.getAttribute("data-correct") || "").trim();
    var buttons = root.querySelectorAll(".sr-choice");
    var feedback = root.querySelector(".sr-feedback");
    var verdict = root.querySelector(".sr-verdict");
    function reveal(chosen) {
        if (root.className.indexOf("sr-revealed") !== -1) { return; }
        root.className += " sr-revealed";
        for (var i = 0; i < buttons.length; i++) {
            var button = buttons[i];
            var letter = button.getAttribute("data-letter");
            button.disabled = true;
            if (letter === correct) {
                button.className += " sr-correct";
            } else if (letter === chosen) {
                button.className += " sr-wrong-pick";
            }
        }
        if (verdict) {
            if (chosen === correct) {
                verdict.className += " sr-good";
                verdict.textContent = "Correct: " + correct + ".";
            } else {
                verdict.className += " sr-bad";
                verdict.textContent = "You picked " + chosen + " - the answer is " + correct + ".";
            }
        }
        if (feedback) {
            feedback.removeAttribute("hidden");
            // MathJax may skip content that was hidden at initial typeset;
            // re-typeset the revealed block when the API is available
            // (progressive enhancement - plain text renders regardless).
            if (window.MathJax && window.MathJax.typesetPromise) {
                window.MathJax.typesetPromise([feedback]).catch(function () {});
            }
        }
    }
    function bind(button) {
        button.addEventListener("click", function () {
            reveal(button.getAttribute("data-letter"));
        });
    }
    for (var i = 0; i < buttons.length; i++) { bind(buttons[i]); }
})();
</script>
"""

# The back is a complete no-JS fallback [R9]: the static text names the
# correct answer and shows every rationale even if the script never ran.
# The script below is progressive enhancement (green highlight) only.
_SOLVE_BACK = """\
<div class="sr-card sr-solve sr-solve-back" data-correct="{{Correct}}">
{{#Title}}<div class="sr-kicker">{{Title}}</div>{{/Title}}
<div class="sr-stem">{{Stem}}</div>
<div class="sr-answer-line">Correct answer: <span class="sr-answer-letter">{{Correct}}</span></div>
<div class="sr-choices sr-choices-static">
<div class="sr-choice" data-letter="A"><span class="sr-choice-letter">A</span><span class="sr-choice-text">{{ChoiceA}}</span></div>
<div class="sr-choice" data-letter="B"><span class="sr-choice-letter">B</span><span class="sr-choice-text">{{ChoiceB}}</span></div>
<div class="sr-choice" data-letter="C"><span class="sr-choice-letter">C</span><span class="sr-choice-text">{{ChoiceC}}</span></div>
</div>
<div class="sr-feedback">
<div class="sr-rationale"><span class="sr-why-label">Why {{Correct}} is correct:</span> {{Rationale}}</div>
<div class="sr-wrong" data-for="A">{{WrongA}}</div>
<div class="sr-wrong" data-for="B">{{WrongB}}</div>
<div class="sr-wrong" data-for="C">{{WrongC}}</div>
{{#Source}}<div class="sr-source">{{Source}}</div>{{/Source}}
</div>
</div>
<script>
(function () {
    var root = document.querySelector(".sr-solve-back:not([data-sr-bound])");
    if (!root) { return; }
    root.setAttribute("data-sr-bound", "1");
    var correct = (root.getAttribute("data-correct") || "").trim();
    var choices = root.querySelectorAll(".sr-choice");
    for (var i = 0; i < choices.length; i++) {
        if (choices[i].getAttribute("data-letter") === correct) {
            choices[i].className += " sr-correct";
        }
    }
})();
</script>
"""

_COMPARE_FRONT = """\
<div class="sr-card sr-compare">
<div class="sr-kicker">Compare</div>
{{#Title}}<div class="sr-title">{{Title}}</div>{{/Title}}
<div class="sr-columns">
<div class="sr-col">
<div class="sr-col-title">{{LeftTitle}}</div>
<div class="sr-col-body">{{LeftBody}}</div>
</div>
<div class="sr-col">
<div class="sr-col-title">{{RightTitle}}</div>
<div class="sr-col-body">{{RightBody}}</div>
</div>
</div>
<div class="sr-discriminator">{{Discriminator}}</div>
</div>
"""

_COMPARE_BACK = """\
{{FrontSide}}
<div class="sr-card sr-compare-answer">
<div class="sr-feedback">
<div class="sr-rationale"><span class="sr-why-label">The discrimination:</span> {{Rationale}}</div>
{{#Source}}<div class="sr-source">{{Source}}</div>{{/Source}}
</div>
</div>
"""

# ---------------------------------------------------------------------------
# The note type specs (what the deck builder instantiates)
# ---------------------------------------------------------------------------

WORKED = {
    "name": WORKED_NOTETYPE_NAME,
    "fields": ["Title", "Prompt", "Steps", "Rationale", "Source"],
    "templates": [
        {"name": "Worked", "qfmt": _WORKED_FRONT, "afmt": _WORKED_BACK},
    ],
    "css": _WORKED_CSS,
}

# Ord 0 = plain MCQ; ord 1 = the self-explanation variant [R16]. The engine
# serves exactly one per note via the self_explain_enabled deck-config flag
# (fade.rs admit_rung: lowest ordinal when off, highest when on).
SOLVE_MCQ = {
    "name": SOLVE_NOTETYPE_NAME,
    "fields": [
        "Title",
        "Stem",
        "ChoiceA",
        "ChoiceB",
        "ChoiceC",
        "Correct",
        "Rationale",
        "WrongA",
        "WrongB",
        "WrongC",
        "SelfExplainPrompt",
        "Source",
    ],
    "templates": [
        {
            "name": "Solve",
            "qfmt": _solve_front(self_explain=False),
            "afmt": _SOLVE_BACK,
        },
        {
            "name": "Solve + Self-Explain",
            "qfmt": _solve_front(self_explain=True),
            "afmt": _SOLVE_BACK,
        },
    ],
    "css": _SOLVE_CSS,
}

COMPARE = {
    "name": COMPARE_NOTETYPE_NAME,
    "fields": [
        "Title",
        "LeftTitle",
        "LeftBody",
        "RightTitle",
        "RightBody",
        "Discriminator",
        "Rationale",
        "Source",
    ],
    "templates": [
        {"name": "Compare", "qfmt": _COMPARE_FRONT, "afmt": _COMPARE_BACK},
    ],
    "css": _COMPARE_CSS,
}

#: The custom note types the builder creates (faded rides stock Cloze).
NOTETYPES = (WORKED, SOLVE_MCQ, COMPARE)

#: item kind -> custom note type spec (cloze handled via the stock notetype).
NOTETYPE_FOR_KIND = {
    "worked": WORKED,
    "mcq": SOLVE_MCQ,
    "compare": COMPARE,
}

# ---------------------------------------------------------------------------
# Field-content helpers (item record -> note field strings)
# ---------------------------------------------------------------------------


def _escape(value: Any) -> str:
    """Item text is plain text; escape it for the HTML field context.
    Cloze braces survive untouched (html.escape only rewrites & < > ")."""
    return html.escape(str(value))


def worked_steps_html(steps: Sequence[str]) -> str:
    items = "".join(f"<li>{_escape(step)}</li>" for step in steps)
    return f'<ol class="sr-steps-list">{items}</ol>'


def _prettify_doc(doc: str) -> str:
    """Corpus filename -> readable name: "bond_pricing.md" -> "Bond pricing"."""
    name = doc.rsplit("/", 1)[-1]
    name = name.removesuffix(".md").replace("_", " ").replace("-", " ").strip()
    return name[:1].upper() + name[1:]


def _prettify_loc(loc: str) -> str:
    """Heading slug -> readable section: "#quoted-yields" -> "Quoted yields"."""
    section = loc.lstrip("#").replace("-", " ").strip()
    return section[:1].upper() + section[1:]


def source_html(source: Mapping[str, str]) -> str:
    """The named source [R21], shown inside the feedback block.

    Item records carry machine coordinates (doc = corpus filename, loc =
    heading slug, e.g. "duration.md" / "#compounding-conventions"); the
    card shows a labelled, readable citation instead of the raw form.
    """
    doc = _escape(_prettify_doc(str(source.get("doc", ""))))
    loc = _escape(_prettify_loc(str(source.get("loc", ""))))
    passage = _escape(source.get("passage", ""))
    reference = f"{doc} &mdash; {loc}" if loc else doc
    out = (
        '<span class="sr-source-label">Source:</span> '
        f'<span class="sr-source-ref">{reference}</span>'
    )
    if passage:
        out += f' <span class="sr-source-passage">&ldquo;{passage}&rdquo;</span>'
    return out


def worked_note_fields(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "Title": _escape(item["title"]),
        "Prompt": _escape(item["prompt"]),
        "Steps": worked_steps_html(item["worked_steps"]),
        "Rationale": _escape(item["rationale"]),
        "Source": source_html(item["source"]),
    }


def faded_cloze_fields(item: Mapping[str, Any]) -> tuple[str, str]:
    """(Text, Back Extra) for a stock-Cloze faded note.

    Text = prompt + the item's native cloze markup (>= 2 indices, so the
    engine's fade_order has siblings to sequence); Back Extra = rationale +
    source, which the stock cloze answer template renders - the [R9]
    feedback step.
    """
    text = f"{_escape(item['prompt'])}<br><br>\n{_escape(item['cloze_text'])}"
    back_extra = (
        f"{_escape(item['rationale'])}<br>\n"
        f"<small>{source_html(item['source'])}</small>"
    )
    return text, back_extra


def solve_note_fields(
    item: Mapping[str, Any],
    include_self_explain: bool = True,
    self_explain_prompt: str = DEFAULT_SELF_EXPLAIN_PROMPT,
) -> dict[str, str]:
    """Solve-MCQ note fields. The wrong-letter rationales land in Wrong<X>
    (the correct letter's slot stays empty; the template hides empty ones),
    and SelfExplainPrompt stays empty under --no-self-explain so template
    ord 1 generates no card.
    """
    choices = item["choices"]
    correct = item["correct"]
    wrong = {letter: "" for letter in CHOICE_LETTERS}
    for letter, why in item["distractor_rationales"].items():
        wrong[letter] = _escape(why)
    return {
        "Title": _escape(item["title"]),
        "Stem": _escape(item["stem"]),
        "ChoiceA": _escape(choices["A"]),
        "ChoiceB": _escape(choices["B"]),
        "ChoiceC": _escape(choices["C"]),
        "Correct": correct,
        "Rationale": _escape(item["rationale"]),
        "WrongA": wrong["A"],
        "WrongB": wrong["B"],
        "WrongC": wrong["C"],
        "SelfExplainPrompt": _escape(self_explain_prompt)
        if include_self_explain
        else "",
        "Source": source_html(item["source"]),
    }


def compare_note_fields(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "Title": _escape(item["title"]),
        "LeftTitle": _escape(item["left_title"]),
        "LeftBody": _escape(item["left_body"]),
        "RightTitle": _escape(item["right_title"]),
        "RightBody": _escape(item["right_body"]),
        "Discriminator": _escape(item["discriminator"]),
        "Rationale": _escape(item["rationale"]),
        "Source": source_html(item["source"]),
    }
