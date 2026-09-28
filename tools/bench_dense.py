"""Benchmark the BGE-M3 int8 encoder before committing to a full-catalog index. Setup only.

Measures, on a seeded sample of in-force titles (normalized with src/text/normalize.py):
  - throughput (titles/s) at several batch sizes, and the projected full-index time
  - token lengths and how many titles are cut at max_length
  - int8 vs fp32: cosine of paired vectors, and top-20 neighbour overlap for sample queries
  - that `sentence_embedding` equals the L2-normalized CLS token (the model's pooling config)
  - single-query latency (encode, batch 1) and exact-search latency over a full-size matrix
  - process peak memory
Writes eval/results/dense_bench.json (numbers only, no text). Nothing in src/ or eval/ imports it.

Usage: python tools/bench_dense.py [--sample 2000] [--queries eval/silver_v2b.csv]
"""
import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.run_eval import peak_memory_mb  # noqa: E402
from src.retrieval.catalog import load_snapshot  # noqa: E402
from src.retrieval.encoder import OnnxEncoder  # noqa: E402
from src.text.normalize import normalize  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main(argv=None):
    p = argparse.ArgumentParser(description="Benchmark the dense encoder (int8) on CPU.")
    p.add_argument("--zip", type=Path, default=None)
    p.add_argument("--as-of", default="1405-07-01")
    p.add_argument("--sample", type=int, default=2000)
    p.add_argument("--fp32-sample", type=int, default=300)
    p.add_argument("--batch-sizes", default="16,32,64")
    p.add_argument("--max-length", type=int, default=128)
    p.add_argument("--queries", type=Path, default=REPO / "eval" / "silver_v2b.csv")
    p.add_argument("--n-queries", type=int, default=100)
    p.add_argument("--int8", type=Path, default=REPO / "models" / "bge-m3" / "onnx-int8")
    p.add_argument("--fp32", type=Path, default=REPO / "models" / "bge-m3" / "onnx")
    p.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "dense_bench.json")
    a = p.parse_args(argv)
    rng = random.Random(20260928)
    zip_path = a.zip or next((REPO / "data" / "catalog").glob("*.zip"))

    snap = load_snapshot(zip_path, a.as_of)
    n_docs = len(snap.docs)
    sample = [normalize(t) for _, t in rng.sample(snap.docs, a.sample)]
    res = {"catalog_rows": n_docs, "sample": a.sample, "max_length": a.max_length,
           "cpu_count": os.cpu_count(), "as_of": a.as_of}

    enc = OnnxEncoder.from_dir(a.int8, max_length=a.max_length)
    lengths = [len(e.ids) for e in enc._full.encode_batch(sample)]
    res["tokens"] = {"mean": round(statistics.mean(lengths), 1), "median": statistics.median(lengths),
                     "p95": _pct(lengths, 0.95), "max": max(lengths),
                     "truncated_share": round(sum(x > a.max_length for x in lengths) / len(lengths), 4)}
    enc.encode(sample[:64])                                   # warm-up
    res["throughput"] = {}
    for bs in [int(x) for x in a.batch_sizes.split(",")]:
        enc.batch_size = bs
        t = time.perf_counter()
        vec_int8 = enc.encode(sample)
        sec = time.perf_counter() - t
        res["throughput"][bs] = {"seconds": round(sec, 1), "titles_per_s": round(a.sample / sec, 1)}
    best = max(res["throughput"].items(), key=lambda kv: kv[1]["titles_per_s"])
    res["best_batch_size"] = best[0]
    res["projected_full_index_hours"] = round(n_docs / best[1]["titles_per_s"] / 3600, 2)

    # int8 vs fp32, and sentence_embedding vs normalized CLS (fp32)
    fp32 = OnnxEncoder.from_dir(a.fp32, max_length=a.max_length, batch_size=best[0])
    sub = sample[:a.fp32_sample]
    t = time.perf_counter()
    vec_fp32 = fp32.encode(sub)
    res["fp32_titles_per_s"] = round(len(sub) / (time.perf_counter() - t), 1)
    cos = np.sum(vec_fp32 * vec_int8[:len(sub)], axis=1)
    res["int8_vs_fp32_cosine"] = {"mean": round(float(cos.mean()), 4), "min": round(float(cos.min()), 4)}
    few = fp32.tokenizer.encode_batch(sub[:8])
    width = max(len(e.ids) for e in few)
    ids = np.full((len(few), width), 1, dtype=np.int64)
    mask = np.zeros_like(ids)
    for i, e in enumerate(few):
        ids[i, :len(e.ids)], mask[i, :len(e.ids)] = e.ids, 1
    tok, sent = fp32.session.run(["token_embeddings", "sentence_embedding"],
                                 {"input_ids": ids, "attention_mask": mask})
    cls = tok[:, 0] / np.linalg.norm(tok[:, 0], axis=1, keepdims=True)
    res["sentence_embedding_vs_normalized_cls_max_abs_diff"] = float(np.abs(cls - sent).max())
    res["sentence_embedding_norms"] = [round(float(x), 4) for x in np.linalg.norm(sent, axis=1)]

    # queries: text column of the silver file (gitignored; only numbers are written out)
    with open(a.queries, encoding="utf-8", newline="") as f:
        csv.field_size_limit(2**31 - 1)
        queries = [r["query_text"] for r in csv.DictReader(f)]
    queries = rng.sample(queries, min(a.n_queries, len(queries)))
    q8, q32 = enc.encode(queries[:50]), fp32.encode(queries[:50])
    d8, d32 = vec_int8[:len(sub)], vec_fp32
    top8 = np.argsort(-(q8 @ d8.T), axis=1)[:, :20]
    top32 = np.argsort(-(q32 @ d32.T), axis=1)[:, :20]
    res["int8_vs_fp32_top20_overlap"] = round(float(np.mean(
        [len(set(x) & set(y)) / 20 for x, y in zip(top8, top32)])), 4)

    enc.batch_size = 1
    lat = []
    for q in queries:
        t = time.perf_counter()
        enc.encode([q])
        lat.append((time.perf_counter() - t) * 1000)
    res["query_encode_ms"] = {"median": round(statistics.median(lat), 1), "p95": round(_pct(lat, 0.95), 1)}

    mat = np.random.default_rng(0).standard_normal((n_docs, 1024), dtype=np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    res["index_matrix_gb_float32"] = round(mat.nbytes / 2**30, 2)
    qv = enc.encode(queries[:50])
    s_lat = []
    for v in qv:
        t = time.perf_counter()
        sc = mat @ v
        top = np.argpartition(-sc, 50)[:50]
        top[np.argsort(-sc[top])]
        s_lat.append((time.perf_counter() - t) * 1000)
    res["exact_search_ms"] = {"median": round(statistics.median(s_lat), 1), "p95": round(_pct(s_lat, 0.95), 1)}
    res["process_peak_mb"] = peak_memory_mb()

    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
