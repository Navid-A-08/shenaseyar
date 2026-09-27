"""tools/make_silver_v2b.py and tools/silver_v2b_report.py on an invented catalog and pair list."""
import csv
import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest

from eval.run_eval import evaluate, is_silver, load_golden, parse_row
from src.retrieval.bm25 import BM25Retriever
from src.retrieval.catalog import HEADER

REPO = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v2b = _load("make_silver_v2b")
rep = _load("silver_v2b_report")
DOM = "شناسه اختصاصی تولید داخل"
AS_OF = "1405-07-01"


def _row(id_, title, run="1404-01-01"):
    return [id_, title, "10", "مشمول", run, "", "1404-01-01", "1404-01-01", DOM, ""]


CATALOG = [
    _row("2900000000001", "تلفن همراه، هوشمند، مشکی، برند الف، سازنده فرضی"),
    _row("2900000000002", "تلفن همراه، هوشمند، سفید، برند ب، سازنده فرضی"),
    _row("2900000000003", "تلفن همراه ساده، دکمه ای، برند ج، سازنده فرضی"),
    _row("2900000000004", "گوشی هوشمند، سفید، برند الف، سازنده دیگر"),      # starts with seller term
    _row("2900000000005", "قاب تلفن همراه، سیلیکونی، برند د"),               # contains, not assigned*
    _row("2900000000006", "لپ تاپ، 15 اینچ، برند ه، سازنده فرضی"),           # for the reversed pair
    _row("2900000000007", "لپ تاپ، 13 اینچ، برند و، سازنده فرضی"),
]
# * "قاب تلفن همراه" contains the catalog term, so it IS assigned to it; it is in the L0 class but
#   not in L1+ labels built from other heads (its head does not contain "تلفن همراه هوشمند").
PAIRS = """# invented
catalog_term,seller_term,note
تلفن همراه,گوشی,
لپ تاپ,رایانه قابل حمل,direction=reversed
ماشین حساب,حساب گر,
"""


def _files(tmp_path, pairs=PAIRS, rows=CATALOG):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    z = tmp_path / "catalog.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("catalog.csv", ("﻿" + buf.getvalue()).encode("utf-8"))
    p = tmp_path / "pairs.csv"
    p.write_text(pairs, encoding="utf-8")
    return z, p


def _build(tmp_path, pairs=PAIRS, **kw):
    z, p = _files(tmp_path, pairs)
    params = {"seed": 1, "k_rows": 10, "as_of": AS_OF, **kw}
    return v2b.build(z, v2b.v2.read_pairs(p), **params)


def _by(rows, meta):
    return {(m[1], m[2], r[0]): r for r, m in zip(rows, meta)}


def test_pair_id_is_stable_and_direction_parsed():
    assert v2b.pair_id("تلفن همراه", "گوشی") == v2b.pair_id("تلفن  همراه", "گوشي")   # normalized
    assert v2b.direction("direction=reversed") == "reversed"
    assert v2b.direction("direction = reversed; note") == "reversed"
    assert v2b.direction("verify") == "original"


def test_ladder_levels():
    lad = v2b.ladder(["گوشی"], "الف", ["هوشمند", "مشکی", "بزرگ"])
    assert lad == {"L1": (["گوشی"], []), "L2": (["گوشی", "هوشمند"], ["هوشمند"]),
                   "L3": (["گوشی", "هوشمند", "مشکی"], ["هوشمند", "مشکی"]),
                   "L4": (["گوشی", "الف", "هوشمند", "مشکی"], ["الف", "هوشمند", "مشکی"])}
    assert set(v2b.ladder(["گوشی"], None, [])) == {"L1"}
    long = v2b.ladder(["x"], None, ["یک دو سه چهار پنج"])["L2"][1]
    assert long == ["یک", "دو", "سه", "چهار"]                                  # 4-word cut, as v1


def test_default_class_rule_is_prefix(tmp_path):
    assert v2b.DEFAULTS["class_rule"] == "prefix"
    assert _build(tmp_path)[2]["params"]["class_rule"] == "prefix"


def test_levels_and_labels_contains_rule(tmp_path):
    rows, meta, report = _build(tmp_path, class_rule="contains")
    phone = v2b.pair_id("تلفن همراه", "گوشی")
    got = _by(rows, meta)
    l0 = got[(phone, "L0", "گوشی")]
    # L0: whole class (heads assigned to تلفن همراه, incl. قاب تلفن همراه) + heads starting with گوشی
    assert set(l0[1].split(";")) == {"2900000000001", "2900000000002", "2900000000003",
                                     "2900000000004", "2900000000005"}
    # L2 from row 1: head contains "تلفن همراه" (assigned) or starts with "گوشی", AND text has هوشمند
    l2 = got[(phone, "L2", "گوشی هوشمند")]
    assert set(l2[1].split(";")) == {"2900000000001", "2900000000002", "2900000000004"}
    # L3 adds مشکی: only row 1
    l3 = got[(phone, "L3", "گوشی هوشمند مشکی")]
    assert l3[1] == "2900000000001" and l3[2] == "S"
    for q, ids, tier, notes in rows:
        assert re.fullmatch(r"silver-v2b\|pair:[0-9a-f]{10}\|L[0-4]\|(original|reversed)\|1", notes)


def test_identical_queries_kept_once_per_level(tmp_path):
    rows, meta, report = _build(tmp_path, class_rule="contains")
    phone = next(p for p in report["pair_details"] if p["seller_term"] == "گوشی")
    # rows 1 and 2 share head "تلفن همراه" -> L1 "گوشی" once (and it equals L0, a different level).
    # "قاب تلفن همراه" contains the catalog term, so the "contains" rule assigns it to the class
    # (a known asymmetry with the seller side's "starts with"; measured in docs/silver_set.md).
    assert phone["queries"]["L1"] == 3                # "گوشی", "گوشی ساده", "قاب گوشی"
    assert phone["duplicates"]["L1"] >= 1


def test_zero_coverage_and_reversed_tagged(tmp_path):
    rows, meta, report = _build(tmp_path)
    zero = v2b.pair_id("ماشین حساب", "حساب گر")
    assert report["zero_coverage"] == [zero]
    laptop = next(p for p in report["pair_details"] if p["catalog_term"] == "لپ تاپ")
    assert laptop["direction"] == "reversed"
    assert all(m[3] == "reversed" for m in meta if m[1] == laptop["pair_id"])


def test_adding_a_pair_does_not_change_existing_pairs(tmp_path):
    rows_a, meta_a, _ = _build(tmp_path, k_rows=2)
    more = PAIRS + "تلفن همراه,موبایل,\n"
    rows_b, meta_b, _ = _build(tmp_path, pairs=more, k_rows=2)
    old = {m[1] for m in meta_a}
    keep_b = [(r, m[1:]) for r, m in zip(rows_b, meta_b) if m[1] in old]
    assert [(r, m[1:]) for r, m in zip(rows_a, meta_a)] == keep_b


def test_deterministic_and_valid_for_harness(tmp_path):
    a, b = _build(tmp_path), _build(tmp_path)
    assert a == b
    path = tmp_path / "silver_v2b.csv"
    v2b.ms.write_csv(path, v2b.ms.GOLDEN_HEADER, a[0])
    g = load_golden(path)
    assert all(parse_row(r)[2] is None for r in g) and is_silver(path, g)


def test_random_hit_probability():
    assert rep.random_hit(0, 100, 5) == 0
    assert rep.random_hit(100, 100, 5) == 1
    assert rep.random_hit(1, 100, 1) == pytest.approx(0.01)
    assert rep.random_hit(1, 100, 5) == pytest.approx(0.05)
    assert rep.random_hit(10, 100, 2) == pytest.approx(1 - (90 / 100) * (89 / 99))


def test_report_macro_micro_slices(tmp_path):
    rows, meta, build = _build(tmp_path)
    build["index_ids"] = 7
    path = tmp_path / "silver_v2b.csv"
    v2b.ms.write_csv(path, v2b.ms.GOLDEN_HEADER, rows)
    res = evaluate(load_golden(path), BM25Retriever([(r[0], r[1]) for r in CATALOG]),
                   {r[0] for r in CATALOG}, silver=True)
    meta_d = [dict(zip(v2b.META_HEADER, map(str, m))) for m in meta]
    out = rep.report(res, meta_d, build)
    head = out["slices"]["headline"]["L0"]
    assert head["pairs"] == 1 and head["queries"] == 1        # phone pair only; reversed is apart
    assert out["slices"]["reversed"]["L0"]["pairs"] == 1
    assert out["slices"]["original_seller_headed"]["L0"]["pairs"] == 1
    assert out["slices"]["original_pure_mismatch"]["L0"]["pairs"] == 0
    # macro averages pairs; micro averages queries
    per_pair = {p["pair_id"]: p for p in out["per_pair"]}
    zero = v2b.pair_id("ماشین حساب", "حساب گر")
    assert per_pair[zero]["status"] == "zero_coverage"
    assert "zero coverage" in rep.markdown(out)


def test_src_and_eval_do_not_import_v2b_tools():
    pat = re.compile(r"^\s*(?:import|from)\s+(?:tools\.)?(?:make_silver_v2b|silver_v2b_report)\b",
                     re.MULTILINE)
    offenders = [str(p) for d in ("src", "eval") for p in (REPO / d).rglob("*.py")
                 if pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_prefix_class_rule_keeps_accessories_out(tmp_path):
    rows, meta, report = _build(tmp_path)                     # default rule
    phone = v2b.pair_id("تلفن همراه", "گوشی")
    l0 = _by(rows, meta)[(phone, "L0", "گوشی")]
    assert "2900000000005" not in l0[1].split(";")          # قاب تلفن همراه: not at head start
    assert not any(q == "قاب گوشی" for q, *_ in rows)       # nor a source row
    assert report["params"]["class_rule"] == "prefix"
    assert v2b.assign(["قاب", "تلفن", "همراه"], [["تلفن", "همراه"]], "prefix") is None
    assert v2b.assign(["قاب", "تلفن", "همراه"], [["تلفن", "همراه"]], "contains") == ["تلفن", "همراه"]
