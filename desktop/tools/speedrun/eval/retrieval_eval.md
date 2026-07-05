# Retrieval-for-grounding eval [R21]

SYNTHETIC qrels: each item's generator-declared grounding passage is its relevance label. Self-referential qrels are weaker than human relevance judgments; treat the numbers as a consistency check, not an IR benchmark.

split by cluster (fixed seed): tuning uses the train clusters; eval uses the held-out cluster 'qm::tvm' (provenance wall: no eval qrel passage is tuned against a paraphrase of the same item).

- passages: 26
- train queries: 40
- eval queries (held-out cluster): 22
- BM25 tuned: {'k1': 0.6, 'b': 0.3}
- vector_hash tuned: {'ngram': [4, 5], 'dim': 65536} (feature-hashed char-n-gram TF-IDF + cosine, stdlib; not a neural embedding)
- dense tuned: {'note': 'dense arm is opt-in (set SPEEDRUN_DENSE=1); the ML stack is ABI-fragile on this host, and the guaranteed path is stdlib BM25 - see eval/archive/ for the full-stack run'}
- fusion: RRF(k=60), rerank top-10
- production grounding arm (default env): bm25 (rrf_stdlib only TIED bm25 on train - kept bm25 (status quo)) - rule: prefer rrf_stdlib over bm25 for grounding iff rrf_stdlib wins on the train split: >= bm25 on BOTH metrics (P@1, P@5-hit) and strictly better on at least one; a tie keeps bm25. Decided at construction time from the train split only - the held-out cluster is never used (no test leakage)

| arm               | available | P@1   | P@5 (hit) | median latency (ms) |
| ----------------- | --------- | ----- | --------- | ------------------- |
| bm25_tuned        | yes       | 0.500 | 0.955     | 0.07                |
| vector_hash_tuned | yes       | 0.182 | 0.909     | 0.32                |
| rrf_stdlib        | yes       | 0.364 | 0.955     | 0.43                |
| dense_tuned       | NO        | -     | -         | -                   |
| rrf               | NO        | -     | -         | -                   |
| rrf_rerank        | NO        | -     | -         | -                   |

**Honest claim:** rrf_stdlib did NOT beat both stdlib baselines at the same cutoff on precision@1/@5 over the held-out cluster (N=22 queries - small-N caveat: differences of one or two queries flip these comparisons; the synthetic self-referential qrels further weaken the claim). Fallback committed result: the archived full-stack run (eval/archive/retrieval_eval_fullstack_20260703.md, session of 2026-07-03 on the pinned torch / sentence-transformers stack) reported rrf_rerank P@1 0.727 / P@5 1.000 beating tuned BM25 (0.500 / 0.955) and tuned dense (0.455 / 1.000) on the same split - quoted verbatim from that DIFFERENT session and stack, not reproduced in this environment.

with a single relevant passage per query, precision@5 is reported as hit-rate@5 (success@5), the standard practice.

the full-stack (torch) side-by-side from 2026-07-03 is archived in eval/archive/retrieval_eval_fullstack_20260703.md.
