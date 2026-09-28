"""tools/build_dense_index.py: chunked writing and resume, with a fake encoder (no model, no catalog)."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_dense_index", REPO / "tools" / "build_dense_index.py")
bdi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdi)

DIM = 4


class FakeEncoder:
    """Vector of text 't<k>' = unit vector along axis k % DIM, scaled by nothing; can fail on a chunk."""

    def __init__(self, fail_on_call=None):
        self.calls, self.fail_on_call, self.truncated = 0, fail_on_call, 0

    def encode(self, texts):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated crash")
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, int(t[1:]) % DIM] = 1.0
        return out


def _expected(n):
    e = np.zeros((n, DIM), dtype=np.float32)
    e[np.arange(n), np.arange(n) % DIM] = 1.0
    return e


def _data(n):
    return [f"{i:013d}" for i in range(n)], [f"t{i}" for i in range(n)]


def _read(out, n):
    return np.fromfile(out / "vectors.f32", dtype=np.float32).reshape(n, DIM)


def test_writes_all_rows_in_order(tmp_path):
    ids, texts = _data(10)
    bdi.build(ids, texts, FakeEncoder(), tmp_path, {"k": 1}, DIM, chunk=3, log=lambda m: None)
    assert np.array_equal(_read(tmp_path, 10), _expected(10))
    assert (tmp_path / "ids.txt").read_text(encoding="utf-8").split() == ids
    prog = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert prog["done_chunks"] == prog["n_chunks"] == 4


def test_resume_after_crash_skips_finished_chunks(tmp_path):
    ids, texts = _data(10)
    with pytest.raises(RuntimeError):
        bdi.build(ids, texts, FakeEncoder(fail_on_call=3), tmp_path, {"k": 1}, DIM, chunk=3,
                  log=lambda m: None)
    assert json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))["done_chunks"] == 2
    enc = FakeEncoder()
    bdi.build(ids, texts, enc, tmp_path, {"k": 1}, DIM, chunk=3, log=lambda m: None)
    assert enc.calls == 2                                  # only chunks 3 and 4
    assert np.array_equal(_read(tmp_path, 10), _expected(10))


def test_refuses_to_resume_a_different_build(tmp_path):
    ids, texts = _data(4)
    bdi.build(ids, texts, FakeEncoder(), tmp_path, {"k": 1}, DIM, chunk=2, log=lambda m: None)
    with pytest.raises(SystemExit):
        bdi.build(ids, texts, FakeEncoder(), tmp_path, {"k": 2}, DIM, chunk=2, log=lambda m: None)
