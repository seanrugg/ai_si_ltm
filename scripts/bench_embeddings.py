"""Embedding benchmark for P8 (CPU-only, fastembed).

Compares embedding models for semantic search over LTMS record summaries.
Measures load time, single-embed latency, batch throughput, peak memory,
dimension, and retrieval quality (recall@1, recall@3).

Each model runs in its own child process so the peak-RSS column is not
cumulative across models.

Usage:
    python -m scripts.bench_embeddings [--threads 2] [--models model1,model2,...]

Default models: BAAI/bge-small-en-v1.5, sentence-transformers/all-MiniLM-L6-v2,
nomic-ai/nomic-embed-text-v1.5. Skip any the installed fastembed doesn't offer.
"""
from __future__ import annotations

import argparse
import json
import resource
import statistics
import subprocess
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

# Model that fastembed documents say needs query/document prefixes.
NOMIC_MODEL = "nomic-ai/nomic-embed-text-v1.5"

# ~40 distinct LTMS record summaries, one fact each, in the style of
# scripts/seed_demo.py. Places (kitchen, hallway, dock), times, battery,
# people and objects vary so every question has exactly one right answer.
SUMMARIES = [
    "Z3-R0 docked at the charger at 06:00",
    "Vector picked up a red block in the kitchen",
    "Z3-R0 crossed the hallway at 0.2 m/s",
    "Vector's battery dropped to 12 percent",
    "Z3-R0 delivered a parcel to the dock",
    "Vector saw a cat on the rug",
    "Z3-R0 refilled its water tank",
    "Vector paused at the kitchen counter",
    "Z3-R0 navigated the hallway in the dark",
    "Vector returned to its charger at 22:00",
    "Z3-R0 logged three obstacle detections",
    "Vector found a lost sock under the sofa",
    "Z3-R0 opened the dock gate",
    "Vector's camera lens fogged up",
    "Z3-R0 charged to 100 percent",
    "Vector avoided a puddle in the hallway",
    "Z3-R0 met the operator at the dock",
    "Vector cleaned the kitchen floor",
    "Z3-R0 recorded a temperature of 19 C",
    "Vector played a chime at 07:00",
    "Z3-R0 took the long route to the dock",
    "Vector dropped a blue block near the stairs",
    "Z3-R0 docked late at 23:30",
    "Vector greeted the visitor in the kitchen",
    "Z3-R0 logged a near-miss in the hallway",
    "Vector's wheel slipped on a rug",
    "Z3-R0 completed a patrol of the dock",
    "Vector found its way home at midnight",
    "Z3-R0 topped off its battery at noon",
    "Vector ignored a bird outside the window",
    "Z3-R0 reported low coolant",
    "Vector sorted the recycling in the kitchen",
    "Z3-R0 waited at the dock for the operator",
    "Vector ran a self-test at 03:00",
    "Z3-R0 took the shortcut through the kitchen",
    "Vector's microphone picked up a knock",
    "Z3-R0 logged a successful docking",
    "Vector parked by the hallway radiator",
    "Z3-R0 recorded a wind gust at the dock",
    "Vector finished its shift at 18:00",
]

# Natural-language questions, each with the summary text it should find and a
# difficulty tag. Questions paraphrase the summaries (they don't repeat them
# word for word) and each targets a unique fact, so there is exactly one right
# answer per question.
#
# Easy questions share an obvious content word with their answer (a sanity
# check). Hard questions share NO content word with their answer (names like
# Z3-R0 / Vector and articles are exempt), and each has at least one distractor
# in SUMMARIES that shares words with the question but is the wrong answer.
QUESTIONS = [
    # easy
    ("when did Z3-R0 get to the charger in the morning?", "Z3-R0 docked at the charger at 06:00", "easy"),
    ("what did Vector pick up in the kitchen?", "Vector picked up a red block in the kitchen", "easy"),
    ("how fast did Z3-R0 cross the hallway?", "Z3-R0 crossed the hallway at 0.2 m/s", "easy"),
    ("how low did Vector's battery get?", "Vector's battery dropped to 12 percent", "easy"),
    ("what animal did Vector spot on the rug?", "Vector saw a cat on the rug", "easy"),
    ("what did Z3-R0 refill?", "Z3-R0 refilled its water tank", "easy"),
    ("what did Vector find under the sofa?", "Vector found a lost sock under the sofa", "easy"),
    ("how full did Z3-R0's battery get?", "Z3-R0 charged to 100 percent", "easy"),
    ("when did Vector play a chime?", "Vector played a chime at 07:00", "easy"),
    ("what did Z3-R0 report as low?", "Z3-R0 reported low coolant", "easy"),
    # hard: no content word shared with the answer; a distractor shares words
    ("how low did Vector's power get?", "Vector's battery dropped to 12 percent", "hard"),
    ("what condensed on Vector's window?", "Vector's camera lens fogged up", "hard"),
    ("what did Vector lose near the rug?", "Vector found a lost sock under the sofa", "hard"),
    ("at the counter, what did Vector lift?", "Vector picked up a red block in the kitchen", "hard"),
    ("at the gate, who did Z3-R0 meet?", "Z3-R0 met the operator at the dock", "hard"),
    ("at noon, when did Vector clock out?", "Vector finished its shift at 18:00", "hard"),
    ("in the kitchen, how full did Z3-R0's battery get?", "Z3-R0 charged to 100 percent", "hard"),
    ("in the hallway, when did Vector play a sound?", "Vector played a chime at 07:00", "hard"),
    ("what went well for Z3-R0 at the dock?", "Z3-R0 logged a successful docking", "hard"),
    ("in the hallway, how many hazards did Z3-R0 spot?", "Z3-R0 logged three obstacle detections", "hard"),
]


def _prepare_texts(model_name: str, texts: list[str], is_query: bool) -> list[str]:
    """Return texts ready for embedding.

    nomic-embed-text requires ``search_query: `` on questions and
    ``search_document: `` on documents; fastembed 0.9 does not add these
    automatically, so we add them here for that model only. Other models get
    the text unchanged.
    """
    if NOMIC_MODEL in model_name:
        prefix = "search_query: " if is_query else "search_document: "
        return [prefix + t for t in texts]
    return list(texts)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity between vector a (1 x d) and matrix b (n x d).

    Returns a flat (n,) array of scores, one per row of b.
    """
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return (b_norm @ a_norm.T).ravel()


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB.

    ru_maxrss is in bytes on macOS and in KiB on Linux.
    """
    kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return kib / (1024 * 1024)
    return kib / 1024


def benchmark_model(model_name: str, threads: int) -> dict:
    """Run the full benchmark for one model (in its own process)."""
    # --- load time ---
    t0 = time.perf_counter()
    model = TextEmbedding(model_name=model_name, threads=threads)
    load_seconds = time.perf_counter() - t0

    # dimension from a single embedding (fastembed yields 1-D arrays for a
    # single text; shape[-1] works whether it's 1-D or 2-D)
    single_vec = next(model.embed(_prepare_texts(model_name, ["dimension probe"], is_query=False)))
    dimension = int(single_vec.shape[-1])

    # --- single-embed latency (median of 20) ---
    probe = SUMMARIES[0]
    timings: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        next(model.embed(_prepare_texts(model_name, [probe], is_query=False)))
        timings.append(time.perf_counter() - t0)
    single_ms = statistics.median(timings) * 1000

    # --- batch throughput: embeddings per second in batches of 32 ---
    # Repeat 5 times and take the median; the first run includes warm-up.
    batch = SUMMARIES[:32]
    batch_times: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        list(model.embed(_prepare_texts(model_name, batch, is_query=False)))
        batch_times.append(time.perf_counter() - t0)
    batch_seconds = statistics.median(batch_times)
    embeddings_per_sec = len(batch) / batch_seconds

    # --- retrieval check ---
    summary_vecs = np.vstack(
        [v for v in model.embed(_prepare_texts(model_name, list(SUMMARIES), is_query=False))]
    )

    recall1_hits = {"easy": 0, "hard": 0}
    recall3_hits = {"easy": 0, "hard": 0}
    counts = {"easy": 0, "hard": 0}
    for question, expected_text, difficulty in QUESTIONS:
        expected_idx = SUMMARIES.index(expected_text)  # raises if text missing
        q_vec = np.vstack(
            [v for v in model.query_embed(_prepare_texts(model_name, [question], is_query=True))]
        )
        sims = cosine_similarity(q_vec, summary_vecs)
        top3 = np.argsort(sims)[::-1][:3]
        if expected_idx == top3[0]:
            recall1_hits[difficulty] += 1
        if expected_idx in top3:
            recall3_hits[difficulty] += 1
        counts[difficulty] += 1

    recall1_easy = recall1_hits["easy"] / counts["easy"]
    recall3_easy = recall3_hits["easy"] / counts["easy"]
    recall1_hard = recall1_hits["hard"] / counts["hard"]
    recall3_hard = recall3_hits["hard"] / counts["hard"]

    # --- peak memory, measured after the retrieval step (all vectors resident) ---
    peak_rss = peak_rss_mb()

    return {
        "model": model_name,
        "load_seconds": round(load_seconds, 2),
        "single_ms": round(single_ms, 1),
        "embeddings_per_sec": round(embeddings_per_sec, 1),
        "peak_rss_mb": round(peak_rss, 1),
        "dimension": dimension,
        "recall1_easy": round(recall1_easy, 2),
        "recall3_easy": round(recall3_easy, 2),
        "recall1_hard": round(recall1_hard, 2),
        "recall3_hard": round(recall3_hard, 2),
    }


def print_table(results: list[dict]) -> None:
    """Print results as a markdown table."""
    print()
    print(
        "| Model | Load (s) | Single (ms) | Emb/s | Peak RSS (MB) | Dim | "
        "R@1 easy | R@1 hard | R@3 easy | R@3 hard |"
    )
    print(
        "|-------|----------|-------------|-------|---------------|-----|----------|----------|----------|----------|"
    )
    for r in results:
        print(
            f"| {r['model']} | {r['load_seconds']} | {r['single_ms']} | "
            f"{r['embeddings_per_sec']} | {r['peak_rss_mb']} | {r['dimension']} | "
            f"{r['recall1_easy']} | {r['recall1_hard']} | {r['recall3_easy']} | {r['recall3_hard']} |"
        )
    print()


def _run_one(model_name: str, threads: int) -> dict:
    """Benchmark one model in a fresh child process and return its result dict."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bench_embeddings",
            "--one",
            model_name,
            "--threads",
            str(threads),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threads", type=int, default=2, help="threads passed to fastembed (simulate VPS CPU)")
    ap.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated list of model names to benchmark",
    )
    ap.add_argument(
        "--one",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = ap.parse_args(argv)

    # --one: benchmark a single model in this process and emit one JSON line,
    # so a parent can run each model in an isolated child process.
    if args.one is not None:
        r = benchmark_model(args.one, args.threads)
        print(json.dumps(r))
        return 0

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results: list[dict] = []

    for model_name in models:
        print(f"Benchmarking {model_name} (threads={args.threads}) ...", flush=True)
        try:
            r = _run_one(model_name, args.threads)
            results.append(r)
            print(f"  done: load={r['load_seconds']}s, single={r['single_ms']}ms, "
                  f"{r['embeddings_per_sec']} emb/s, {r['peak_rss_mb']}MB, dim={r['dimension']}, "
                  f"R@1 easy={r['recall1_easy']} hard={r['recall1_hard']}, "
                  f"R@3 easy={r['recall3_easy']} hard={r['recall3_hard']}")
        except Exception as exc:
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                detail = exc.stderr.strip()
            print(f"  SKIPPED {model_name}: {exc} {detail}", file=sys.stderr)

    print_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
