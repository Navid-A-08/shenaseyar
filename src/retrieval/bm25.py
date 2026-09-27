"""BM25 (Okapi) over catalog titles. The plain Phase 1 baseline: parameters are NOT tuned.

    score(q, d) = sum over distinct query terms t in d of
                  idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
    idf(t)      = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))      (Lucene form, never negative)

k1 = 1.5, b = 0.75 (textbook defaults). A query term counts once even if repeated.
Text: src.text.normalize.normalize() on BOTH titles and queries, then tokens = runs of \\w
(letters, digits, underscore), so punctuation such as ، / \\ ( ) - . splits tokens.

One document per catalog row (the snapshot's active rows in force on as_of). search() returns
distinct IDs: an ID with two rows in force is returned once, at its best row's rank.
Ties are broken by catalog row order, so results are deterministic.

Index: postings in CSR form (numpy arrays), with the per-posting BM25 term weight precomputed,
so a query is one gather plus a bincount over the matching postings.
"""
import re
from array import array
from collections import Counter

import numpy as np

from src.text.normalize import normalize

K1 = 1.5
B = 0.75
_TOKEN = re.compile(r"\w+")


def tokenize(text):
    return _TOKEN.findall(normalize(text))


class BM25Retriever:
    def __init__(self, docs, k1=K1, b=B):
        """docs: iterable of (sstid, title)."""
        self.k1, self.b = k1, b
        self.ids = []
        vocab = {}
        doc_col, term_col, tf_col, lengths = array("i"), array("i"), array("i"), array("i")
        for n, (sstid, title) in enumerate(docs):
            self.ids.append(sstid)
            toks = tokenize(title)
            lengths.append(len(toks))
            for term, count in Counter(toks).items():
                j = vocab.get(term)
                if j is None:
                    j = vocab[term] = len(vocab)
                doc_col.append(n)
                term_col.append(j)
                tf_col.append(count)
        self.vocab = vocab
        self.n_docs = n_docs = len(self.ids)
        lengths = np.frombuffer(lengths, dtype=np.int32).astype(np.float32)
        terms = np.frombuffer(term_col, dtype=np.int32)
        order = np.argsort(terms, kind="stable")          # group by term, doc order kept
        self.post_docs = np.frombuffer(doc_col, dtype=np.int32)[order].copy()
        tf = np.frombuffer(tf_col, dtype=np.int32)[order].astype(np.float32)
        del doc_col, term_col, tf_col
        df = np.bincount(terms, minlength=len(vocab))
        self.indptr = np.concatenate([[0], np.cumsum(df)]).astype(np.int64)
        self.avgdl = float(lengths.mean()) if n_docs else 0.0
        dl = lengths[self.post_docs]
        norm = k1 * (1 - b + b * dl / self.avgdl) if n_docs else dl
        self.post_w = (tf * (k1 + 1) / (tf + norm)).astype(np.float32)
        self.idf = np.log(1 + (n_docs - df + 0.5) / (df + 0.5)).astype(np.float32)

    def scores(self, text):
        """Dense score vector over all documents (float64). Zero for documents with no query term."""
        terms = [self.vocab[t] for t in dict.fromkeys(tokenize(text)) if t in self.vocab]
        if not terms:
            return np.zeros(self.n_docs)
        docs = np.concatenate([self.post_docs[self.indptr[t]:self.indptr[t + 1]] for t in terms])
        weights = np.concatenate([self.post_w[self.indptr[t]:self.indptr[t + 1]] * self.idf[t]
                                  for t in terms])
        return np.bincount(docs, weights=weights, minlength=self.n_docs)

    def search(self, text, k):
        scores = self.scores(text)
        hits = np.flatnonzero(scores > 0)
        out, m = [], k
        while True:
            if len(hits) <= m:
                take = hits
            else:  # everything scoring >= the m-th best, so ties at the cut are all kept
                cut = -np.partition(-scores[hits], m - 1)[m - 1]
                take = hits[scores[hits] >= cut]
            ranked = take[np.lexsort((take, -scores[take]))]    # score desc, then row order
            seen, out = set(), []
            for d in ranked:
                sstid = self.ids[d]
                if sstid not in seen:
                    seen.add(sstid)
                    out.append(sstid)
                    if len(out) == k:
                        return out
            if len(take) == len(hits):
                return out
            m *= 2

    def stats(self):
        arrays = (self.post_docs, self.post_w, self.indptr, self.idf)
        return {"docs": self.n_docs, "vocab": len(self.vocab), "postings": int(len(self.post_docs)),
                "avg_doc_tokens": round(self.avgdl, 2), "k1": self.k1, "b": self.b,
                "index_arrays_mb": round(sum(a.nbytes for a in arrays) / 2**20, 1)}
