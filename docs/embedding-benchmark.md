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
- Questions: 20 natural-language questions, 10 easy and 10 hard, each with
  exactly one right answer; scored with recall@1 and recall@3 using cosine
  similarity, reported separately for the easy and hard sets

## Notes on method

- Each model runs in its **own child process** (`--one`), so peak RSS is
  per-model and not cumulative. Peak memory is measured after the retrieval
  step (all summary vectors resident), using `resource.getrusage` (bytes on
  macOS, KiB on Linux).
- Batch throughput is the median of 5 repeats (the first run is warm-up).
- Easy questions share an obvious content word with their answer; hard questions
  share **no** content word with their answer and each has a distractor summary
  that shares words with the question but is the wrong answer.
- `nomic-ai/nomic-embed-text-v1.5` requires `search_query: ` on questions and
  `search_document: ` on documents; fastembed 0.9 does not add these prefixes
  automatically, so they are added for that model only (its own model
  description marks the prefixes as "necessary"). BGE and MiniLM are embedded
  raw.

## Results

| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | R@1 easy | R@1 hard | R@3 easy | R@3 hard |
|-------|----------|-------------|-------|---------------|-----|----------|----------|----------|----------|
| BAAI/bge-small-en-v1.5 | 0.09 | 5.2 | 218.2 | 297.8 | 384 | 0.9 | 0.4 | 1.0 | 0.7 |
| sentence-transformers/all-MiniLM-L6-v2 | 0.07 | 2.5 | 442.4 | 270.8 | 384 | 0.9 | 0.2 | 1.0 | 0.6 |
| nomic-ai/nomic-embed-text-v1.5 | 0.32 | 28.2 | 38.8 | 883.0 | 768 | 0.9 | 0.3 | 1.0 | 0.7 |

## Which model to pick

**BAAI/bge-small-en-v1.5.** On the easy set all three models tie (R@1 = 0.9,
R@3 = 1.0), but that set is a ceiling and doesn't discriminate. On the hard
set, bge-small leads: R@1 = 0.4 and R@3 = 0.7, ahead of MiniLM (0.2 / 0.6) and
nomic (0.3 / 0.7). This matches public retrieval benchmarks showing bge-small
ahead of MiniLM on semantic-search tasks. The speed and memory gap versus
MiniLM is small (5 ms vs 2.5 ms, 298 MB vs 271 MB) and does not compensate for
the lower hard-set recall, so bge-small is the best choice for P8's semantic
search.

**sentence-transformers/all-MiniLM-L6-v2** is the runner-up: fastest (2.5 ms,
442 emb/s) and lightest (271 MB), but clearly behind bge-small on the hard
recall set. Only pick it if latency is the overriding constraint.

**nomic-ai/nomic-embed-text-v1.5** is not worth it: slowest (28 ms), by far
the most memory (883 MB), larger 768-dimensional vectors, and no recall
advantage over bge-small on either set.
