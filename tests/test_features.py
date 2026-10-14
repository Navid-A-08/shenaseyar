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


def test_fast_ranking_equals_the_full_scan():
    """_rank looks only at the top-m documents; it must match the original full scan exactly,
    including when m is tiny and the window has to widen (m=1), and on BM25 ties."""
    import csv as _csv
    from pathlib import Path as _P
    from src.detect.rates import RateTable as _RT
    fake = _P(__file__).resolve().parents[1] / "data" / "sample" / "demo_invoices.csv"
    table = _RT.from_catalog(_P(__file__).resolve().parents[1] / "data" / "sample" / "fake_catalog.csv")
    s = CatalogSearch(table)
    rows = list(_csv.DictReader(open(fake, encoding="utf-8")))
    assert len(rows) == 200
    for r in rows:
        args = (r["sstt"], r["sstid"], r["issue_date"])
        assert s.features(*args) == s._features_reference(*args), r["line_id"]
        scores = s.bm25.scores(r["sstt"])
        pos = __import__("numpy").flatnonzero(scores > 0)
        for m in (1, 2, 3):
            assert s._rank(scores, pos, r["issue_date"], m=m) == list(
                s._features_reference(*args).ranked), (r["line_id"], m)
