"""Retrieval metrics. Pure functions; a rank is 1-based, None means "not in the returned list"."""


def rank_of(expected, results):
    """1-based rank of `expected` in `results`, counting only the first occurrence of each ID."""
    seen = set()
    rank = 0
    for sstid in results:
        if sstid in seen:
            continue
        seen.add(sstid)
        rank += 1
        if sstid == expected:
            return rank
    return None


def recall_at_k(ranks, k):
    """Share of queries whose expected ID is in the top k. None when there are no queries."""
    if not ranks:
        return None
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks):
    """Mean reciprocal rank; a query with no hit contributes 0. None when there are no queries."""
    if not ranks:
        return None
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)
