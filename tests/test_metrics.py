import pytest

from eval.metrics import mrr, rank_of, rank_of_any, recall_at_k


def test_rank_of_first_middle_absent():
    assert rank_of("a", ["a", "b", "c"]) == 1
    assert rank_of("c", ["a", "b", "c"]) == 3
    assert rank_of("z", ["a", "b", "c"]) is None
    assert rank_of("a", []) is None


def test_rank_of_counts_only_first_occurrence_of_duplicates():
    # b appears twice before c; c is the 3rd distinct ID, not the 4th item
    assert rank_of("c", ["a", "b", "b", "c"]) == 3


# Hits at rank 1, 3 and 6, plus one miss: the textbook case.
RANKS = [1, 3, 6, None]


def test_recall_at_k():
    assert recall_at_k(RANKS, 1) == pytest.approx(1 / 4)
    assert recall_at_k(RANKS, 5) == pytest.approx(2 / 4)
    assert recall_at_k(RANKS, 6) == pytest.approx(3 / 4)


def test_mrr():
    assert mrr(RANKS) == pytest.approx((1 + 1 / 3 + 1 / 6 + 0) / 4)  # 0.375
    assert mrr([1, 1]) == 1.0
    assert mrr([None, None]) == 0.0


def test_no_queries_gives_none_not_zero():
    assert recall_at_k([], 5) is None
    assert mrr([]) is None


def test_rank_of_any_takes_best_rank():
    assert rank_of_any(["c", "a"], ["a", "b", "c"]) == 1
    assert rank_of_any(["z", "c"], ["a", "b", "c"]) == 3
    assert rank_of_any(["z", "y"], ["a", "b"]) is None
    assert rank_of_any([], ["a"]) is None


def test_rank_of_any_uses_first_occurrence_rule():
    assert rank_of_any(["c", "d"], ["a", "b", "b", "c", "d"]) == 3
