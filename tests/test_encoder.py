"""OnnxEncoder batching logic with a fake session and a tiny in-memory tokenizer (no real model)."""
import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from src.retrieval.encoder import PROVIDERS, OnnxEncoder, session_options


def _tokenizer():
    vocab = {"<pad>": 1, "<unk>": 3, **{w: i + 10 for i, w in enumerate("a b c d e f g h".split())}}
    tok = Tokenizer(WordLevel(vocab, unk_token="<unk>"))
    tok.pre_tokenizer = Whitespace()
    return tok


class _Out:
    def __init__(self, name, shape):
        self.name, self.shape = name, shape


class FakeSession:
    """Vector = [sum of real token ids, number of real tokens, 1]; records batch shapes."""

    def __init__(self):
        self.batches = []

    def get_outputs(self):
        return [_Out("token_embeddings", ["b", "s", 3]), _Out("sentence_embedding", ["b", 3])]

    def run(self, names, feeds):
        ids, mask = feeds["input_ids"], feeds["attention_mask"]
        assert names == ["sentence_embedding"] and ids.dtype == np.int64 and ids.shape == mask.shape
        self.batches.append(ids.shape)
        s = (ids * mask).sum(axis=1).astype(np.float32)
        return [np.stack([s, mask.sum(axis=1).astype(np.float32), np.ones_like(s)], axis=1)]


def test_order_preserved_and_rows_normalized():
    enc = OnnxEncoder(FakeSession(), _tokenizer(), max_length=8, batch_size=2)
    texts = ["a b c", "a", "d e f g h", "b"]
    out = enc.encode(texts)
    assert out.shape == (4, 3) and out.dtype == np.float32
    assert np.allclose(np.linalg.norm(out, axis=1), 1)
    raw = np.array([[10 + 11 + 12, 3, 1], [10, 1, 1], [13 + 14 + 15 + 16 + 17, 5, 1], [11, 1, 1]], float)
    assert np.allclose(out, raw / np.linalg.norm(raw, axis=1, keepdims=True))


def test_length_sorted_batches_keep_padding_small():
    sess = FakeSession()
    enc = OnnxEncoder(sess, _tokenizer(), max_length=8, batch_size=2)
    enc.encode(["a b c d e", "a", "a b c d", "b"])
    assert sess.batches == [(2, 1), (2, 5)]        # short texts together, long texts together


def test_truncation_counted():
    enc = OnnxEncoder(FakeSession(), _tokenizer(), max_length=3, batch_size=4)
    enc.encode(["a b c d e", "a"])
    assert enc.truncated == 1


def test_empty_input_and_cpu_only():
    enc = OnnxEncoder(FakeSession(), _tokenizer())
    assert enc.encode([]).shape == (0, 3)
    assert PROVIDERS == ["CPUExecutionProvider"]


def test_session_options_threads_and_graph_opt():
    ort = pytest.importorskip("onnxruntime")
    o = session_options(threads=8, graph_opt="all")
    assert o.intra_op_num_threads == 8
    assert o.graph_optimization_level == ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    assert session_options(graph_opt="basic").graph_optimization_level == ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    assert session_options().intra_op_num_threads == 0          # onnxruntime picks


def test_session_options_rejects_unknown_level():
    pytest.importorskip("onnxruntime")
    with pytest.raises(ValueError):
        session_options(graph_opt="max")
