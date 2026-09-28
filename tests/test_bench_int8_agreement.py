"""The pre-registered int8 agreement metrics (docs/silver_set.md) on small synthetic vectors."""
import importlib.util
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bench_int8_agreement", REPO / "tools" / "bench_int8_agreement.py")
bia = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bia)
BAR, agreement, topk_excluding_self, verdict = bia.BAR, bia.agreement, bia.topk_excluding_self, bia.verdict


def _unit(x):
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_topk_excludes_self():
    v = _unit(np.random.default_rng(0).standard_normal((30, 8)))
    top = topk_excluding_self(v, k=5)
    assert top.shape == (30, 5)
    assert all(i not in row for i, row in enumerate(top))


def test_identical_vectors_agree_fully():
    v = _unit(np.random.default_rng(1).standard_normal((50, 16)))
    m = agreement(v, v, k=20)
    assert m["mean_cosine"] == 1.0 and m["top20_overlap"] == 1.0 and m["disagreement_rate"] == 0.0
    assert verdict(m)


def test_unrelated_vectors_fail_the_bar():
    rng = np.random.default_rng(2)
    a, b = _unit(rng.standard_normal((60, 16))), _unit(rng.standard_normal((60, 16)))
    m = agreement(a, b, k=20)
    assert m["mean_cosine"] < 0.5 and not verdict(m)


def test_bar_needs_both_conditions():
    assert BAR == {"mean_cosine": 0.99, "top20_overlap": 0.95}
    assert not verdict({"mean_cosine": 0.995, "top20_overlap": 0.94})
    assert not verdict({"mean_cosine": 0.989, "top20_overlap": 0.99})
    assert verdict({"mean_cosine": 0.99, "top20_overlap": 0.95})


def test_overlap_is_partial_when_one_neighbour_changes():
    # Points on the unit circle; k=2. Moving point 4 next to point 5 changes the neighbour sets of
    # row 3 ({2,4} -> {2,1}) and row 4 ({3,2} -> {5,3}); each keeps 1 of 2. Others are unchanged.
    ang = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 3.0])
    ref = np.stack([np.cos(ang), np.sin(ang)], axis=1)
    test = ref.copy()
    test[4] = [np.cos(-3.0), np.sin(-3.0)]
    m = agreement(test, ref, k=2)
    assert m["top20_overlap"] == round(5 / 6, 4) and m["top20_overlap_min"] == 0.5
