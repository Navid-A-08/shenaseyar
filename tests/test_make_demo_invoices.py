"""tools/make_demo_invoices.py: reproducible, labeled as documented; labels re-checked here
independently of the rule engine (from rate_at and plain arithmetic)."""
import csv
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import make_demo_invoices as mdi  # noqa: E402
from src.detect.rates import RateTable, Status  # noqa: E402

FAKE = ROOT / "data" / "sample" / "fake_catalog.csv"
COMMITTED = ROOT / "data" / "sample" / "demo_invoices.csv"
TABLE = RateTable.from_catalog(FAKE)


@pytest.fixture(scope="module")
def rows():
    return mdi.generate(TABLE, 200, 1405)


def test_committed_file_is_the_seeded_output(tmp_path):
    out = tmp_path / "d.csv"
    mdi.main(["--out", str(out)])
    assert out.read_bytes() == COMMITTED.read_bytes()


def test_same_seed_same_rows_other_seed_differs(rows):
    assert mdi.generate(TABLE, 200, 1405) == rows
    assert mdi.generate(TABLE, 200, 7) != rows


def test_mix(rows):
    c = Counter(r["labels"] or "clean" for r in rows)
    assert c == {"clean": 152, "T2": 10, "T3": 10, "T4": 10, "T6": 10,
                 "NOT_IN_CATALOG": 4, "NOT_IN_FORCE": 4}
    assert [r["line_id"] for r in rows] == [f"L{i:03d}" for i in range(1, 201)]
    assert all(r["issue_date"] <= mdi.DATE_MAX and r["issue_date"] >= mdi.DATE_MIN for r in rows)


def _exact(r):
    return Decimal(r["am"]) * Decimal(r["fee"]) * Decimal(r["vra"]) / 100


def test_labels_hold_by_independent_check(rows):
    for r in rows:
        res = TABLE.rate_at(r["sstid"], r["issue_date"])
        lab, vra, off = r["labels"], Decimal(r["vra"]), abs(Decimal(r["vam"]) - _exact(r))
        if lab == "NOT_IN_CATALOG":
            assert res.status is Status.NOT_IN_CATALOG
        elif lab == "NOT_IN_FORCE":
            assert res.status is Status.NOT_IN_FORCE
        else:
            assert res.status is Status.OK, r
            assert (vra != res.rate) is (lab == "T2"), r
            assert (off > 1) is (lab == "T6"), r
            if lab != "T6":
                assert off < 1, r
            if lab == "T3":
                assert res.version.is_general
        if lab in ("NOT_IN_CATALOG", "NOT_IN_FORCE"):
            assert off < 1


def test_t2_rates_come_from_the_table(rows):
    rates = set(TABLE.distinct_rates())
    assert all(Decimal(r["vra"]) in rates for r in rows)


def test_t4_amounts_straddle_the_threshold(rows):
    thr = mdi.t4_threshold()
    for r in rows:
        if r["sstt"] in mdi.VAGUE_PHRASES:
            base = Decimal(r["am"]) * Decimal(r["fee"])
            assert (base >= thr * Decimal("1.2")) if r["labels"] == "T4" else (base <= thr * Decimal("0.8"))


def test_file_is_utf8_with_expected_columns():
    with open(COMMITTED, encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == mdi.COLUMNS
