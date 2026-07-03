# Speedrun item schema (v1) — contract between the AIG pipeline and the deck builder

One JSON object per generated/authored item, stored as JSON Lines in
`desktop/tools/speedrun/items/*.jsonl`. The AIG pipeline (`aig/`) **emits**
records; the deck builder (`build_ladder_deck.py`) **consumes** them and turns
them into notes. Both sides validate against this schema; the deck builder
must reject records that violate it (the feedback-invariant lint [R9] lives
there).

## Common fields (all kinds)

| field           | type   | required | notes                                                                         |
| --------------- | ------ | -------- | ----------------------------------------------------------------------------- |
| `schema`        | string | yes      | literal `"speedrun-item-v1"`                                                  |
| `kind`          | string | yes      | `worked` \| `cloze` \| `mcq` \| `compare`                                     |
| `rung`          | string | yes      | `worked` \| `faded` \| `solve` \| `compare` (compare sits outside the ladder) |
| `topic`         | string | yes      | `cfa::topic::` suffix, e.g. `fixed_income`                                    |
| `cluster`       | string | yes      | `cluster::` suffix, e.g. `fi::duration`                                       |
| `interactivity` | string | yes      | `high` \| `low` ([R17] element-interactivity tag)                             |
| `title`         | string | yes      | short human name, becomes the first field / sort field                        |
| `rationale`     | string | yes      | the feedback shown after reveal ([R9] — **must be non-empty**)                |
| `source`        | object | yes      | named source: `{"doc": str, "loc": str, "passage": str}` ([R21])              |
| `provenance`    | object | yes      | see below                                                                     |
| `tags_extra`    | array  | no       | extra tags verbatim (e.g. `confusable::high` written by the computed pass)    |

### `provenance`

```json
{
  "generator": "param:duration_gap_v1" | "llm:<drafter-model>",
  "gates": ["numeric", "solve_check", "critic", "consensus"],
  "graded": false
}
```

`graded: false` ⇒ the note gets the `aig::ungraded` tag and **never feeds
readiness** ([R24]). Set `graded: true` only for items whose live
point-biserial has been checked (none at authoring time; the flag is flipped
later by the retirement tool).

## Kind-specific fields

### `worked`

| field          | type   | required | notes                            |
| -------------- | ------ | -------- | -------------------------------- |
| `prompt`       | string | yes      | the problem statement            |
| `worked_steps` | array  | yes      | ordered solution steps (strings) |

### `cloze` (the faded rung)

| field        | type   | required | notes                                                                                                          |
| ------------ | ------ | -------- | -------------------------------------------------------------------------------------------------------------- |
| `prompt`     | string | yes      | the problem statement                                                                                          |
| `cloze_text` | string | yes      | native Anki cloze markup; **≥ 2 cloze indices** (`{{c1::…}}`, `{{c2::…}}`) so fading has an order to work with |

### `mcq` (the solve rung — exam-congruent A/B/C single-best)

| field                   | type   | required | notes                                                                                        |
| ----------------------- | ------ | -------- | -------------------------------------------------------------------------------------------- |
| `stem`                  | string | yes      | application-style stem                                                                       |
| `choices`               | object | yes      | exactly keys `A`,`B`,`C` (CFA L1 format)                                                     |
| `correct`               | string | yes      | one of `A`,`B`,`C`                                                                           |
| `distractor_rationales` | object | yes      | why each wrong choice is wrong (misconception-grounded, [R22]); keys = the two wrong letters |
| `misconceptions`        | object | no       | wrong letter → misconception id (e.g. `duration.modified_vs_macaulay`)                       |

### `compare` ([R20] side-by-side for the tightest confusables)

| field           | type   | required | notes                                     |
| --------------- | ------ | -------- | ----------------------------------------- |
| `left_title`    | string | yes      | e.g. `Macaulay duration`                  |
| `left_body`     | string | yes      |                                           |
| `right_title`   | string | yes      | e.g. `Modified duration`                  |
| `right_body`    | string | yes      |                                           |
| `discriminator` | string | yes      | the prompt that forces the discrimination |

## Tagging (M2 — no schema change, native sync)

The deck builder derives tags mechanically:

- `cfa::topic::<topic>`
- `cluster::<cluster>`
- `rung::<rung>` (not for `compare`; compare cards get `rung::compare` for
  bookkeeping but the engine only gates `worked|faded|solve`)
- `interactivity::<interactivity>`
- `aig::graded` / `aig::ungraded` from `provenance.graded`
- everything in `tags_extra` verbatim

## Feedback invariant lint ([R9], enforced at build time)

The deck builder refuses to emit a note when:

- `rationale` is empty/whitespace, or
- kind `mcq` and any wrong letter lacks a `distractor_rationales` entry, or
- kind `cloze` and `cloze_text` has fewer than 2 cloze indices, or
- kind `worked` and `worked_steps` is empty, or
- the rendered template for the kind lacks a reveal/feedback section
  (template-level check, run once per note type).
