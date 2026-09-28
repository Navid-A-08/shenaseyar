"""Text -> dense vector with the BGE-M3 ONNX model. CPU only, batched.

Uses the official export's `sentence_embedding` output (CLS pooling, per 1_Pooling/config.json),
L2-normalized. Texts are tokenized, truncated to `max_length` tokens, sorted by length and batched
so padding stays small; the output keeps the input order.

Callers normalize Persian text with src.text.normalize.normalize() first (project convention);
this module does not, so the same encoder can be checked on raw text.

Only CPUExecutionProvider is ever requested: onnxruntime also ships remote providers, and the
pipeline must make no external calls at runtime.
"""
from pathlib import Path

import numpy as np
from tokenizers import Tokenizer

PROVIDERS = ["CPUExecutionProvider"]
OUTPUT = "sentence_embedding"
GRAPH_OPT = {"disable": "ORT_DISABLE_ALL", "basic": "ORT_ENABLE_BASIC",
             "extended": "ORT_ENABLE_EXTENDED", "all": "ORT_ENABLE_ALL"}


def session_options(threads=None, graph_opt="all"):
    """onnxruntime SessionOptions: intra-op threads (None = onnxruntime's default) and graph
    optimization level (one of GRAPH_OPT; "all" is also onnxruntime's own default)."""
    import onnxruntime as ort

    if graph_opt not in GRAPH_OPT:
        raise ValueError(f"graph_opt must be one of {sorted(GRAPH_OPT)}, got {graph_opt!r}")
    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = threads
    opts.graph_optimization_level = getattr(ort.GraphOptimizationLevel, GRAPH_OPT[graph_opt])
    return opts


class OnnxEncoder:
    def __init__(self, session, tokenizer, max_length=128, batch_size=32, output=OUTPUT):
        self.session, self.tokenizer = session, tokenizer
        self.max_length, self.batch_size, self.output = max_length, batch_size, output
        self._full = Tokenizer.from_str(tokenizer.to_str())   # untruncated copy, only for counting
        self._full.no_truncation()
        self._full.no_padding()
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.no_padding()
        self.truncated = 0                       # texts cut at max_length, over this encoder's life

    @classmethod
    def from_dir(cls, model_dir, max_length=128, batch_size=32, threads=None, graph_opt="all"):
        import onnxruntime as ort

        model_dir = Path(model_dir)
        opts = session_options(threads, graph_opt)
        session = ort.InferenceSession(str(model_dir / "model.onnx"), opts, providers=PROVIDERS)
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        return cls(session, tokenizer, max_length, batch_size)

    @property
    def dim(self):
        return self.session.get_outputs()[[o.name for o in self.session.get_outputs()].index(self.output)].shape[-1]

    def encode(self, texts):
        """float32 array (len(texts), dim), rows L2-normalized, in input order."""
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        encs = self.tokenizer.encode_batch(texts)
        self.truncated += sum(1 for e in self._full.encode_batch(texts) if len(e.ids) > self.max_length)
        order = sorted(range(len(encs)), key=lambda i: len(encs[i].ids))
        out = [None] * len(encs)
        for start in range(0, len(order), self.batch_size):
            idx = order[start:start + self.batch_size]
            width = max(len(encs[i].ids) for i in idx)
            ids = np.full((len(idx), width), self._pad_id(), dtype=np.int64)
            mask = np.zeros((len(idx), width), dtype=np.int64)
            for row, i in enumerate(idx):
                n = len(encs[i].ids)
                ids[row, :n] = encs[i].ids
                mask[row, :n] = 1
            vecs = self.session.run([self.output], {"input_ids": ids, "attention_mask": mask})[0]
            for row, i in enumerate(idx):
                out[i] = vecs[row]
        mat = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.maximum(norms, 1e-12)

    def _pad_id(self):
        pid = self.tokenizer.token_to_id("<pad>")
        return 1 if pid is None else pid
