"""tools/fetch_bge_m3.py: verification logic only (no network in tests)."""
import hashlib
import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fetch_bge_m3", REPO / "tools" / "fetch_bge_m3.py")
fb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fb)


def test_git_blob_hash_matches_git(tmp_path):
    p = tmp_path / "f.json"
    p.write_bytes(b'{"a": 1}\n')
    git = subprocess.run(["git", "hash-object", str(p)], capture_output=True, text=True, check=True)
    assert fb.file_digest(p, "git-blob-sha1") == git.stdout.strip()


def test_sha256_and_verify(tmp_path):
    p = tmp_path / "w"
    p.write_bytes(b"x" * 1000)
    digest = hashlib.sha256(b"x" * 1000).hexdigest()
    assert fb.verify(p, 1000, ("sha256", digest))
    assert not fb.verify(p, 999, ("sha256", digest))          # wrong size
    assert not fb.verify(p, 1000, ("sha256", "0" * 64))        # wrong hash
    assert not fb.verify(tmp_path / "missing", 1000, ("sha256", digest))


def test_pinned_revision_and_no_pickle_files():
    assert fb.REVISION == "5617a9f61b028005a4858fdac845db406aefb181"
    assert not any(n.endswith(fb.FORBIDDEN_SUFFIXES) for n in fb.FILES)
    assert "pytorch_model.bin" not in fb.FILES
    assert all(fb.url_for(n).startswith(f"https://huggingface.co/BAAI/bge-m3/resolve/{fb.REVISION}/")
               for n in fb.FILES)
    assert sum(size for size, _ in fb.FILES.values()) == 2289765981
