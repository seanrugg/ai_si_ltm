# Embedding benchmark (P8)

CPU-only comparison of embedding models for semantic search over LTMS record
summaries, run with `scripts/bench_embeddings.py` (fastembed). Results below.

## Machine

Apple M2 Ultra (arm64, 24 CPUs), 128 GB RAM, macOS/Darwin 27.0.0.
This is a dev machine, so `--threads 2` was passed to fastembed to simulate the
small VPS CPU that P8 will run on; the raw core count would otherwise flatter
the numbers.

## Settings

- Threads: 2 (passed to `fastembed.TextEmbedding(threads=2)`)
- fastembed version: 0.9.0
- Summaries: 42 LTMS record summaries (Z3-R0/Vector living-room domain)
- Questions: 10 natural-language questions, scored with recall@1 and recall@3

## Results

| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | R@1 | R@3 |
|-------|----------|-------------|-------|---------------|-----|-----|-----|
| BAAI/bge-small-en-v1.5 | 0.09 | 3.6 | 248.3 | 279.6 | 384 | 0.2 | 0.7 |
| sentence-transformers/all-MiniLM-L6-v2 | 0.06 | 1.8 | 534.1 | 388.1 | 384 | 0.2 | 0.7 |
| nomic-ai/nomic-embed-text-v1.5 | 0.33 | 15.8 | 58.6 | 1030.8 | 768 | 0.2 | 0.5 |

## Which model to pick

**BAAI/bge-small-en-v1.5.** It ties the best retrieval quality (R@3 = 0.7) while
using the least memory (279.6 MB peak RSS) and loading quickly, which matters on
a small, memory-constrained CPU-only VPS. Its 384-dimensional vectors also match
the plan for P8. `all-MiniLM-L6-v2` is the faster runner-up (1.8 ms per query,
534 emb/s) with identical recall, but its higher RAM use (388.1 MB) makes it the
second choice. `nomic-embed-text-v1.5` is the worst fit: slowest (15.8 ms),
by far the most memory (1030.8 MB), and the lowest recall (R@3 = 0.5), so it's
not worth the 768-dimensional vectors.
