"""Build the dense (BGE-M3, fp32) index of the catalog snapshot. Setup only; long-running.

Encodes every active in-force title of the snapshot (normalized with src/text/normalize.py) with
the official fp32 ONNX model and writes, to --out (default data/index/dense-fp32/, gitignored):
  vectors.f32    float32 matrix (rows, 1024), row i = ids.txt line i, L2-normalized
  ids.txt        the 13-digit IDs, one per line, in snapshot order
  progress.json  chunks done so far (the build resumes from here after an interruption)
  manifest.json  catalog zip SHA-256, as_of, model file SHA-256, settings, timings, truncation

int8 is not used: no int8 variant met the pre-registered bar and int8 vectors depend on batch
composition (docs/silver_set.md). fp32 vectors are batch-invariant, so chunking and batch size
do not change them.

Usage: python tools/build_dense_index.py [--threads 0] [--batch 64] [--chunk 8192] [--limit N]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.run_eval import peak_memory_mb  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)                      # atomic: a crash never leaves half a file


def build(ids, texts, encoder, out_dir, key, dim, chunk=8192, log=print):
    """Encode `texts` into out_dir/vectors.f32 in chunks; resume if out_dir holds the same `key`.

    `key` identifies the build (catalog, as_of, model, rows); a different key in an existing
    progress.json is an error rather than a silent overwrite. Returns seconds spent this call.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(texts)
    prog_path, vec_path = out_dir / "progress.json", out_dir / "vectors.f32"
    done = 0
    if prog_path.exists():
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
        if prog["key"] != key:
            raise SystemExit(f"{out_dir} holds a different build ({prog['key']}); use another --out")
        done = prog["done_chunks"]
        log(f"resuming at chunk {done}")
    else:
        (out_dir / "ids.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    mode = "r+" if vec_path.exists() and done else "w+"
    mat = np.memmap(vec_path, dtype=np.float32, mode=mode, shape=(n, dim))
    n_chunks = (n + chunk - 1) // chunk
    t0 = time.perf_counter()
    rows_this_call = 0
    for c in range(done, n_chunks):
        lo, hi = c * chunk, min(n, (c + 1) * chunk)
        mat[lo:hi] = encoder.encode(texts[lo:hi])
        mat.flush()
        _write_json(prog_path, {"key": key, "done_chunks": c + 1, "n_chunks": n_chunks,
                                "truncated": getattr(encoder, "truncated", 0)})
        rows_this_call += hi - lo
        rate = rows_this_call / (time.perf_counter() - t0)
        eta_h = (n - hi) / rate / 3600
        log(f"chunk {c + 1}/{n_chunks} rows {hi}/{n} {rate:.1f}/s eta {eta_h:.2f} h")
    del mat
    return time.perf_counter() - t0


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the fp32 BGE-M3 dense index of the catalog.")
    p.add_argument("--zip", type=Path, default=None)
    p.add_argument("--as-of", default="1405-07-01")
    p.add_argument("--model", type=Path, default=REPO / "models" / "bge-m3" / "onnx")
    p.add_argument("--threads", type=int, default=0, help="0 = onnxruntime default")
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--chunk", type=int, default=8192)
    p.add_argument("--max-length", type=int, default=128)
    p.add_argument("--limit", type=int, default=None, help="first N rows only (smoke test)")
    p.add_argument("--out", type=Path, default=REPO / "data" / "index" / "dense-fp32")
    a = p.parse_args(argv)
    from src.retrieval.catalog import load_snapshot
    from src.retrieval.encoder import OnnxEncoder
    from src.text.normalize import normalize

    zip_path = a.zip or next((REPO / "data" / "catalog").glob("*.zip"))
    snap = load_snapshot(zip_path, a.as_of)
    docs = snap.docs[:a.limit] if a.limit else snap.docs
    ids, texts = [d[0] for d in docs], [normalize(d[1]) for d in docs]
    enc = OnnxEncoder.from_dir(a.model, a.max_length, a.batch, a.threads or None)
    key = {"catalog_zip_sha256": sha256(zip_path), "as_of": a.as_of, "rows": len(docs),
           "model_onnx_sha256": sha256(a.model / "model.onnx"), "max_length": a.max_length,
           "normalize": "src/text/normalize.py", "rules": snap.rules}
    print(f"{len(docs)} rows, {snap.describe()}", flush=True)
    seconds = build(ids, texts, enc, a.out, key, enc.dim, a.chunk,
                    log=lambda m: print(m, flush=True))
    manifest = {**key, "model_dir": str(a.model.relative_to(REPO)) if a.model.is_relative_to(REPO) else str(a.model),
                "model_onnx_data_sha256": sha256(a.model / "model.onnx_data"),
                "threads": a.threads, "batch": a.batch, "chunk": a.chunk, "dim": enc.dim,
                "dtype": "float32", "seconds_last_call": round(seconds, 1),
                "truncated_last_call": enc.truncated, "process_peak_mb": peak_memory_mb()}
    _write_json(a.out / "manifest.json", manifest)
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
