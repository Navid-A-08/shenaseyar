"""Quantize the official BGE-M3 ONNX export to int8 (dynamic, weights only). Setup only.

Input:  models/bge-m3/onnx/model.onnx (+ model.onnx_data), from tools/fetch_bge_m3.py (verified).
Output: models/bge-m3/onnx-int8/model.onnx (gitignored) and a manifest with the input and output
        SHA-256, the onnxruntime version and the settings, so the int8 model is traceable to the
        official weights. This is "our own conversion of official weights" (CLAUDE.md).
Needs the setup-only dependency in requirements-setup.txt (onnx). Nothing in src/ or eval/
may import this file.

Usage: python tools/quantize_bge_m3.py [--src models/bge-m3/onnx] [--dst models/bge-m3/onnx-int8]
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                   "sentencepiece.bpe.model", "config.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description="Quantize official BGE-M3 ONNX to int8 (dynamic).")
    p.add_argument("--src", type=Path, default=REPO / "models" / "bge-m3" / "onnx")
    p.add_argument("--dst", type=Path, default=REPO / "models" / "bge-m3" / "onnx-int8")
    a = p.parse_args(argv)
    import onnxruntime
    from onnxruntime.quantization import QuantType, quantize_dynamic

    a.dst.mkdir(parents=True, exist_ok=True)
    src_model, dst_model = a.src / "model.onnx", a.dst / "model.onnx"
    t0 = time.perf_counter()
    quantize_dynamic(str(src_model), str(dst_model), weight_type=QuantType.QInt8)
    seconds = time.perf_counter() - t0
    for name in TOKENIZER_FILES:                     # keep the tokenizer next to the model
        shutil.copy2(a.src / name, a.dst / name)
    manifest = {
        "source": "BAAI/bge-m3 official ONNX export, revision 5617a9f61b028005a4858fdac845db406aefb181",
        "method": "onnxruntime.quantization.quantize_dynamic, weight_type=QInt8 (weights only)",
        "onnxruntime": onnxruntime.__version__,
        "input_sha256": {"model.onnx": sha256(src_model), "model.onnx_data": sha256(a.src / "model.onnx_data")},
        "output_sha256": {f.name: sha256(f) for f in sorted(a.dst.iterdir()) if f.is_file()
                          and f.name != "manifest.json"},
        "output_bytes": sum(f.stat().st_size for f in a.dst.iterdir() if f.is_file()),
        "seconds": round(seconds, 1),
    }
    (a.dst / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
