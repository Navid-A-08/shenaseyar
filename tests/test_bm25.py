"""BM25Retriever tests: scores checked against the formula computed by hand, independently."""
import math

import pytest

from src.retrieval.bm25 import B, K1, BM25Retriever, tokenize

DOCS = [
    ("2900000000001", "دفتر سیمی 100 برگ"),
    ("2900000000002", "دفتر دفتر مشق"),
    ("2900000000003", "مداد مشکی"),
    ("2900000000004", "کیک شکلاتی"),
]


def _bm25(tf, dl, avgdl, df, n):
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    return idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / avgdl))


def test_scores_match_hand_computation():
    r = BM25Retriever(DOCS)
    avgdl = (4 + 3 + 2 + 2) / 4
    s = r.scores("دفتر")
    assert s[0] == pytest.approx(_bm25(1, 4, avgdl, 2, 4), rel=1e-6)
    assert s[1] == pytest.approx(_bm25(2, 3, avgdl, 2, 4), rel=1e-6)
    assert s[2] == 0 and s[3] == 0
    two = r.scores("دفتر مشق")
    assert two[1] == pytest.approx(_bm25(2, 3, avgdl, 2, 4) + _bm25(1, 3, avgdl, 1, 4), rel=1e-6)


def test_repeated_query_term_counts_once():
    r = BM25Retriever(DOCS)
    assert list(r.scores("دفتر دفتر")) == list(r.scores("دفتر"))


def test_search_order_and_k():
    r = BM25Retriever(DOCS)
    assert r.search("دفتر مشق", 5) == ["2900000000002", "2900000000001"]
    assert r.search("دفتر", 1) == ["2900000000002"]
    assert r.search("ناموجود", 5) == [] and r.search("", 5) == []


def test_normalization_applied_to_both_sides():
    r = BM25Retriever(DOCS + [("2900000000005", "بسته‌بندی آچار ۱۲")])
    assert r.search("كيك", 5) == ["2900000000004"]                    # Arabic kaf/yeh
    assert r.search("بسته بندی", 5) == ["2900000000005"]              # ZWNJ in the title
    assert r.search("اچار 12", 5) == ["2900000000005"]                # آ and Persian digits
    assert tokenize("سایز 1\\2 in، MI-42(ES)") == ["سایز", "1", "2", "in", "mi", "42", "es"]


def test_duplicate_id_returned_once_at_best_rank():
    docs = [("A000000000001", "لیوان شیشه"), ("A000000000002", "لیوان"),
            ("A000000000001", "لیوان شیشه ای بزرگ")]
    r = BM25Retriever(docs)
    assert r.search("لیوان شیشه", 5) == ["A000000000001", "A000000000002"]


def test_ties_broken_by_catalog_order_even_at_the_cut():
    docs = [(f"29000000000{i:02d}", "پیچ فولادی") for i in range(30)]
    r = BM25Retriever(docs)
    assert r.search("پیچ", 5) == [f"29000000000{i:02d}" for i in range(5)]


def test_stats():
    st = BM25Retriever(DOCS).stats()
    assert st["docs"] == 4 and st["k1"] == K1 and st["b"] == B
    assert st["postings"] == 4 + 2 + 2 + 2          # distinct terms per doc
    assert st["vocab"] == 9
