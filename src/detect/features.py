"""Retrieval features from BM25 only (dense and reranker are descoped for v0.1, CLAUDE.md).

The index holds one document per catalog VERSION (all active rows, any date). A query is scored
against all of them, then only IDs whose rate_at(issue_date) is OK are ranked, each at its best
version's score. So alternatives are always IDs in force on the invoice date.

Features (architecture.md §6), all computed from raw BM25 scores s(.) for the line's `sstt`:
    s1, s2         best and second-best in-force ID scores
    sim_declared   s(declared) / s1, clipped to [0, 1]; 0 when s1 = 0. 1.0 = the declared ID
                   matches the text as well as the best in-force ID. s(declared) is the best score
                   over the declared ID's versions, in force or not.
    rank_declared  1-based rank of the declared ID among in-force IDs; RANK_ABSENT (21) if it is
                   not in the top 20 or not in force on the date
    margin_top1    (s1 - s2) / s1 in [0, 1]; 0 when fewer than 2 IDs score > 0. How clearly BM25
                   prefers ONE ID for this text, whichever ID that is.
"""
from dataclasses import dataclass

import numpy as np

from src.detect.rates import Status
from src.retrieval.bm25 import BM25Retriever

TOP_N = 20
RANK_ABSENT = TOP_N + 1


@dataclass(frozen=True)
class Candidate:
    sstid: str
    score: float
    version: object          # rates.Version in force on the date


@dataclass(frozen=True)
class RetrievalFeatures:
    sim_declared: float
    rank_declared: int
    margin_top1: float
    declared_score: float
    ranked: tuple            # Candidates, best first, at most TOP_N

    def as_dict(self):
        return {"sim_declared": self.sim_declared, "rank_declared": self.rank_declared,
                "margin_top1": self.margin_top1}


class CatalogSearch:
    def __init__(self, table):
        self.table = table
        docs = [(v.sstid, v.title) for v in table.all_versions()]
        self.bm25 = BM25Retriever(docs)
        self._doc_ids = np.array([d[0] for d in docs], dtype=object)

    def features(self, sstt, declared, issue_date):
        scores = self.bm25.scores(sstt)
        best = {}
        for d in np.flatnonzero(scores > 0):
            sid = self._doc_ids[d]
            if scores[d] > best.get(sid, 0.0):
                best[sid] = float(scores[d])
        declared_score = best.get(declared, 0.0)
        ranked = []
        # score desc, then sstid for determinism
        for sid, sc in sorted(best.items(), key=lambda kv: (-kv[1], kv[0])):
            res = self.table.rate_at(sid, issue_date)
            if res.status is Status.OK:
                ranked.append(Candidate(sid, sc, res.version))
                if len(ranked) == TOP_N:
                    break
        s1 = ranked[0].score if ranked else 0.0
        s2 = ranked[1].score if len(ranked) > 1 else 0.0
        sim = min(declared_score / s1, 1.0) if s1 > 0 else 0.0
        margin = (s1 - s2) / s1 if len(ranked) > 1 and s1 > 0 else 0.0
        rank = next((i for i, c in enumerate(ranked, 1) if c.sstid == declared), RANK_ABSENT)
        return RetrievalFeatures(sim, rank, margin, declared_score, tuple(ranked))
