"""Embedding benchmark for P8 (CPU-only, fastembed).

Compares embedding models for semantic search over LTMS record summaries.
Measures load time, single-embed latency, batch throughput, peak memory,
dimension, and retrieval quality (recall@1, recall@3).

Usage:
    python -m scripts.bench_embeddings [--threads 2] [--models model1,model2,...]

Default models: BAAI/bge-small-en-v1.5, sentence-transformers/all-MiniLM-L6-v2,
nomic-ai/nomic-embed-text-v1.5. Skip any the installed fastembed doesn't offer.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import Callable

import numpy as np
import psutil

try:
    from fastembed import TextEmbedding
except ImportError as exc:
    print(f"fastembed is required: {exc}", file=sys.stderr)
    raise SystemExit(1)

DEFAULT_MODELS = [
    "BAAI/bge-small-en-v1.5",
    "sentence-transformers/all-MiniLM-L6-v2",
    "nomic-ai/nomic-embed-text-v1.5",
]

# ~40 realistic LTMS record summaries, in the style of scripts/seed_demo.py.
# Z3-R0 and Vector in the living room, reflex stops, policies, lessons.
SUMMARIES = [
    "No driving after 9 pm",
    "Keep 30 cm from the stairs",
    "Hello Z3",
    "Heard Vector",
    "Approach Vector at 0.1 m/s on the rug",
    "Reflex stop: obstacle detected 0.8 m ahead",
    "Reflex stop: cliff edge at stairs",
    "Vector audio channel beep boop",
    "Z3-R0 confidence 0.91 on voice match",
    "Living room rug navigation complete",
    "Hallway min distance 0.3 m maintained",
    "Quiet hours 21:00-07:00 enforced",
    "Vector says hello to Z3-R0",
    "Z3-R0 heard Vector's greeting",
    "3 reflex stops in 6 approaches to Vector",
    "Lesson: approach Vector slowly on rug",
    "Policy: no driving after 9 pm",
    "Policy: keep 30 cm from stairs",
    "Z3-R0 reflex stop near stairs",
    "Vector communication received in living room",
    "Approach Vector at reduced speed",
    "Obstacle 0.8 m triggers reflex stop",
    "Cliff edge detected at stairwell",
    "Voice match confidence above threshold",
    "Rug navigation path confirmed",
    "Hallway distance rule satisfied",
    "Quiet hours policy active",
    "Vector audio beep boop received",
    "Z3-R0 voice recognition successful",
    "Slow approach to Vector recorded",
    "Six approaches with three stops logged",
    "Living room is the current place",
    "Vector and Z3-R0 peer communication",
    "Reflex stop count updated",
    "Navigation basis: three stops in six approaches",
    "Stairs proximity rule: 30 cm minimum",
    "Night driving prohibition in effect",
    "Voice channel audio from Vector",
    "Z3-R0 proximity to Vector: 0.1 m/s",
    "Lesson stored for future approaches",
    "Policy record from operator",
    "Scoped cloud agent recall limited to lessons",
]

# 10 natural-language questions, each with the index of the summary it should find.
QUESTIONS = [
    ("what is the quiet hours policy?", 25),
    ("what happened when Z3-R0 got close to the stairs?", 18),
    ("how fast should Z3-R0 approach Vector?", 4),
    ("what is the minimum distance from the stairs?", 1),
    ("what triggers a reflex stop?", 5),
    ("what did Vector say to Z3-R0?", 13),
    ("what is the voice match confidence?", 8),
    ("where does Z3-R0 approach Vector?", 30),
    ("how many reflex stops in six approaches?", 14),
    ("what is the lesson about approaching Vector?", 3),
]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity between vector a (1 x d) and matrix b (n x d).

    Returns a flat (n,) array of scores, one per row of b.
    """
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return (b_norm @ a_norm.T).ravel()


def peak_rss_mb() -> float:
    """Current process resident set size in MB."""
    return psutil.Process().memory_info().rss / (1024 * 1024)


def benchmark_model(model_name: str, threads: int) -> dict:
    """Run the full benchmark for one model."""
    # --- load time + peak memory ---
    t0 = time.perf_counter()
    model = TextEmbedding(model_name=model_name, threads=threads)
    load_seconds = time.perf_counter() - t0
    rss_after_load = peak_rss_mb()

    # dimension from a single embedding (fastembed yields 1-D arrays for a
    # single text; shape[-1] works whether it's 1-D or 2-D)
    single_vec = next(model.embed(["dimension probe"]))
    dimension = int(single_vec.shape[-1])

    # --- single-embed latency (median of 20) ---
    probe = SUMMARIES[0]
    timings: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        next(model.embed([probe]))
        timings.append(time.perf_counter() - t0)
    single_ms = statistics.median(timings) * 1000

    # --- batch throughput: embeddings per second in batches of 32 ---
    batch = SUMMARIES[:32]
    t0 = time.perf_counter()
    list(model.embed(batch))
    batch_seconds = time.perf_counter() - t0
    embeddings_per_sec = len(batch) / batch_seconds

    # --- retrieval check ---
    # Embed all summaries once
    summary_vecs = np.vstack([v for v in model.embed(SUMMARIES)])

    recall1_hits = 0
    recall3_hits = 0
    for question, expected_idx in QUESTIONS:
        # BGE models need a query prefix; fastembed's query_embed handles it.
        q_vec = np.vstack([v for v in model.query_embed([question])])
        sims = cosine_similarity(q_vec, summary_vecs)
        top3 = np.argsort(sims)[::-1][:3]
        if expected_idx == top3[0]:
            recall1_hits += 1
        if expected_idx in top3:
            recall3_hits += 1

    recall1 = recall1_hits / len(QUESTIONS)
    recall3 = recall3_hits / len(QUESTIONS)

    return {
        "model": model_name,
        "load_seconds": round(load_seconds, 2),
        "single_ms": round(single_ms, 1),
        "embeddings_per_sec": round(embeddings_per_sec, 1),
        "peak_rss_mb": round(rss_after_load, 1),
        "dimension": dimension,
        "recall1": round(recall1, 2),
        "recall3": round(recall3, 2),
    }


def print_table(results: list[dict]) -> None:
    """Print results as a markdown table."""
    print()
    print("| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | R@1 | R@3 |")
    print("|-------|----------|-------------|-------|---------------|-----|-----|-----|")
    for r in results:
        print(
            f"| {r['model']} | {r['load_seconds']} | {r['single_ms']} | "
            f"{r['embeddings_per_sec']} | {r['peak_rss_mb']} | {r['dimension']} | "
            f"{r['recall1']} | {r['recall3']} |"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threads", type=int, default=2, help="threads passed to fastembed (simulate VPS CPU)")
    ap.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated list of model names to benchmark",
    )
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results: list[dict] = []

    for model_name in models:
        print(f"Benchmarking {model_name} (threads={args.threads}) ...", flush=True)
        try:
            r = benchmark_model(model_name, args.threads)
            results.append(r)
            print(f"  done: load={r['load_seconds']}s, single={r['single_ms']}ms, "
                  f"{r['embeddings_per_sec']} emb/s, {r['peak_rss_mb']}MB, dim={r['dimension']}, "
                  f"R@1={r['recall1']}, R@3={r['recall3']}")
        except Exception as exc:
            print(f"  SKIPPED {model_name}: {exc}", file=sys.stderr)

    print_table(results)

    # Also write the table to stdout in a copy-paste-friendly block
    print("### Markdown table for docs/embedding-benchmark.md")
    print("| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | R@1 | R@3 |")
    print("|-------|----------|-------------|-------|---------------|-----|-----|-----|")
    for r in results:
        print(
            f"| {r['model']} | {r['load_seconds']} | {r['single_ms']} | "
            f"{r['embeddings_per_sec']} | {r['peak_rss_mb']} | {r['dimension']} | "
            f"{r['recall1']} | {r['recall3']} |"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
