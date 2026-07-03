# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""One-command AIG pipeline entry (M1): generators -> gates -> grounding.

    python3 run_pipeline.py --backend mock --out ../items/generated.jsonl

Runs the parameterized numeric generators (the guaranteed-runnable shipped
content), the optional LLM drafter/critic path (mock backend = deterministic
canned drafts, exercised through the same gates but NOT emitted as content;
real backends emit items that clear every gate), the machine validation
gates on EVERY item, retrieval-for-grounding (named source per item), and
writes:

- ``items/generated.jsonl``  - emitted items (provenance.graded=false)
- ``eval/validation_report.json`` - every gate decision, per item
- ``eval/retrieval_eval.json`` / ``.md`` - the [R21] arm comparison
- ``eval/confusability_report.json`` + ``eval/confusable_markers.json``
  - the M1b computed-signal self-test (synthetic revlog), unless skipped

With ``--backend mock`` the run is fully offline and deterministic. The
dense retrieval arm is OPT-IN via ``SPEEDRUN_DENSE=1`` (the
torch/sentence-transformers stack is ABI-fragile; the guaranteed path is
stdlib BM25 - see aig/retrieval.py); the archived full-stack eval lives in
``eval/archive/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_SPEEDRUN_DIR = Path(__file__).resolve().parents[1]
if str(_SPEEDRUN_DIR) not in sys.path:
    sys.path.insert(0, str(_SPEEDRUN_DIR))

from aig import confusability, gates, generators, models, retrieval  # noqa: E402

# Concept briefs for the LLM drafter path: (cluster, topic, concept,
# misconception ids, declared grounding passage).
LLM_CONCEPTS = [
    (
        "fi::duration",
        "fixed_income",
        "converting Macaulay duration to modified duration on a semiannual-pay bond",
        ["duration.modified_vs_macaulay", "duration.compounding_confusion"],
        "duration.md#modified-duration",
    ),
    (
        "qm::tvm",
        "quantitative_methods",
        "future value of a lump sum with intra-year compounding",
        ["tvm.rate_per_period", "tvm.simple_vs_compound"],
        "tvm.md#future-value-of-a-single-sum",
    ),
    (
        "fsa::inventory",
        "financial_statement_analysis",
        "restating LIFO inventory to FIFO using the LIFO reserve",
        ["inventory.lifo_reserve_direction", "inventory.reserve_period_mixup"],
        "inventory.md#lifo-reserve",
    ),
]


def _llm_item_from_draft(
    draft: dict[str, Any],
    cluster: str,
    topic: str,
    passage: str,
    generator_id: str,
) -> dict[str, Any]:
    """Wrap a drafted MCQ into a schema-conforming item dict."""
    return {
        "schema": generators.SCHEMA_VERSION,
        "kind": "mcq",
        "rung": "solve",
        "topic": topic,
        "cluster": cluster,
        "interactivity": "high",
        "title": str(draft.get("title", f"LLM item ({cluster})")),
        "stem": draft.get("stem", ""),
        "choices": draft.get("choices", {}),
        "correct": draft.get("correct", ""),
        "distractor_rationales": draft.get("distractor_rationales", {}),
        "misconceptions": draft.get("misconceptions", {}),
        "rationale": draft.get("rationale", ""),
        "source": {},
        "provenance": {"generator": generator_id, "gates": [], "graded": False},
        "_aig": {
            "generator": generator_id,
            "declared_passage": passage,
            "query": draft.get("stem", ""),
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    out_path = Path(args.out)
    eval_dir = Path(args.eval_dir)
    report = gates.ValidationReport()

    # ------------------------------------------------------------------ 1.
    # Parameterized generators: the guaranteed-runnable shipped content.
    items = generators.generate_all(seed=args.seed)

    # ------------------------------------------------------------------ 2.
    # LLM drafter/critic path. Mock drafts are canned fixtures: they are
    # run through the whole gate chain to exercise/validate the path, but
    # only REAL backends emit LLM items as content.
    llm_items: list[dict[str, Any]] = []
    llm_path = None
    emit_llm = args.backend != "mock" and args.llm_drafts > 0
    if args.llm_drafts > 0:
        llm_path = models.make_llm_path(args.backend, k_samples=args.k_samples)
        drafter_name = getattr(llm_path.drafter, "name", args.backend)
        drafter_model = getattr(llm_path.drafter, "model", "")
        gen_id = f"llm:{drafter_name}" + (f":{drafter_model}" if drafter_model else "")
        for cluster, topic, concept, mids, passage in LLM_CONCEPTS[: args.llm_drafts]:
            draft, audit = llm_path.generate_validated(topic, cluster, concept, mids)
            if draft is None:
                report.record(
                    f"llm/{cluster}",
                    {
                        "title": f"LLM draft ({concept})",
                        "kind": "mcq",
                        "cluster": cluster,
                        "provenance": {"generator": gen_id},
                    },
                    [gates.GateResult("critic", False, str(audit.get("outcome")))],
                    emitted=False,
                    extra={"llm_audit": audit, "llm_path_demo": not emit_llm},
                )
                continue
            item = _llm_item_from_draft(draft, cluster, topic, passage, gen_id)
            item["_aig"]["llm_audit"] = audit
            llm_items.append(item)

    # ------------------------------------------------------------------ 3.
    # Retrieval-for-grounding: index the corpus, tune BM25 (and the dense
    # arm when available) on the train split of the synthetic qrels.
    if args.no_dense:
        real_loader = retrieval.try_load_dense
        retrieval.try_load_dense = lambda p: (None, None, "disabled by --no-dense")  # type: ignore[assignment]
    grounder = retrieval.GroundingRetriever(
        args.corpus_dir, items + llm_items, seed=args.seed
    )
    if args.no_dense:
        retrieval.try_load_dense = real_loader  # type: ignore[assignment]

    corpus_texts = {p.pid: p.title + "\n" + p.text for p in grounder.passages}
    wall = gates.LeakageWall(
        reference_pdf=args.reference_pdf, corpus_texts=corpus_texts
    )

    # ------------------------------------------------------------------ 4.
    # Gates on EVERY item (param + llm), grounding, final schema check.
    emitted: list[dict[str, Any]] = []
    for idx, item in enumerate(items + llm_items):
        is_llm = item in llm_items
        item_id = (
            f"{item['cluster']}/{item['kind']}/{item['provenance']['generator']}/{idx}"
        )
        results = [
            gates.gate_numeric(item),
            gates.gate_solve_check(item, llm_path=llm_path if is_llm else None),
            gates.gate_rationale(item),
            gates.gate_leakage(item, wall),
        ]
        if is_llm:
            audit = item["_aig"].get("llm_audit", {})
            results.append(
                gates.GateResult(
                    "critic",
                    bool(audit.get("critic_accept")),
                    "; ".join(audit.get("critic_reasons", [])),
                )
            )
            results.append(
                gates.GateResult(
                    "consensus",
                    bool(audit.get("consensus")),
                    f"solver picks: {audit.get('solver_picks')}",
                )
            )
        if gates.all_passed(results):
            grounder.ground_item(item)
            results.append(gates.gate_schema(item))
        will_emit = gates.all_passed(results) and (not is_llm or emit_llm)
        if will_emit:
            item["provenance"]["gates"] = [r.gate for r in results]
            emitted.append(item)
        report.record(
            item_id,
            item,
            results,
            emitted=will_emit,
            extra=(
                {"llm_path_demo": True}
                if is_llm and not emit_llm and gates.all_passed(results)
                else {}
            ),
        )

    # ------------------------------------------------------------------ 5.
    # Write items JSONL (private keys stripped).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for item in emitted:
            f.write(json.dumps(generators.strip_private(item), sort_keys=True) + "\n")

    # ------------------------------------------------------------------ 6.
    # Eval reports.
    evaluation = grounder.evaluate()
    retrieval.write_eval_reports(
        evaluation, eval_dir / "retrieval_eval.json", eval_dir / "retrieval_eval.md"
    )
    report.write(
        eval_dir / "validation_report.json",
        meta={
            "backend": args.backend,
            "seed": args.seed,
            "llm_items_emitted": emit_llm,
            "llm_path_note": (
                "mock backend: canned LLM drafts were run through every gate "
                "to exercise the path but are NOT emitted as content; the "
                "parameterized generators provide the shipped items"
                if args.backend == "mock"
                else ""
            ),
        },
    )

    conf_result = None
    if args.confusability == "self-test":
        conf_result = confusability.run(
            revlog=None,
            notes=None,
            out_report=eval_dir / "confusability_report.json",
            out_markers=eval_dir / "confusable_markers.json",
            self_test=True,
        )
    elif args.confusability != "skip":
        conf_result = confusability.run(
            revlog=args.confusability,
            notes=None,
            out_report=eval_dir / "confusability_report.json",
            out_markers=eval_dir / "confusable_markers.json",
        )

    # ------------------------------------------------------------------ 7.
    # Console summary.
    by_cluster_kind = Counter((i["cluster"], i["kind"]) for i in emitted)
    summary = {
        "emitted_total": len(emitted),
        "by_cluster_kind": {
            f"{c}/{k}": n for (c, k), n in sorted(by_cluster_kind.items())
        },
        "gate_summary": report.summary(),
        "retrieval_honest_claim": evaluation["honest_claim"],
        "confusability": conf_result.reason if conf_result else "skipped",
        "out": str(out_path),
    }
    print(json.dumps(summary, indent=1))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--backend",
        choices=["mock", "claude-cli", "openai-compatible"],
        default="mock",
    )
    ap.add_argument("--out", default=str(_SPEEDRUN_DIR / "items" / "generated.jsonl"))
    ap.add_argument("--eval-dir", default=str(_SPEEDRUN_DIR / "eval"))
    ap.add_argument("--corpus-dir", default=str(_SPEEDRUN_DIR / "corpus"))
    ap.add_argument(
        "--reference-pdf",
        default=str(_SPEEDRUN_DIR / "reference" / "cfa_l1_official_sample_2025.pdf"),
        help="local CFA sample PDF for the leakage wall (never read into prompts)",
    )
    ap.add_argument("--seed", type=int, default=generators.DEFAULT_SEED)
    ap.add_argument(
        "--llm-drafts",
        type=int,
        default=len(LLM_CONCEPTS),
        help="how many LLM concepts to draft (0 disables the LLM path)",
    )
    ap.add_argument("--k-samples", type=int, default=3, help="solver consensus samples")
    ap.add_argument(
        "--no-dense",
        action="store_true",
        help="force the stdlib-only retrieval path (the default unless "
        "SPEEDRUN_DENSE=1 is set; the ML stack is opt-in because it is "
        "ABI-fragile - see aig/retrieval.py)",
    )
    ap.add_argument(
        "--confusability",
        default="self-test",
        help="'self-test' (synthetic revlog), 'skip', or a revlog path",
    )
    args = ap.parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
