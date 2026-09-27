"""Why are the silver set's tier C queries ambiguous? Rebuilds the table in docs/silver_set.md.

For each tier C row of the silver set, the source title is turned into richer queries and the
same token-subset test as tools/make_silver.py is re-run:
  all_attrs         head + brand + every attribute derive() keeps (no manufacturer, no part number)
  all_attrs+mfr     ... + the manufacturer segment(s)
  all_attrs+partno  ... + the part-number segment
Each row lands in one category: unique_with_all_attrs, needs_mfr_or_partno, unique_under_none.

Analysis only: nothing in src/ or eval/ may import this file. Output is counts and IDs, no text.

Usage:
  python tools/analyze_ambiguity.py [--zip PATH] [--meta eval/silver_meta.csv]
                                    [--out eval/results/ambiguity.json]
Deterministic: same zip, same silver_meta.csv -> same JSON.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_search as cs  # noqa: E402
import make_silver as ms  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("all_attrs", "all_attrs+mfr", "all_attrs+partno")
_TRAILING_COMPANY = re.compile(r"/\s*(شرکت[^/]*)$")


def _segments(rec):
    t = " ".join(rec["DescriptionOfID"].split())
    parts = t.split("/") if rec["Type"].endswith("خدمت") else t.split("،")
    return [s.strip() for s in parts if s.strip()]


def detail_queries(rec):
    """Return {variant: query or None}; None when the title has no such segment."""
    head, brand, attrs, _ = ms.derive(rec["DescriptionOfID"], rec["Type"])
    base = " ".join([head] + ([brand] if brand else []) + attrs)
    segs = _segments(rec)
    mfr = " ".join(s for s in segs if s.startswith(ms.MFR_PREFIXES))
    if not mfr:
        m = _TRAILING_COMPANY.search(" ".join(rec["DescriptionOfID"].split()))
        mfr = m.group(1) if m else ""
    part = " ".join(s for s in segs if s.startswith(ms.PARTNO_PREFIXES))
    return {"all_attrs": base,
            "all_attrs+mfr": f"{base} {mfr}" if mfr else None,
            "all_attrs+partno": f"{base} {part}" if part else None}


def classify(unique):
    """unique: {variant: bool}. One category per row."""
    if unique["all_attrs"]:
        return "unique_with_all_attrs"
    if unique["all_attrs+mfr"] or unique["all_attrs+partno"]:
        return "needs_mfr_or_partno"
    return "unique_under_none"


def analyze(zip_path, source_ids, as_of):
    """Return the report dict for the given tier C source IDs."""
    cs.check_date(as_of, "--as-of")
    wanted = set(source_ids)

    def in_force_rows():
        for i, line, rec in cs.iter_rows(zip_path):
            if cs.in_force(rec, as_of, f"row {i} (CSV line {line}, ID {rec['ID']})"):
                yield rec

    recs, bad_ids = {}, set()
    for rec in in_force_rows():
        if rec["ID"] in wanted:
            recs[rec["ID"]] = rec
        if not ms._ID13.fullmatch(rec["ID"]):
            bad_ids.add(rec["ID"])  # same universe as make_silver: 13-digit IDs only
    missing = sorted(wanted - recs.keys())
    variants = {i: detail_queries(recs[i]) for i in source_ids if i in recs}
    queries = {q: ms.tokens(q) for v in variants.values() for q in v.values() if q}
    matches = ms.match_ids(in_force_rows, queries, cap=2, exclude_ids=bad_ids)

    rows, counts = [], Counter()
    for i, v in variants.items():
        unique = {k: bool(q) and len(matches[q]) == 1 for k, q in v.items()}
        category = classify(unique)
        counts[category] += 1
        if category == "needs_mfr_or_partno":
            counts["needs_mfr"] += unique["all_attrs+mfr"] and not unique["all_attrs+partno"]
            counts["needs_partno"] += unique["all_attrs+partno"] and not unique["all_attrs+mfr"]
            counts["needs_either"] += unique["all_attrs+mfr"] and unique["all_attrs+partno"]
        counts["has_mfr"] += v["all_attrs+mfr"] is not None
        counts["has_partno"] += v["all_attrs+partno"] is not None
        rows.append({"source_id": i, "category": category, "unique": unique})
    n = len(rows)
    return {
        "as_of": as_of,
        "tier_C_rows": n,
        "missing_from_catalog": missing,
        "categories": {k: counts[k] for k in ("unique_with_all_attrs", "needs_mfr_or_partno",
                                              "unique_under_none")},
        "shares": {k: round(counts[k] / n, 4) if n else None
                   for k in ("unique_with_all_attrs", "needs_mfr_or_partno", "unique_under_none")},
        "needs_mfr_or_partno_split": {"mfr_only": counts["needs_mfr"],
                                      "partno_only": counts["needs_partno"],
                                      "either": counts["needs_either"]},
        "segments_present": {"mfr": counts["has_mfr"], "partno": counts["has_partno"]},
        "rows": rows,
    }


def tier_c_sources(meta_path):
    with open(meta_path, encoding="utf-8", newline="") as f:
        return [r["source_id"] for r in csv.DictReader(f) if r["tier"] == "C"]


def main(argv=None):
    p = argparse.ArgumentParser(description="Explain tier C ambiguity in the silver set.")
    p.add_argument("--zip", type=Path, help="catalog zip (default: the one zip in data/catalog/)")
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_meta.csv")
    p.add_argument("--as-of", default=ms.DEFAULTS["as_of"])
    p.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "ambiguity.json")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    zip_path = a.zip or cs.default_zip()
    report = analyze(zip_path, tier_c_sources(a.meta), a.as_of)
    report = {"catalog_zip": Path(zip_path).name, "catalog_sha256": ms.sha256(zip_path),
              "silver_meta_sha256": ms.sha256(a.meta), **report}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")
    print(json.dumps({k: report[k] for k in ("tier_C_rows", "categories", "shares",
                                             "needs_mfr_or_partno_split", "segments_present")}))
    return report


if __name__ == "__main__":
    main()
