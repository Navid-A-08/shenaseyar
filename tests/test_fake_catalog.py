"""The invented test catalog must keep the structure of the real export (docs/data_dictionary.md)."""
import csv
import re
from collections import defaultdict
from pathlib import Path

FAKE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fake_catalog.csv"

HEADER = ["ID", "DescriptionOfID", "Vat", "Taxable", "RunDate", "ExpirationDate",
          "CreateDate", "LastEditDate", "Type", "PricingDescription"]
TYPE_PREFIX = {
    "شناسه اختصاصی تولید داخل": "290",
    "شناسه اختصاصی وارداتی": "280",
    "شناسه اختصاصی خدمت": "233",
    "شناسه عمومی تولید داخل": "272",
    "شناسه عمومی وارداتی": "271",
    "شناسه عمومی خدمت": "233",
}
TAXABLE = {"مشمول", "معاف", "غیر مشمول"}
JALALI = re.compile(r"^1[34]\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _rows():
    with open(FAKE, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_encoding_is_utf8_with_bom():
    assert FAKE.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_header_and_row_count():
    rows = _rows()
    assert rows[0] == HEADER
    assert len(rows) - 1 == 50
    assert all(len(r) == len(HEADER) for r in rows[1:])


def test_field_formats():
    for r in _rows()[1:]:
        rec = dict(zip(HEADER, r))
        assert re.fullmatch(r"\d{13}", rec["ID"])
        assert rec["DescriptionOfID"].strip()
        assert re.fullmatch(r"\d+(\.\d+)?", rec["Vat"])
        assert rec["Taxable"] in TAXABLE
        assert rec["Type"] in TYPE_PREFIX
        assert rec["ID"].startswith(TYPE_PREFIX[rec["Type"]])
        for col in ("RunDate", "CreateDate", "LastEditDate"):
            assert JALALI.match(rec[col]), (col, rec[col])
        assert rec["ExpirationDate"] == "" or JALALI.match(rec["ExpirationDate"])


def test_history_periods_do_not_overlap():
    by_id = defaultdict(list)
    for r in _rows()[1:]:
        rec = dict(zip(HEADER, r))
        by_id[rec["ID"]].append(rec)
    history = {k: v for k, v in by_id.items() if len(v) > 1}
    assert history, "fake catalog should include change-history IDs"
    for recs in history.values():
        recs.sort(key=lambda x: x["RunDate"])
        for a, b in zip(recs, recs[1:]):
            assert a["ExpirationDate"] and a["ExpirationDate"] < b["RunDate"]
        assert sum(1 for x in recs if not x["ExpirationDate"]) == 1
