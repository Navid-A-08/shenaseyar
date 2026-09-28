"""int8 variants vs full precision (pre-registered bar), and speed settings. Setup only.

Agreement (docs/silver_set.md, "Acceptance bar: int8 vs full precision"): the same seeded 2,000
in-force titles (normalized) are encoded by fp32 and by each int8 variant.
  - mean cosine of paired vectors
  - top-20 overlap: each title queries the other 1,999 (itself excluded); overlap of its top 20
    under int8 vs fp32, averaged over the 2,000 titles
  - acceptable = mean cosine >= 0.99 AND top-20 overlap >= 0.95
No silver query is read.

Speed: throughput on the same 2,000 titles for each (threads, batch) setting at graph
optimization ALL, on one model (an int8 variant or fp32). Each setting's vectors are compared with a reference
setting's vectors, to check that speed settings leave the vectors unchanged.

Writes eval/results/int8_agreement.json (numbers only, no text). fp32 vectors are cached in the
scratch directory given by --cache (never in the repo).

Usage: python tools/bench_int8_agreement.py [--part agreement|speed|both] [--cache DIR]
"""
import argparse
import json
import random
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
MODELS = REPO / "models" / "bge-m3"
VARIANTS = {"per_tensor": MODELS / "onnx-int8",
            "per_channel": MODELS / "onnx-int8-per_channel",
            "per_channel_preproc": MODELS / "onnx-int8-per_channel_preproc",
            "per_channel_matmul": MODELS / "onnx-int8-per_channel_matmul"}
BAR = {"mean_cosine": 0.99, "top20_overlap": 0.95}
K = 20


def topk_excluding_self(vecs, k=K):
    """Row i: indices of the k most similar other rows (cosine; rows are L2-normalized)."""
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -np.inf)
    part = np.argpartition(-sims, k, axis=1)[:, :k]
    return part


def agreement(test, ref, k=K):
    """Pre-registered metrics of `test` vectors against `ref` vectors (same rows, same order)."""
    cos = np.sum(test * ref, axis=1)
    t, r = topk_excluding_self(test, k), topk_excluding_self(ref, k)
    overlap = np.array([len(set(a) & set(b)) / k for a, b in zip(t, r)])
    return {"mean_cosine": round(float(cos.mean()), 4), "min_cosine": round(float(cos.min()), 4),
            "top20_overlap": round(float(overlap.mean()), 4),
            "top20_overlap_min": round(float(overlap.min()), 4),
            "disagreement_rate": round(float(1 - overlap.mean()), 4)}


def verdict(m):
    return m["mean_cosine"] >= BAR["mean_cosine"] and m["top20_overlap"] >= BAR["top20_overlap"]


def sample_titles(zip_path, as_of, n):
    snap = load_snapshot(zip_path, as_of)
    rng = random.Random(20260928)             # same seed and draw as tools/bench_dense.py
    return [normalize(t) for _, t in rng.sample(snap.docs, n)], len(snap.docs)


def timed_encode(enc, texts):
    enc.encode(texts[:64])                    # warm-up
    t = time.perf_counter()
    v = enc.encode(texts)
    return v, time.perf_counter() - t


def main(argv=None):
    p = argparse.ArgumentParser(description="int8 variants vs fp32 (pre-registered bar) and speed.")
    p.add_argument("--zip", type=Path, default=None)
    p.add_argument("--as-of", default="1405-07-01")
    p.add_argument("--sample", type=int, default=2000)
    p.add_argument("--max-length", type=int, default=128)
    p.add_argument("--part", choices=["agreement", "speed", "both"], default="both")
    p.add_argument("--variants", default=",".join(VARIANTS))
    p.add_argument("--agree-threads", type=int, default=None)
    p.add_argument("--agree-batch", type=int, default=64)
    p.add_argument("--speed-model", default="per_tensor", choices=["fp32", *VARIANTS])
    p.add_argument("--threads", default="8,16,28")
    p.add_argument("--batches", default="64,128")
    p.add_argument("--cache", type=Path, required=True, help="scratch dir for fp32 vectors")
    p.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "int8_agreement.json")
    a = p.parse_args(argv)
    zip_path = a.zip or next((REPO / "data" / "catalog").glob("*.zip"))
    titles, n_docs = sample_titles(zip_path, a.as_of, a.sample)
    res = json.loads(a.out.read_text(encoding="utf-8")) if a.out.exists() else {}
    res.update({"catalog_rows": n_docs, "sample": a.sample, "max_length": a.max_length,
                "as_of": a.as_of, "bar": BAR, "graph_opt": "all"})

    if a.part in ("agreement", "both"):
        a.cache.mkdir(parents=True, exist_ok=True)
        fp32_path = a.cache / f"fp32_{a.sample}_{a.max_length}.npy"
        if fp32_path.exists():
            ref = np.load(fp32_path)
        else:
            fp32 = OnnxEncoder.from_dir(MODELS / "onnx", a.max_length, a.agree_batch, a.agree_threads)
            ref, sec = timed_encode(fp32, titles)
            np.save(fp32_path, ref)
            res["fp32_titles_per_s"] = round(len(titles) / sec, 1)
        res.setdefault("variants", {})
        for name in a.variants.split(","):
            enc = OnnxEncoder.from_dir(VARIANTS[name], a.max_length, a.agree_batch, a.agree_threads)
            vec, sec = timed_encode(enc, titles)
            m = agreement(vec, ref)
            m.update({"acceptable": verdict(m), "titles_per_s": round(len(titles) / sec, 1),
                      "model_bytes": (VARIANTS[name] / "model.onnx").stat().st_size})
            res["variants"][name] = m
            print(name, json.dumps(m), flush=True)
        ok = [n for n, m in res["variants"].items() if m["acceptable"]]
        res["acceptable_variants"] = ok

    if a.part in ("speed", "both"):
        speed, ref_vec = {}, None
        for th in [int(x) for x in a.threads.split(",")]:
            model_dir = MODELS / "onnx" if a.speed_model == "fp32" else VARIANTS[a.speed_model]
            enc = OnnxEncoder.from_dir(model_dir, a.max_length, 64, th)
            for bs in [int(x) for x in a.batches.split(",")]:
                enc.batch_size = bs
                vec, sec = timed_encode(enc, titles)
                if ref_vec is None:
                    ref_vec = vec
                tps = len(titles) / sec
                speed[f"threads{th}_batch{bs}"] = {
                    "threads": th, "batch": bs, "seconds": round(sec, 1), "titles_per_s": round(tps, 1),
                    "projected_full_index_hours": round(n_docs / tps / 3600, 2),
                    "max_abs_diff_vs_first_setting": float(np.abs(vec - ref_vec).max())}
                print(f"threads={th} batch={bs}", json.dumps(speed[f"threads{th}_batch{bs}"]), flush=True)
        res["speed"] = {"model": a.speed_model, "settings": speed,
                        "best": max(speed, key=lambda s: speed[s]["titles_per_s"])}
    res["process_peak_mb"] = peak_memory_mb()
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    main()
