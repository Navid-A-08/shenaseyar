"""src/retrieval/catalog.py tests: invented rows in tmp_path, plus the committed fake catalog."""
import csv
import io
import zipfile
from pathlib import Path

import pytest

from src.retrieval.catalog import HEADER, CatalogError, iter_rows, load_snapshot

FAKE_CATALOG = Path(__file__).resolve().parents[1] / "data" / "sample" / "fake_catalog.csv"
AS_OF = "1405-07-01"


def _row(id_, title="کالای فرضی", run="1404-01-01", exp="", vat="10"):
    return [id_, title, vat, "مشمول", run, exp, "1404-01-01", "1404-01-01",
            "شناسه اختصاصی تولید داخل", ""]


def _csv(tmp_path, rows, name="c.csv"):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    p = tmp_path / name
    p.write_bytes(("﻿" + buf.getvalue()).encode("utf-8"))
    return p


ROWS = [
    _row("2900000000001"),                                   # clean, open-ended
    _row("2900000000002"), _row("2900000000002"),            # R1: exact duplicate -> one kept
    _row("2900000000003", vat="10"),                         # R2: same (ID, RunDate),
    _row("2900000000003", vat="9"),                          #     different content -> both out
    _row("2900000000004", run="1404-05-01", exp="1404-04-30"),  # R3 only row -> quarantined-only
    _row("29000000000050"),                                  # R4: 14 characters
    _row("2900000000006", run="1403-01-01", exp="1403-12-29"),  # active but expired
    _row("2900000000007", vat="10"),                         # R2 on one version,
    _row("2900000000007", vat="9"),
    _row("2900000000007", run="1405-01-01"),                 # ... another version is fine
    _row("2900000000008", run="1405-08-01"),                 # active, starts after as_of
    _row("2900000000009", exp=AS_OF),                        # in force: end is inclusive
]


def test_rules_and_counts(tmp_path):
    s = load_snapshot(_csv(tmp_path, ROWS), AS_OF)
    assert s.counts["r1_extra_copies"] == 1
    assert (s.counts["r2_keys"], s.counts["r2_rows"]) == (2, 4)
    assert s.counts["r3_rows"] == 1 and s.counts["r4_rows"] == 1
    assert s.quarantined_only_ids == {"2900000000003", "2900000000004", "29000000000050"}
    assert "2900000000007" in s.active_ids and "2900000000007" not in s.quarantined_only_ids
    assert s.index_ids == {"2900000000001", "2900000000002", "2900000000007", "2900000000009"}
    assert s.docs.count(("2900000000002", "کالای فرضی")) == 1       # duplicate indexed once
    assert "2900000000006" in s.active_ids - s.index_ids             # expired
    assert "2900000000008" in s.active_ids - s.index_ids             # not started yet
    assert s.raw_ids >= s.active_ids | s.quarantined_only_ids


def test_row_with_two_reasons_counted_once_per_rule(tmp_path):
    rows = [_row("2900000000001", run="1404-05-01", exp="1404-04-30", vat="10"),
            _row("2900000000001", run="1404-05-01", exp="1404-04-30", vat="9")]
    s = load_snapshot(_csv(tmp_path, rows), AS_OF)
    assert s.counts["r2_rows"] == 2 and s.counts["r3_rows"] == 2
    assert s.counts["rows_with_several_reasons"] == 2


@pytest.mark.parametrize("bad", ["1404-1-01", "1404/01/01", "۱۴۰۴-۰۱-۰۱", ""])
def test_bad_date_is_an_error_naming_the_row(tmp_path, bad):
    with pytest.raises(CatalogError, match=r"row 2 \(ID 2900000000002\)"):
        load_snapshot(_csv(tmp_path, [_row("2900000000001"), _row("2900000000002", run=bad)]), AS_OF)


def test_bad_as_of_is_an_error(tmp_path):
    with pytest.raises(CatalogError, match="as_of"):
        load_snapshot(_csv(tmp_path, ROWS), "1405-7-1")


def test_zip_and_csv_agree(tmp_path):
    p = _csv(tmp_path, ROWS)
    z = tmp_path / "c.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(p, "c.csv")
    a, b = load_snapshot(p, AS_OF), load_snapshot(z, AS_OF)
    assert a.docs == b.docs and a.counts == b.counts


def test_bad_header_and_field_count(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("ID,Title\n1,x\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="unexpected header"):
        list(iter_rows(p))
    p.write_text(",".join(HEADER) + "\n1,2,3\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="3 fields"):
        list(iter_rows(p))


def test_fake_catalog_snapshot():
    s = load_snapshot(FAKE_CATALOG, AS_OF)
    assert s.counts["raw_rows"] == 50 and s.counts["r1_extra_copies"] == 0
    assert len(s.raw_ids) == 44
    assert s.index_ids <= s.active_ids <= s.raw_ids
