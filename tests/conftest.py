"""Shared helpers: invented catalog rows written to tmp_path (never real data)."""
import csv
import io

import pytest

from src.retrieval.catalog import HEADER

DOM_SPECIFIC = "شناسه اختصاصی تولید داخل"
DOM_GENERAL = "شناسه عمومی تولید داخل"


def cat_row(id_, title="کالای فرضی", vat="10", run="1404-01-01", exp="", taxable="مشمول",
            type_=DOM_SPECIFIC):
    return [id_, title, vat, taxable, run, exp, "1404-01-01", "1404-01-01", type_, ""]


@pytest.fixture
def make_catalog(tmp_path):
    def _make(rows, name="c.csv"):
        buf = io.StringIO(newline="")
        w = csv.writer(buf)
        w.writerow(HEADER)
        w.writerows(rows)
        p = tmp_path / name
        p.write_bytes(("﻿" + buf.getvalue()).encode("utf-8"))
        return p
    return _make
