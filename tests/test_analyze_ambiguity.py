"""tools/analyze_ambiguity.py tests on an invented catalog zipped in tmp_path."""
import csv
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("analyze_ambiguity", REPO / "tools" / "analyze_ambiguity.py")
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)
cs = aa.cs

DOM, SRV = "شناسه اختصاصی تولید داخل", "شناسه اختصاصی خدمت"
AS_OF = "1405-07-01"


def _row(id_, title, type_=DOM):
    return [id_, title, "10", "مشمول", "1404-01-01", "", "1404-01-01", "1404-01-01", type_, ""]


# One pair per category; the first ID of each pair is the tier C source row.
CATALOG = [
    # a later attribute separates them
    _row("2900000000001", "پیچ، فولادی، سایز 5، طول 10، سازنده الف، ایران"),
    _row("2900000000002", "پیچ، فولادی، سایز 5، طول 20، سازنده الف، ایران"),
    # only the manufacturer separates them
    _row("2900000000003", "مهره، فولادی، سازنده الف، ایران"),
    _row("2900000000004", "مهره، فولادی، سازنده ب، ایران"),
    # only the part number separates them
    _row("2900000000005", "واشر، لاستیکی، سازنده الف، شماره فنی W1"),
    _row("2900000000006", "واشر، لاستیکی، سازنده الف، شماره فنی W2"),
    # nothing separates them
    _row("2900000000007", "بست، فلزی، سازنده الف"),
    _row("2900000000008", "بست، فلزی، سازنده الف"),
    # a service whose company is the only difference (trailing "/ شرکت ...")
    _row("2330000000009", "خدمات نظافت/نظافت اداری/ شرکت الف", SRV),
    _row("2330000000010", "خدمات نظافت/نظافت اداری/ شرکت ب", SRV),
]
SOURCES = ["2900000000001", "2900000000003", "2900000000005", "2900000000007", "2330000000009"]


def _zip(tmp_path, rows=CATALOG):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(cs.HEADER)
    w.writerows(rows)
    path = tmp_path / "catalog.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("catalog.csv", ("﻿" + buf.getvalue()).encode("utf-8"))
    return path


def _meta(tmp_path, sources=SOURCES, extra_s=("2900000000002",)):
    path = tmp_path / "silver_meta.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row", "source_id", "type", "vat", "length", "n_matches", "tier"])
        for n, i in enumerate(sources, start=1):
            w.writerow([n, i, DOM, "10", "short", 2, "C"])
        for i in extra_s:  # tier S rows are ignored
            w.writerow([99, i, DOM, "10", "short", 1, "S"])
    return path


def test_detail_queries():
    q = aa.detail_queries(dict(zip(cs.HEADER, CATALOG[4])))
    assert q == {"all_attrs": "واشر لاستیکی",
                 "all_attrs+mfr": "واشر لاستیکی سازنده الف",
                 "all_attrs+partno": "واشر لاستیکی شماره فنی W1"}
    assert aa.detail_queries(dict(zip(cs.HEADER, CATALOG[8])))["all_attrs+mfr"] == \
        "خدمات نظافت نظافت اداری شرکت الف"
    assert aa.detail_queries(dict(zip(cs.HEADER, CATALOG[6])))["all_attrs+partno"] is None


def test_classify_each_category(tmp_path):
    rep = aa.analyze(_zip(tmp_path), SOURCES, AS_OF)
    by_id = {r["source_id"]: r["category"] for r in rep["rows"]}
    assert by_id == {"2900000000001": "unique_with_all_attrs",
                     "2900000000003": "needs_mfr_or_partno",
                     "2900000000005": "needs_mfr_or_partno",
                     "2900000000007": "unique_under_none",
                     "2330000000009": "needs_mfr_or_partno"}
    assert rep["categories"] == {"unique_with_all_attrs": 1, "needs_mfr_or_partno": 3,
                                 "unique_under_none": 1}
    assert rep["needs_mfr_or_partno_split"] == {"mfr_only": 2, "partno_only": 1, "either": 0}
    assert rep["segments_present"] == {"mfr": 5, "partno": 1}
    assert rep["shares"]["needs_mfr_or_partno"] == 0.6


def test_only_tier_c_rows_are_analysed(tmp_path):
    assert aa.tier_c_sources(_meta(tmp_path)) == SOURCES


def test_missing_source_is_reported_not_counted(tmp_path):
    rep = aa.analyze(_zip(tmp_path), SOURCES + ["2909999999999"], AS_OF)
    assert rep["missing_from_catalog"] == ["2909999999999"]
    assert rep["tier_C_rows"] == len(SOURCES)


def test_main_writes_deterministic_json_without_text(tmp_path):
    z, meta, out = _zip(tmp_path), _meta(tmp_path), tmp_path / "r" / "ambiguity.json"
    args = ["--zip", str(z), "--meta", str(meta), "--out", str(out)]
    aa.main(args)
    first = out.read_bytes()
    aa.main(args)
    assert out.read_bytes() == first
    text = out.read_text(encoding="utf-8")
    assert not re.search(r"[؀-ۿ]", text)   # no Persian text: counts and IDs only
    rep = json.loads(text)
    assert rep["catalog_sha256"] and rep["silver_meta_sha256"] and rep["tier_C_rows"] == 5


def test_src_and_eval_do_not_import_analyze_ambiguity():
    pat = re.compile(r"^\s*(?:import\s+(?:tools\.)?analyze_ambiguity\b"
                     r"|from\s+(?:tools\.)?analyze_ambiguity\s+import\b"
                     r"|from\s+tools\s+import\s+[^#\n]*\banalyze_ambiguity\b)", re.MULTILINE)
    offenders = [str(p) for d in ("src", "eval") if (REPO / d).is_dir()
                 for p in (REPO / d).rglob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
