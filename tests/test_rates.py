"""src/detect/rates.py: half-open conversion, rate_at statuses, and the fake catalog."""
from decimal import Decimal
from pathlib import Path

import pytest

from src.detect.rates import (RateTable, RateTableError, Status, Version, next_day,
                              parse_jalali)
from tests.conftest import cat_row

FAKE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fake_catalog.csv"


def test_next_day_uses_the_jalali_calendar():
    assert next_day("1403-12-30") == "1404-01-01"      # 1403 is a leap year
    assert next_day("1404-06-31") == "1404-07-01"
    assert next_day("1404-07-30") == "1404-08-01"
    with pytest.raises(RateTableError):
        next_day("1404-12-30")                          # 1404 is not a leap year


@pytest.mark.parametrize("bad", ["1404-1-01", "14040101", "1404-13-01", "", None])
def test_parse_jalali_rejects_bad_dates(bad):
    with pytest.raises(RateTableError):
        parse_jalali(bad)


def test_inclusive_expiration_becomes_half_open(make_catalog):
    t = RateTable.from_catalog(make_catalog([cat_row("2900000000001", exp="1404-06-31")]))
    (v,) = t.versions("2900000000001")
    assert (v.valid_from, v.valid_to_excl, v.valid_to_incl) == \
        ("1404-01-01", "1404-07-01", "1404-06-31")
    assert t.rate_at("2900000000001", "1404-01-01").status is Status.OK      # first day
    assert t.rate_at("2900000000001", "1404-06-31").status is Status.OK      # last day
    assert t.rate_at("2900000000001", "1404-07-01").status is Status.NOT_IN_FORCE
    assert t.rate_at("2900000000001", "1403-12-29").status is Status.NOT_IN_FORCE


def test_statuses(make_catalog):
    rows = [
        cat_row("2900000000001", vat="9", run="1402-01-01", exp="1402-12-29"),
        cat_row("2900000000001", vat="10", run="1403-01-01"),            # gap: none
        cat_row("2900000000002", vat="10", run="1404-01-01"),             # two open versions,
        cat_row("2900000000002", vat="9", run="1404-03-01"),              #  latest wins
        cat_row("2900000000003", vat="10", run="1404-01-01", exp="1404-12-29"),
        cat_row("2900000000004", vat="10"), cat_row("2900000000004", vat="9"),  # R2 -> quarantined
        cat_row("2900000000005", vat="12.5", taxable="معاف"),
    ]
    t = RateTable.from_catalog(make_catalog(rows))
    assert t.rate_at("2900000000001", "1402-06-01").rate == Decimal("9")
    assert t.rate_at("2900000000001", "1403-06-01").rate == Decimal("10")
    assert t.rate_at("2900000000002", "1404-02-01").rate == Decimal("10")
    assert t.rate_at("2900000000002", "1404-05-01").rate == Decimal("9")
    assert t.rate_at("2900000000003", "1405-01-01").status is Status.NOT_IN_FORCE
    assert t.rate_at("2900000000001", "1401-12-29").status is Status.NOT_IN_FORCE  # before first
    assert t.rate_at("2900000000009", "1404-01-01").status is Status.NOT_IN_CATALOG
    assert t.rate_at("2900000000004", "1404-06-01").status is Status.QUARANTINED_ONLY
    r = t.rate_at("2900000000005", "1404-06-01")
    assert (r.rate, r.version.tax_status) == (Decimal("12.5"), "exempt")
    assert r.rate is not None and t.rate_at("2900000000009", "1404-01-01").rate is None


def test_tie_on_latest_valid_from_is_ambiguous_never_a_pick():
    a = Version("2900000000001", "x", Decimal("10"), "taxable", "t", "1404-01-01", None)
    b = Version("2900000000001", "x", Decimal("9"), "taxable", "t", "1404-01-01", "1405-01-01")
    res = RateTable([a, b]).rate_at("2900000000001", "1404-06-01")
    assert res.status is Status.AMBIGUOUS and res.rate is None
    assert set(res.candidates) == {a, b}


def test_invalid_issue_date_is_an_error(make_catalog):
    t = RateTable.from_catalog(make_catalog([cat_row("2900000000001")]))
    with pytest.raises(RateTableError):
        t.rate_at("2900000000001", "1404/01/01")


def test_fake_catalog_history_and_gap():
    t = RateTable.from_catalog(FAKE)
    assert t.rate_at("2909206651897", "1403-06-01").rate == Decimal("9")
    assert t.rate_at("2909206651897", "1404-06-01").rate == Decimal("10")
    assert t.rate_at("2909206651897", "1403-12-30").status is Status.NOT_IN_FORCE  # leap-day gap
    assert t.rate_at("2809257424641", "1404-01-01").version.tax_status == "exempt"
    assert t.rate_at("2729744617259", "1405-01-01").version.is_general
    assert Decimal("12.5") in t.distinct_rates()
