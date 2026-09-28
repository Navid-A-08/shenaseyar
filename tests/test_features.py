"""src/detect/features.py: BM25 features and date-filtered alternatives."""
from decimal import Decimal

import pytest

from src.detect.features import RANK_ABSENT, CatalogSearch
from src.detect.rates import RateTable, Version

SPEC = "شناسه اختصاصی تولید داخل"


def v(sid, title, frm="1404-01-01", to=None):
    return Version(sid, title, Decimal("10"), "taxable", SPEC, frm, to)


TABLE = RateTable([
    v("2900000000001", "پیچ نمونه"),
    v("2900000000002", "پیچ نمونه فولادی بلند"),
    v("2900000000003", "پیچ نمونه قدیمی", to="1404-03-01"),
    v("2900000000004", "مهره"),
])
S = CatalogSearch(TABLE)


def test_declared_is_best_match():
    f = S.features("پیچ نمونه", "2900000000001", "1404-06-01")
    assert f.rank_declared == 1 and f.sim_declared == 1.0
    assert 0 < f.margin_top1 < 1


def test_declared_is_second():
    f = S.features("پیچ نمونه", "2900000000002", "1404-06-01")
    assert f.rank_declared == 2 and 0 < f.sim_declared < 1
    assert [c.sstid for c in f.ranked] == ["2900000000001", "2900000000002"]


def test_alternatives_are_only_ids_in_force_on_the_date():
    before = S.features("پیچ نمونه قدیمی", "2900000000003", "1404-02-01")
    after = S.features("پیچ نمونه قدیمی", "2900000000003", "1404-06-01")
    assert before.ranked[0].sstid == "2900000000003"
    assert "2900000000003" not in [c.sstid for c in after.ranked]
    assert after.rank_declared == RANK_ABSENT and after.sim_declared > 0


def test_no_match_at_all():
    f = S.features("xyz", "2900000000001", "1404-06-01")
    assert (f.sim_declared, f.rank_declared, f.margin_top1, f.ranked) == (0.0, RANK_ABSENT, 0.0, ())


def test_single_candidate_has_zero_margin():
    f = S.features("مهره", "2900000000004", "1404-06-01")
    assert f.rank_declared == 1 and f.margin_top1 == 0.0


def test_features_are_in_range():
    for q in ["پیچ", "پیچ نمونه فولادی", "مهره پیچ"]:
        for sid in ["2900000000001", "2900000000004", "2999999999999"]:
            d = S.features(q, sid, "1404-06-01").as_dict()
            assert 0 <= d["sim_declared"] <= 1 and 0 <= d["margin_top1"] <= 1
            assert 1 <= d["rank_declared"] <= RANK_ABSENT
