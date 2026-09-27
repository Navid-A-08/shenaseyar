"""tools/make_silver_v2.py and tools/silver_v2_per_pair.py on an invented catalog and pair list."""
import csv
import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest

from eval.run_eval import evaluate, is_silver, load_golden, parse_row
from src.retrieval.bm25 import BM25Retriever, tokenize
from src.retrieval.catalog import HEADER

REPO = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v2 = _load("make_silver_v2")
pp = _load("silver_v2_per_pair")

DOM = "شناسه اختصاصی تولید داخل"
AS_OF = "1405-07-01"


def _row(id_, title, run="1404-01-01"):
    return [id_, title, "10", "مشمول", run, "", "1404-01-01", "1404-01-01", DOM, ""]


CATALOG = [
    _row("2900000000001", "تلفن همراه، هوشمند، برند الف، مدل X1، سازنده فرضی، ایران"),
    _row("2900000000002", "تلفن همراه، هوشمند، برند ب، مدل Y2، سازنده فرضی، ایران"),
    _row("2900000000003", "تلفن همراه، ساده، برند ج، سازنده فرضی، ایران"),
    _row("2900000000004", "گوشی، هوشمند، برند الف، مدل X1، سازنده دیگر، چین"),  # seller-term head
    _row("2900000000005", "قاب گوشی، سیلیکونی، برند د، سازنده فرضی، ایران"),  # accessory: not in class
    _row("2900000000006", "رایانه لوحی، 10 اینچ، برند ه، سازنده فرضی، ایران"),
    _row("2900000000007", "رایانه، رومیزی، برند و، سازنده فرضی، ایران"),
    _row("2900000000008", "تلفن همراه، دوگانه، برند ز"),                     # two rows in force:
    _row("2900000000008", "تلفن همراه، دوگانه، برند ز", run="1404-02-01"),   # never sampled
]
PAIRS_CSV = """# invented pairs for tests
catalog_term,seller_term,note
تلفن همراه,گوشی,
رایانه,کامپیوتر,
رایانه لوحی,تبلت,
ماشین حساب,حساب گر,zero coverage
"""


def _files(tmp_path):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(CATALOG)
    z = tmp_path / "catalog.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("catalog.csv", ("﻿" + buf.getvalue()).encode("utf-8"))
    pairs = tmp_path / "pairs.csv"
    pairs.write_text(PAIRS_CSV, encoding="utf-8")
    return z, pairs


def _build(tmp_path, **kw):
    z, pairs = _files(tmp_path)
    params = {"seed": 1, "per_pair": 10, "as_of": AS_OF, "max_matches": 20, **kw}
    return v2.build(z, v2.load_pairs(pairs), **params)


# --- pair list -----------------------------------------------------------------------------

def test_load_pairs_skips_comments(tmp_path):
    _, pairs = _files(tmp_path)
    assert v2.load_pairs(pairs)[0] == ("تلفن همراه", "گوشی")
    assert len(v2.load_pairs(pairs)) == 4


@pytest.mark.parametrize("body,msg", [
    ("catalog_term,seller_term\nكيك,کیک\n", "identical after normalize"),
    ("catalog_term,seller_term\nالف,ب\nالف,ب\n", "duplicate"),
    ("catalog_term,seller_term\nالف,\n", "empty"),
    ("a,b\nالف,ب\n", "header"),
])
def test_load_pairs_rejects_bad_lists(tmp_path, body, msg):
    p = tmp_path / "p.csv"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(v2.SynonymError, match=msg):
        v2.load_pairs(p)


def test_assign_prefers_longest_term_and_substitute():
    terms = [tokenize("رایانه"), tokenize("رایانه لوحی")]
    assert v2.assign(tokenize("رایانه لوحی 10"), terms) == tokenize("رایانه لوحی")
    assert v2.assign(tokenize("رایانه رومیزی"), terms) == tokenize("رایانه")
    assert v2.assign(tokenize("لوحی"), terms) is None
    assert v2.substitute(tokenize("تلفن همراه هوشمند"), tokenize("تلفن همراه"), tokenize("گوشی")) == \
        ["گوشی", "هوشمند"]


# --- build ---------------------------------------------------------------------------------

def test_main_rows_replace_head_and_keep_labels(tmp_path):
    main_rows, _, meta, report = _build(tmp_path)
    for q, ids, tier, notes in main_rows:
        assert re.fullmatch(r"silver-v2\|pair:\d+\|[a-z0-9_+]+\+synonym_head\|1", notes)
    phone = [r for r in main_rows if r[3].startswith("silver-v2|pair:0|")]
    assert {r[1].split(";")[0] for r in phone} >= {"2900000000001"}
    assert all("گوشی" in r[0] and "تلفن" not in r[0] for r in phone)
    assert "2900000000008" not in {i for r in main_rows for i in r[1].split(";")}  # two rows in force
    # "گوشی الف هوشمند مدل x1" also matches row 4 (same brand and model, catalog says گوشی):
    # a genuine second answer, so it joins the acceptable set and the row becomes tier C
    row1 = next(r for r in phone if "2900000000001" in r[1])
    assert set(row1[1].split(";")) == {"2900000000001", "2900000000004"} and row1[2] == "C"


def test_zero_coverage_pair_is_listed_not_dropped(tmp_path):
    _, head_rows, _, report = _build(tmp_path)
    assert report["zero_coverage_pairs"] == [3]
    assert report["coverage"][3]["catalog_term_rows"] == 0
    assert not any("pair:3|" in r[3] for r in head_rows)


def test_head_only_class_labels(tmp_path):
    _, head_rows, _, _ = _build(tmp_path)
    by_pair = {int(r[3].split("pair:")[1].split("|")[0]): r for r in head_rows}
    q, ids, tier, _ = by_pair[0]
    assert q == "گوشی" and tier == "C"
    # class (heads assigned to تلفن همراه, incl. the 2-row ID) + heads starting with گوشی; not قاب گوشی
    assert set(ids.split(";")) == {"2900000000001", "2900000000002", "2900000000003",
                                   "2900000000004", "2900000000008"}
    assert set(by_pair[2][1].split(";")) == {"2900000000006"}      # رایانه لوحی only
    assert set(by_pair[1][1].split(";")) == {"2900000000007"}      # رایانه, not رایانه لوحی
    assert by_pair[1][2] == "S"


def test_deterministic(tmp_path):
    assert _build(tmp_path) == _build(tmp_path)


def test_outputs_are_silver_and_valid_for_the_harness(tmp_path):
    main_rows, head_rows, meta, _ = _build(tmp_path)
    for name, rows in (("silver_v2.csv", main_rows), ("silver_v2_head.csv", head_rows)):
        path = tmp_path / name
        v2.ms.write_csv(path, v2.ms.GOLDEN_HEADER, rows)
        g = load_golden(path)
        assert all(parse_row(r)[2] is None for r in g)
        assert is_silver(path, g)


def test_main_writes_files_and_refuses_golden(tmp_path):
    z, pairs = _files(tmp_path)
    out = [tmp_path / "silver_v2.csv", tmp_path / "silver_v2_head.csv", tmp_path / "m.csv",
           tmp_path / "b.json"]
    args = ["--zip", str(z), "--pairs", str(pairs), "--out", str(out[0]), "--out-head", str(out[1]),
            "--meta", str(out[2]), "--report", str(out[3]), "--seed", "1"]
    v2.main(args)
    first = [p.read_bytes() for p in out[:3]]
    v2.main(args)
    assert [p.read_bytes() for p in out[:3]] == first
    assert '"pairs_sha256"' in out[3].read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        v2.main(["--zip", str(z), "--pairs", str(pairs), "--out", str(tmp_path / "golden.csv")])


# --- per-pair report -----------------------------------------------------------------------

def test_per_pair_joins_ranks_and_lists_every_pair(tmp_path):
    main_rows, head_rows, meta, report = _build(tmp_path)
    z, pairs_path = _files(tmp_path)
    docs = [(r[0], r[1]) for r in CATALOG]
    bm25 = BM25Retriever(docs)
    ids = {r[0] for r in CATALOG}
    results = {}
    for name, rows in (("silver_v2.csv", main_rows), ("silver_v2_head.csv", head_rows)):
        path = tmp_path / name
        v2.ms.write_csv(path, v2.ms.GOLDEN_HEADER, rows)
        results[name] = evaluate(load_golden(path), bm25, ids, silver=True)
    meta_dicts = [dict(zip(v2.META_HEADER, map(str, m))) for m in meta]
    out = pp.per_pair(v2.load_pairs(pairs_path), meta_dicts, results["silver_v2.csv"],
                      results["silver_v2_head.csv"], report)
    assert [r["pair"] for r in out] == [0, 1, 2, 3]
    assert out[3]["status"] == "zero_coverage" and out[3]["head_only_rank"] is None
    assert out[0]["main_rows"] == sum("pair:0|" in r[3] for r in main_rows)
    assert out[0]["head_only_hit_at_5"] is True     # "گوشی" finds row 4, whose head starts with it
    assert "| 3 | ماشین حساب → حساب گر | 0 |" in pp.markdown(out)


def test_src_and_eval_do_not_import_v2_tools():
    pat = re.compile(r"^\s*(?:import|from)\s+(?:tools\.)?(?:make_silver_v2|silver_v2_per_pair)\b"
                     r"|^\s*from\s+tools\s+import\s+[^#\n]*\b(?:make_silver_v2|silver_v2_per_pair)\b",
                     re.MULTILINE)
    offenders = [str(p) for d in ("src", "eval") for p in (REPO / d).rglob("*.py")
                 if pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_query_tokens_that_v1_tokens_would_not_split_still_match(tmp_path):
    # "-" and آ in the HEAD (v2 re-tokenizes the head): v1's tokens() keeps "آبی-سبز" whole and
    # does not fold آ, so matching must use the tokenizer that built the query, or the source row
    # fails to match its own query (this produced empty acceptable sets once).
    rows = CATALOG + [_row("2900000000009", "تلفن همراه آبی-سبز، برند ح، سازنده فرضی")]
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    z = tmp_path / "catalog.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("catalog.csv", ("﻿" + buf.getvalue()).encode("utf-8"))
    pairs = tmp_path / "pairs.csv"
    pairs.write_text(PAIRS_CSV, encoding="utf-8")
    main_rows, _, _, _ = v2.build(z, v2.load_pairs(pairs), seed=1, per_pair=10, as_of=AS_OF,
                                  max_matches=20)
    row = next(r for r in main_rows if "2900000000009" in r[1].split(";"))
    assert all(len(i) == 13 for i in row[1].split(";"))
    assert row[2] == "S"
