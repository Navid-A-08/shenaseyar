"""Quantize the official BGE-M3 ONNX export to int8 (dynamic, weights only). Setup only.

Input:  models/bge-m3/onnx/model.onnx (+ model.onnx_data), from tools/fetch_bge_m3.py (verified).
Output: models/bge-m3/onnx-int8/model.onnx (gitignored) and a manifest with the input and output
        SHA-256, the onnxruntime version and the settings, so the int8 model is traceable to the
        official weights. This is "our own conversion of official weights" (CLAUDE.md).
Needs the setup-only dependency in requirements-setup.txt (onnx). Nothing in src/ or eval/
may import this file.

Variants (--variant), all dynamic, weights int8 (QInt8):
  per_tensor           one scale per weight tensor; every op type quantize_dynamic supports
                       (here: weight MatMuls and the 3 embedding Gathers). The original model.
  per_channel          one scale per output channel, same op types.
  per_channel_preproc  per_channel after onnxruntime.quantization.shape_inference.quant_pre_process
                       (ONNX shape inference + graph optimization, e.g. fused LayerNorm; the
                       symbolic shape pass is skipped because it needs sympy).
  per_channel_matmul   per_channel, MatMul only: embedding tables stay float32.

Usage: python tools/quantize_bge_m3.py [--variant per_tensor] [--src models/bge-m3/onnx] [--dst DIR]
       Default --dst: models/bge-m3/onnx-int8 for per_tensor, models/bge-m3/onnx-int8-<variant> otherwise.
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VARIANTS = {
    "per_tensor": {"per_channel": False, "preprocess": False, "op_types": None},
    "per_channel": {"per_channel": True, "preprocess": False, "op_types": None},
    "per_channel_preproc": {"per_channel": True, "preprocess": True, "op_types": None,
                            "skip_symbolic_shape": True},
    "per_channel_matmul": {"per_channel": True, "preprocess": False, "op_types": ["MatMul"]},
}
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                   "sentencepiece.bpe.model", "config.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_dst(variant):
    base = REPO / "models" / "bge-m3"
    return base / "onnx-int8" if variant == "per_tensor" else base / f"onnx-int8-{variant}"


def main(argv=None):
    p = argparse.ArgumentParser(description="Quantize official BGE-M3 ONNX to int8 (dynamic).")
    p.add_argument("--src", type=Path, default=REPO / "models" / "bge-m3" / "onnx")
    p.add_argument("--dst", type=Path, default=None)
    p.add_argument("--variant", choices=sorted(VARIANTS), default="per_tensor")
    a = p.parse_args(argv)
    import onnxruntime
    from onnxruntime.quantization import QuantType, quantize_dynamic

    v = VARIANTS[a.variant]
    a.dst = a.dst or default_dst(a.variant)
    a.dst.mkdir(parents=True, exist_ok=True)
    src_model, dst_model = a.src / "model.onnx", a.dst / "model.onnx"
    t0 = time.perf_counter()
    q_input = src_model
    if v["preprocess"]:
        from onnxruntime.quantization.shape_inference import quant_pre_process
        q_input = a.dst / "preprocessed.onnx"        # > 2 GB, so weights go to an external file
        # skip_symbolic_shape: the symbolic pass needs sympy, which is not a project dependency.
        # ONNX shape inference and onnxruntime graph optimization still run.
        quant_pre_process(str(src_model), str(q_input), skip_symbolic_shape=True, save_as_external_data=True,
                          all_tensors_to_one_file=True, external_data_location="preprocessed.onnx_data")
    quantize_dynamic(str(q_input), str(dst_model), weight_type=QuantType.QInt8,
                     per_channel=v["per_channel"], op_types_to_quantize=v["op_types"])
    if v["preprocess"]:                               # intermediate fp32 copy, not needed afterwards
        for f in (q_input, a.dst / "preprocessed.onnx_data"):
            f.unlink(missing_ok=True)
    seconds = time.perf_counter() - t0
    for name in TOKENIZER_FILES:                     # keep the tokenizer next to the model
        shutil.copy2(a.src / name, a.dst / name)
    manifest = {
        "source": "BAAI/bge-m3 official ONNX export, revision 5617a9f61b028005a4858fdac845db406aefb181",
        "method": "onnxruntime.quantization.quantize_dynamic, weight_type=QInt8 (weights only)",
        "variant": a.variant,
        "settings": v,
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
