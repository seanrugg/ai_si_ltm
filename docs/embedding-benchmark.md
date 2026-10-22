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
- Summaries: 40 distinct LTMS record summaries (Z3-R0/Vector domain; kitchen,
  hallway, dock, battery, times, people, objects)
- Questions: 10 paraphrased natural-language questions, each with exactly one
  right answer; scored with recall@1 and recall@3 using cosine similarity

## Notes on method

- Each model runs in its **own child process**, so peak RSS is per-model and not
  cumulative. Peak memory is measured after the retrieval step (all summary
  vectors resident), using `resource.getrusage` (bytes on macOS, KiB on Linux).
- Batch throughput is the median of 5 repeats (the first run is warm-up).
- `nomic-ai/nomic-embed-text-v1.5` requires `search_query: ` on questions and
  `search_document: ` on documents; fastembed 0.9 does not add these prefixes
  automatically, so they are added for that model only (its own model description
  marks the prefixes as "necessary"). BGE and MiniLM are embedded raw.

## Results

| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | R@1 | R@3 |
|-------|----------|-------------|-------|---------------|-----|-----|-----|
| BAAI/bge-small-en-v1.5 | 0.09 | 5.2 | 218.2 | 298.4 | 384 | 0.9 | 1.0 |
| sentence-transformers/all-MiniLM-L6-v2 | 0.07 | 3.0 | 446.1 | 271.1 | 384 | 0.9 | 1.0 |
| nomic-ai/nomic-embed-text-v1.5 | 0.33 | 27.9 | 39.2 | 881.7 | 768 | 0.9 | 1.0 |

## Which model to pick

All three models recall the memories equally well (R@1 = 0.9, R@3 = 1.0), so
recall no longer differentiates them; the choice is speed and memory. With
identical recall and the same 384-dimensional vectors, the cheapest model wins.

**sentence-transformers/all-MiniLM-L6-v2.** It is the fastest (3.0 ms per
query, 446 emb/s) and the lightest (271.1 MB peak RSS) while tying the best
recall — the best fit for a small, memory-constrained CPU-only VPS.

**BAAI/bge-small-en-v1.5** is a fine close second (5.2 ms, 298.4 MB, 384 dim)
if the project wants to stay with the originally planned BGE model; it gives up
some speed and memory for no recall gain.

**nomic-ai/nomic-embed-text-v1.5** is not worth it: slowest (27.9 ms), by far
the most memory (881.7 MB), larger 768-dimensional vectors, and no recall
advantage over the other two.
