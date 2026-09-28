"""Download the OFFICIAL BGE-M3 ONNX export, pinned and verified. Setup only, not the pipeline.

Source: huggingface.co/BAAI/bge-m3 (MIT), revision pinned below. Only the files listed in FILES
are fetched: the official ONNX export and its tokenizer. The repo's pickle-based files
(pytorch_model.bin, colbert_linear.pt, sparse_linear.pt) are never downloaded or loaded
(CLAUDE.md: no pickle-based model files; ONNX only from our conversion or the official repo).

Every file is checked against its published hash: SHA-256 for LFS files, the git blob SHA-1 for
small files. A file that fails is deleted. Interrupted downloads resume from a .part file.
Weights land in models/bge-m3/ (gitignored). Nothing in src/ or eval/ may import this file.

Usage: python tools/fetch_bge_m3.py [--dest models/bge-m3]
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# path in repo -> (size in bytes, ("sha256" | "git-blob-sha1", hex digest))
FILES = {
    "onnx/model.onnx": (724923, ("sha256", "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b")),
    "onnx/model.onnx_data": (2266820608, ("sha256", "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4")),
    "onnx/Constant_7_attr__value": (65552, ("git-blob-sha1", "3cdc05e5f550d8bd136f28efcea0ad5e6b4169c8")),
    "onnx/config.json": (698, ("git-blob-sha1", "aadef53b97586be7cafa2c06837a0bd53f1f1ded")),
    "onnx/tokenizer.json": (17082821, ("sha256", "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790")),
    "onnx/tokenizer_config.json": (1173, ("git-blob-sha1", "328a00a9a560aadcf2a3064f917517359eb3cc26")),
    "onnx/special_tokens_map.json": (964, ("git-blob-sha1", "b1879d702821e753ffe4245048eee415d54a9385")),
    "onnx/sentencepiece.bpe.model": (5069051, ("sha256", "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865")),
    "1_Pooling/config.json": (191, ("git-blob-sha1", "9bd85925f325e25246d94c4918dc02ab98f2a1b7")),
}
FORBIDDEN_SUFFIXES = (".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt")
CHUNK = 1 << 20


def file_digest(path, kind):
    """sha256 of the bytes, or git's blob hash: sha1(b"blob <size>\\0" + bytes)."""
    h = hashlib.sha256() if kind == "sha256" else hashlib.sha1()
    if kind == "git-blob-sha1":
        h.update(f"blob {path.stat().st_size}\0".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path, size, check):
    kind, expected = check
    if not path.exists() or path.stat().st_size != size:
        return False
    return file_digest(path, kind) == expected


def url_for(name):
    return f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"


def download(name, dest, size, check):
    target = dest / name
    if verify(target, size, check):
        print(f"ok (already verified)  {name}")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have < size:
        req = urllib.request.Request(url_for(name), headers={"Range": f"bytes={have}-"} if have else {})
        with urllib.request.urlopen(req, timeout=60) as resp:
            if have and resp.status != 206:           # server ignored the range: start over
                have = 0
            with open(part, "ab" if have else "wb") as out:
                done = have
                for chunk in iter(lambda: resp.read(CHUNK), b""):
                    out.write(chunk)
                    done += len(chunk)
                    if size > 50 * CHUNK and done % (200 * CHUNK) < CHUNK:
                        print(f"  {name}: {done / 2**20:,.0f} / {size / 2**20:,.0f} MB", flush=True)
    part.replace(target)
    if verify(target, size, check):
        print(f"ok (verified {check[0]})  {name}")
        return True
    target.unlink()
    print(f"FAILED verification, deleted  {name}", file=sys.stderr)
    return False


def main(argv=None):
    p = argparse.ArgumentParser(description="Fetch the official BGE-M3 ONNX export (pinned).")
    p.add_argument("--dest", type=Path, default=Path(__file__).resolve().parents[1] / "models" / "bge-m3")
    a = p.parse_args(argv)
    assert not any(n.endswith(FORBIDDEN_SUFFIXES) for n in FILES), "pickle-based file in FILES"
    print(f"{REPO} @ {REVISION} -> {a.dest}")
    ok = all([download(n, a.dest, size, check) for n, (size, check) in FILES.items()])
    print("ALL VERIFIED" if ok else "SOME FILES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
