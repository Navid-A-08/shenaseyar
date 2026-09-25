"""tools/make_silver.py tests. Catalogs are invented and zipped in tmp_path; the real zip is never read."""
import csv
import importlib.util
import io
import random
import re
import zipfile
from pathlib import Path

import pytest

from eval.run_eval import GOLDEN_HEADER, evaluate, format_report, load_golden, parse_row

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_silver", REPO / "tools" / "make_silver.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)
cs = ms.cs

DOM, IMP, SRV = "شناسه اختصاصی تولید داخل", "شناسه اختصاصی وارداتی", "شناسه اختصاصی خدمت"
GEN = "شناسه عمومی تولید داخل"
NOTES = re.compile(r"silver\|rule-derived\|(non-unique\|)?[a-z0-9_]+(\+[a-z0-9_]+)*\|\d+")


def _id(prefix, i):
    return f"{prefix}{i:010d}"


def _row(id_, title, type_=DOM, vat="10", run="1404-01-01", exp=""):
    return [id_, title, vat, "مشمول", run, exp, "1404-01-01", "1404-01-01", type_, ""]


def _zip(tmp_path, rows):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(cs.HEADER)
    w.writerows(rows)
    path = tmp_path / "catalog.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("catalog.csv", ("﻿" + buf.getvalue()).encode("utf-8"))
    return path


def small_catalog():
    """30 notebooks (one query, >20 matches), 5 pencils (one query, 5 matches), 3 unique cups."""
    rows = [_row(_id("290", i), f"دفتر، 100 برگ، سازنده فرضی {i}، ایران") for i in range(30)]
    rows += [_row(_id("291", i), f"مداد، مشکی، سازنده نمونه {i}، ایران") for i in range(5)]
    rows += [_row(_id("292", i), f"لیوان، شیشه ای، برند نمونه{i}، سازنده فرضی، ایران") for i in range(3)]
    return rows


def mixed_catalog():
    rows = []
    for i in range(40):
        rows.append(_row(_id("290", i), f"لیوان فرضی، شیشه ای، برند نمونه{i}، ظرفیت {i} ml، سازنده فرضی، ایران"))
        rows.append(_row(_id("280", i), f"لنت فرضی، سرامیکی، BRAND{i}، سازنده X، چین، شماره فنی Q{i}", IMP,
                         vat="0" if i % 4 else "90"))
    for i in range(12):
        rows.append(_row(_id("233", i), f"خدمات فرضی کد{i}/شرح خدمت نمونه کد{i}/ شرکت فرضی {i}", SRV))
        rows.append(_row(_id("272", i), f"کالای عمومی فرضی گونه{i}", GEN, vat="0"))
    rows.append(_row("2909999999990", "لیوان منقضی، برند منقضی", exp="1404-06-30"))      # expired
    rows.append(_row("2909999999991", "لیوان دوگانه، برند دوگانه"))                       # ambiguous:
    rows.append(_row("2909999999991", "لیوان دوگانه، برند دوگانه", run="1404-02-01"))     # two in force
    rows.append(_row("29099999999920", "لیوان چهارده رقمی، برند چهارده"))                # 14 digits
    return rows


def _build(tmp_path, rows, **kw):
    params = {**ms.DEFAULTS, "as_of": "1405-07-01", **kw}
    return ms.build(_zip(tmp_path, rows), **params)


# --- rules ---------------------------------------------------------------------------------

def test_derive_goods_rules():
    head, brand, attrs, rules = ms.derive(
        "عدس، درشت، نام تجارتی نمونه، سازنده فرضی، ایران، بسته بندی کیسه، شماره فنی 12، "
        "کاغذ 70 g\\m^2، کشور صاحب برند ایران، ساخت ایران", DOM)
    assert (head, brand, attrs) == ("عدس", "نمونه", ["درشت"])
    assert rules == ["drop_commas", "brand", "drop_mfr", "drop_country", "drop_pack",
                     "drop_partno", "drop_paperweight"]


@pytest.mark.parametrize("seg", ["برند فاقد نام تجارتی", "نام تجارتی فاقد نام تجارتی", "مدل فاقد مدل",
                                 "فاقد نام تجارتی", "فاقد برند"])
def test_derive_drops_placeholders(seg):
    head, brand, attrs, rules = ms.derive(f"نوار چسب، {seg}، عرض 20 mm", DOM)
    assert brand is None and attrs == ["عرض 20 mm"] and "drop_placeholder" in rules


def test_derive_keeps_real_faqed_attribute():
    assert ms.derive("شارژر، فاقد نشانگر", IMP)[2] == ["فاقد نشانگر"]


def test_derive_latin_brand_only_early():
    assert ms.derive("لنت، سرامیکی، ACME، سازنده ACME، چین", IMP)[1] == "ACME"
    head, brand, attrs, _ = ms.derive("لنت، سرامیکی، عقب، ACME", IMP)
    assert brand is None and attrs == ["سرامیکی", "عقب", "ACME"]


def test_derive_service_and_admin_tail():
    head, brand, attrs, rules = ms.derive(
        "اجرای زنده/اجرای موسیقی نمونه /سازمان امور مالیاتی/اعتبار تا تاریخ 1402/06/31 / شرکت فرضی", SRV)
    assert (head, attrs) == ("اجرای زنده", ["اجرای موسیقی نمونه"])
    assert rules == ["drop_admin"]
    assert ms.derive("خدمات نمونه/شرح/ شرکت فرضی", SRV)[3] == ["drop_mfr"]


def test_derive_goods_trailing_company():
    head, _, attrs, rules = ms.derive("کفش نمونه مدل 65/ شرکت نمونه فرضی 3", DOM)
    assert head == "کفش نمونه مدل 65" and attrs == [] and rules == ["drop_mfr"]


def test_build_query_trims_long_attributes_and_ladder():
    q, rules = ms.build_query("خدمات", None, ["یک دو سه چهار پنج شش"], 1, [])
    assert q == "خدمات یک دو سه چهار" and "trim_attr" in rules
    assert ms.attr_ladder(["a", "b", "c"]) == [1, 2]
    assert ms.attr_ladder(["a"]) == [1] and ms.attr_ladder([]) == [0]


@pytest.mark.parametrize("rule,query,expected", [
    ("noise_arabic_yk", "کیک ایرانی", "كيك ايراني"),
    ("noise_no_zwnj", "می‌کند", "میکند"),
    ("noise_fa_digits", "مدل 65", "مدل ۶۵"),
])
def test_noise_rules(rule, query, expected):
    assert rule in ms.eligible_noise(query)
    assert ms.apply_noise(query, rule, random.Random(0)) == expected


def test_noise_typo_changes_one_word_by_one_letter():
    for seed in range(20):
        out = ms.apply_noise("لیوان شیشه", "noise_typo", random.Random(seed))
        assert out != "لیوان شیشه"
        assert sum(a != b for a, b in zip(out.split(" "), "لیوان شیشه".split(" "))) == 1


# --- allocation ----------------------------------------------------------------------------

def test_allocate_proportional_with_floor():
    q = ms.allocate({"a": 9000, "b": 1000, "c": 3, "d": 0}, 100, 5)
    assert q == {"a": 87, "b": 10, "c": 3}  # c: all its rows (< floor); a 87.3, b 9.7 -> b gets the remainder
    assert sum(q.values()) == 100


def test_allocate_floor_lifts_small_cells():
    q = ms.allocate({"a": 10000, "b": 20}, 100, 5)
    assert q["b"] == 5 and q["a"] == 95


def test_allocate_caps_at_catalog_size_and_rejects_impossible_floor():
    assert ms.allocate({"a": 3, "b": 4}, 100, 5) == {"a": 3, "b": 4}
    with pytest.raises(ValueError):
        ms.allocate({c: 100 for c in "abcdef"}, 20, 5)


# --- build ---------------------------------------------------------------------------------

def test_unique_S_nonunique_C_and_over_max_rejected(tmp_path):
    silver, meta, report = _build(tmp_path, small_catalog(), n=38, floor=1, noise_frac=0.0)
    by_tier = {t: [r for r in silver if r[2] == t] for t in "SC"}
    assert sorted(r[1] for r in by_tier["S"]) == [_id("292", i) for i in range(3)]
    assert len(by_tier["C"]) == 1                      # 5 pencils, one shared query
    assert by_tier["C"][0][1] == ";".join(_id("291", i) for i in range(5))
    assert "|non-unique|" in by_tier["C"][0][3]
    t = report["totals"]
    assert t["rejected_over_max"] == 30                # every notebook: 30 matches > 20
    assert t["rejected_duplicate_query"] == 4          # pencils 2-5 repeat pencil 1's query
    assert (t["tier_S"], t["tier_C"]) == (3, 1)


def test_S_queries_are_unique_by_brute_force(tmp_path):
    rows = mixed_catalog()
    silver, _, _ = _build(tmp_path, rows, n=40, floor=2, noise_frac=0.0)
    in_force = [r for r in rows if not r[5] and len(r[0]) == 13]
    for q, ids, tier, _ in silver:
        hits = {r[0] for r in in_force if ms.tokens(q) <= ms.tokens(r[1])}
        assert hits == set(ids.split(";")), q
        assert (tier == "S") == (len(hits) == 1)


def test_excluded_rows_never_sampled(tmp_path):
    silver, meta, report = _build(tmp_path, mixed_catalog(), n=120, floor=1)
    all_ids = {i for r in silver for i in r[1].split(";")}
    assert not all_ids & {"2909999999990", "2909999999991", "29099999999920"}
    assert report["excluded_ids"] == {"ambiguous_two_in_force": 1, "not_13_digits": 1}


def test_output_format_and_harness_accepts_it(tmp_path):
    silver, meta, report = _build(tmp_path, mixed_catalog(), n=40, floor=2)
    for q, ids, tier, notes in silver:
        assert NOTES.fullmatch(notes), notes
        assert notes.endswith(f"|{ms.DEFAULTS['seed']}")
        assert (tier == "C") == ("|non-unique|" in notes)
    path = tmp_path / "silver.csv"
    ms.write_csv(path, ms.GOLDEN_HEADER, silver)
    rows = load_golden(path)
    assert all(parse_row(g)[2] is None for g in rows)   # no invalid rows
    assert len(meta) == len(silver) and {m[3] for m in meta} <= {"0", "10", "90"}  # Vat recorded


def test_noise_count_is_exact(tmp_path):
    silver, _, report = _build(tmp_path, mixed_catalog(), n=40, floor=2, noise_frac=0.30)
    k = round(0.30 * len(silver))
    assert report["noise"]["rows"] == k
    assert sum(any(n in r[3] for n in ms.NOISE_RULES) for r in silver) == k


def test_deterministic_and_seed_sensitive(tmp_path):
    a = _build(tmp_path, mixed_catalog(), n=40, floor=2)
    b = _build(tmp_path, mixed_catalog(), n=40, floor=2)
    c = _build(tmp_path, mixed_catalog(), n=40, floor=2, seed=1)
    assert a == b
    assert a[0] != c[0]


def test_quota_uses_type_and_length_not_vat(tmp_path):
    _, _, report = _build(tmp_path, mixed_catalog(), n=40, floor=2)
    assert {c["length"] for c in report["cells"]} <= {"short", "long"}
    assert sum(c["quota"] for c in report["cells"]) == 40
    assert "vat" not in report["cells"][0]


def test_main_writes_files_and_refuses_golden(tmp_path, capsys):
    z = _zip(tmp_path, mixed_catalog())
    out, meta, rep = tmp_path / "silver.csv", tmp_path / "meta.csv", tmp_path / "r" / "b.json"
    args = ["--zip", str(z), "--out", str(out), "--meta", str(meta), "--report", str(rep),
            "--n", "40", "--floor", "2"]
    ms.main(args)
    first = out.read_bytes()
    ms.main(args)
    assert out.read_bytes() == first                  # byte-identical rebuild
    assert out.read_text(encoding="utf-8").splitlines()[0] == ",".join(GOLDEN_HEADER)
    assert '"catalog_sha256"' in rep.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        ms.main(["--zip", str(z), "--out", str(tmp_path / "golden.csv")])


def test_silver_rows_evaluate_as_silver(tmp_path):
    silver, _, _ = _build(tmp_path, mixed_catalog(), n=40, floor=2)
    path = tmp_path / "silver.csv"
    ms.write_csv(path, ms.GOLDEN_HEADER, silver)

    class Echo:  # returns the row's own IDs: a perfect retriever
        def __init__(self, rows):
            self.m = {r[0]: r[1].split(";") for r in rows}

        def search(self, text, k):
            return self.m[text]

    ids = {i for r in silver for i in r[1].split(";")}
    res = evaluate(load_golden(path), Echo(silver), ids, silver=True)
    assert res["exit_criterion"]["status"] == "not_applicable"
    assert format_report(res, "echo").startswith("*** SILVER (OPTIMISTIC, MACHINE-GENERATED) ***")


def test_generator_header_matches_harness():
    assert ms.GOLDEN_HEADER == GOLDEN_HEADER


def test_src_and_eval_do_not_import_make_silver():
    pat = re.compile(r"^\s*(?:import\s+(?:tools\.)?make_silver\b|from\s+(?:tools\.)?make_silver\s+import\b"
                     r"|from\s+tools\s+import\s+[^#\n]*\bmake_silver\b)", re.MULTILINE)
    offenders = [str(p) for d in ("src", "eval") if (REPO / d).is_dir()
                 for p in (REPO / d).rglob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
