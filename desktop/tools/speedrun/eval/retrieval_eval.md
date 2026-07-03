# Retrieval-for-grounding eval [R21]

SYNTHETIC qrels: each item's generator-declared grounding passage is its relevance label. Self-referential qrels are weaker than human relevance judgments; treat the numbers as a consistency check, not an IR benchmark.

split by cluster (fixed seed): tuning uses the train clusters; eval uses the held-out cluster 'qm::tvm' (provenance wall: no eval qrel passage is tuned against a paraphrase of the same item).

- passages: 26
- train queries: 40
- eval queries (held-out cluster): 22
- BM25 tuned: {'k1': 0.6, 'b': 0.3}
- dense tuned: {'repr': 'title_text', 'score': 'cosine'}
- fusion: RRF(k=60), rerank top-10

| arm         | available | P@1   | P@5 (hit) | median latency (ms) |
| ----------- | --------- | ----- | --------- | ------------------- |
| bm25_tuned  | yes       | 0.500 | 0.955     | 0.07                |
| dense_tuned | yes       | 0.455 | 1.000     | 12.15               |
| rrf         | yes       | 0.545 | 1.000     | 12.34               |
| rrf_rerank  | yes       | 0.727 | 1.000     | 42.64               |

**Honest claim:** RRF+rerank beat BOTH tuned BM25 and tuned dense at the same cutoff on precision@1/@5 over the held-out cluster (N=22 queries - small-N caveat: differences of one or two queries flip these comparisons; the synthetic self-referential qrels further weaken the claim).

with a single relevant passage per query, precision@5 is reported as hit-rate@5 (success@5), the standard practice.
