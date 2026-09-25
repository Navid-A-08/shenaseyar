"""Evaluate a retriever against the hand-labeled golden set.

Usage: python eval/run_eval.py --retriever random --catalog <catalog.csv|catalog.zip>

A retriever is any object with search(text, k) -> list of sstids, best first.
Golden header: query_text,expected_sstid,tier,notes
  tier S  specific: exactly one correct ID
  tier C  class: one or more acceptable IDs, separated by ";". A hit is ANY of them in the top k.
Metrics are reported for tier S (the headline), tier C (a lower bound) and combined.
The Phase 1 exit criterion gates on tier S only, and only with >= EXIT_MIN_TIER_S rows evaluated.

Every golden row lands in exactly one bucket, and every bucket is reported:
  evaluated            counted in the metrics
  missing_from_catalog none of its IDs is in the catalog: reported, not counted as a miss
  quarantined_only     none usable, and all of them exist only in quarantined rows: reported, not a miss
  invalid              with a reason: empty_query, bad_tier, bad_id, duplicate_id, multi_id_tier_s
A row with some (not all) IDs missing or quarantined is scored on the usable IDs, and the unusable
IDs are listed per row (partially_missing / partially_quarantined) so typos can be found.

TODO(loader): the catalog check runs on raw IDs (R1-R4 not applied), so quarantined_only
is NOT CHECKED and such IDs are counted as present. Once the loader exists, pass its
active-index IDs as `catalog_ids` and its quarantined-only IDs as `quarantined_only_ids`.
Until then the report says NOT CHECKED; it never prints 0 for a check that did not run.
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

# Run as `python eval/run_eval.py`, the repo root is not on sys.path; add it so `eval.*` imports work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.dummy_retriever import RandomRetriever  # noqa: E402
from eval.metrics import mrr, rank_of_any, recall_at_k  # noqa: E402

GOLDEN_HEADER = ["query_text", "expected_sstid", "tier", "notes"]
TIERS = ("S", "C")
EXIT_MIN_TIER_S = 60
EXIT_RECALL_AT_5 = 0.85
PROVISIONAL_BANNER = "PROVISIONAL: catalog may be truncated (1,000,000-row export cap)"
QUARANTINE_NOT_CHECKED = (
    "WARNING quarantined_only: NOT CHECKED. Loader not available: catalog IDs are raw (R1-R4 not"
    " applied), so an ID whose rows are all quarantined is counted as present."
)
TIER_C_LOWER_BOUND = (
    "Tier C numbers are a LOWER BOUND: the acceptable-ID set is hand-made and cannot be complete,"
    " so a correct ID outside the set counts as a miss."
)
LEGEND = [
    "Buckets: evaluated = counted in metrics | missing_from_catalog = none of the row's IDs in catalog"
    " (reported, not a miss) | quarantined_only = row's IDs exist only in quarantined rows"
    " (reported, not a miss) | invalid = see the reason per row.",
    "partially_missing / partially_quarantined: the row IS scored, on its usable IDs only; the"
    " listed IDs were ignored. Check them for typos.",
    "not_in_force / ambiguous: CANNOT OCCUR in this report. Golden rows have no invoice date, so"
    " rate_at is never called. Their absence is NOT a passing result.",
]
# Temporary: replace with src/text/normalize.py once it exists.
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

RETRIEVERS = {
    "random": lambda catalog_ids, seed: RandomRetriever(catalog_ids, seed),
}


def normalize_sstid(value):
    return value.strip().translate(_DIGITS)


def load_golden(path):
    """Return a list of dicts with 1-based `row` (data row, header excluded)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != GOLDEN_HEADER:
            raise ValueError(f"{path}: header must be {','.join(GOLDEN_HEADER)}, got {header}")
        rows = []
        for i, rec in enumerate(reader, start=1):
            rec = (rec + [""] * 4)[:4]
            rows.append({"row": i, "query_text": rec[0], "expected_sstid": rec[1],
                         "tier": rec[2], "notes": rec[3]})
        return rows


def parse_row(g):
    """Return (tier, ids, None) for a valid row, or (None, None, reason) for an invalid one."""
    if not g["query_text"].strip():
        return None, None, "empty_query"
    tier = g["tier"].strip()
    if tier not in TIERS:
        return None, None, "bad_tier"
    ids = [normalize_sstid(x) for x in g["expected_sstid"].split(";")]
    if not all(len(i) == 13 and i.isascii() and i.isdigit() for i in ids):
        return None, None, "bad_id"
    if len(set(ids)) != len(ids):
        return None, None, "duplicate_id"
    if tier == "S" and len(ids) > 1:
        return None, None, "multi_id_tier_s"
    return tier, ids, None


def load_catalog_ids(path):
    """Stream only the ID column from a catalog CSV, or from the first CSV inside a zip.

    Not the loader: data-quality rules R1-R4 are not applied, so an ID whose rows are all
    quarantined still counts as present.
    """
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(name) as fh:
                return _read_ids(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return _read_ids(fh)


def _read_ids(text_stream):
    reader = csv.reader(text_stream)
    header = next(reader)
    col = header.index("ID")
    return {row[col].strip() for row in reader if len(row) > col}


def _metrics(ranks, k):
    return {"evaluated": len(ranks), "recall_at_1": recall_at_k(ranks, 1),
            "recall_at_5": recall_at_k(ranks, 5), f"mrr_at_{k}": mrr(ranks)}


def exit_criterion(tier_s):
    n, r5 = tier_s["evaluated"], tier_s["recall_at_5"]
    if n < EXIT_MIN_TIER_S:
        status = "not_yet_measurable"
    else:
        status = "met" if r5 >= EXIT_RECALL_AT_5 else "not_met"
    return {"tier": "S", "metric": "recall_at_5", "threshold": EXIT_RECALL_AT_5,
            "min_rows": EXIT_MIN_TIER_S, "evaluated": n, "status": status}


def evaluate(golden_rows, retriever, catalog_ids, k=20, quarantined_only_ids=None):
    """`quarantined_only_ids=None` means the quarantine check could not run (no loader yet)."""
    if k < 5:
        raise ValueError("k must be at least 5 to report Recall@5")
    checked = quarantined_only_ids is not None
    quarantined_ids = quarantined_only_ids or set()
    invalid, missing, quarantined, part_missing, part_quarantined = {}, [], [], {}, {}
    ranks = {t: [] for t in TIERS}
    for g in golden_rows:
        tier, ids, reason = parse_row(g)
        if reason:
            invalid[g["row"]] = reason
            continue
        q = [i for i in ids if i in quarantined_ids]
        gone = [i for i in ids if i not in quarantined_ids and i not in catalog_ids]
        usable = [i for i in ids if i not in q and i not in gone]
        if not usable:
            (quarantined if not gone else missing).append(g["row"])
            continue
        if gone:
            part_missing[g["row"]] = gone
        if q:
            part_quarantined[g["row"]] = q
        results = list(retriever.search(g["query_text"], k))[:k]
        if not all(isinstance(r, str) for r in results):
            raise TypeError("retriever.search must return a list of sstid strings")
        ranks[tier].append(rank_of_any(usable, results))
    metrics = {"tier_S": _metrics(ranks["S"], k), "tier_C": _metrics(ranks["C"], k),
               "combined": _metrics(ranks["S"] + ranks["C"], k)}
    return {
        "counts": {
            "total": len(golden_rows),
            "evaluated": metrics["combined"]["evaluated"],
            "missing_from_catalog": len(missing),
            "quarantined_only": len(quarantined) if checked else None,
            "invalid": len(invalid),
        },
        "quarantine_check": "checked" if checked else "not_checked",
        "rows": {
            "missing_from_catalog": missing,
            "quarantined_only": quarantined if checked else None,
            "invalid": invalid,
            "partially_missing": part_missing,
            "partially_quarantined": part_quarantined if checked else None,
        },
        "k": k,
        "metrics": metrics,
        "exit_criterion": exit_criterion(metrics["tier_S"]),
    }


def format_report(result, retriever_name):
    def num(x):
        return "n/a" if x is None else f"{x:.4f}"

    def per_row(d):
        return "; ".join(f"row {row}: {', '.join(v) if isinstance(v, list) else v}"
                         for row, v in d.items())

    c, r, m = result["counts"], result["rows"], result["metrics"]
    quarantined = "NOT CHECKED" if c["quarantined_only"] is None else c["quarantined_only"]
    lines = [PROVISIONAL_BANNER]
    if c["quarantined_only"] is None:
        lines.append(QUARANTINE_NOT_CHECKED)
    lines += [
        f"retriever: {retriever_name}   k: {result['k']}",
        f"golden rows: {c['total']}   evaluated: {c['evaluated']}   "
        f"missing_from_catalog: {c['missing_from_catalog']}   quarantined_only: {quarantined}   "
        f"invalid: {c['invalid']}",
    ]
    for key, label in (("tier_S", "tier S (headline)"), ("tier_C", "tier C (LOWER BOUND)"),
                       ("combined", "combined")):
        vals = "   ".join(f"{name}: {num(v)}" for name, v in m[key].items() if name != "evaluated")
        lines.append(f"{label:<21} evaluated: {m[key]['evaluated']}   {vals}")
    lines.append(TIER_C_LOWER_BOUND)

    e = result["exit_criterion"]
    rule = (f"Phase 1 exit (tier S recall_at_5 >= {e['threshold']}, needs >= {e['min_rows']} "
            f"evaluated tier S rows):")
    if e["status"] == "not_yet_measurable":
        lines.append(f"{rule} NOT YET MEASURABLE ({e['evaluated']} tier S rows evaluated)")
    else:
        lines.append(f"{rule} {e['status'].upper().replace('_', ' ')} "
                     f"({num(m['tier_S']['recall_at_5'])} on {e['evaluated']} rows; provisional)")

    if r["missing_from_catalog"]:
        lines.append(f"missing_from_catalog rows: {r['missing_from_catalog']}")
    if r["quarantined_only"]:
        lines.append(f"quarantined_only rows: {r['quarantined_only']}")
    if r["partially_missing"]:
        lines.append(f"partially_missing (scored; these IDs ignored): {per_row(r['partially_missing'])}")
    if r["partially_quarantined"]:
        lines.append(f"partially_quarantined (scored; these IDs ignored): "
                     f"{per_row(r['partially_quarantined'])}")
    if r["invalid"]:
        lines.append(f"invalid rows: {per_row(r['invalid'])}")
    lines += LEGEND
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--retriever", required=True, choices=sorted(RETRIEVERS))
    p.add_argument("--catalog", required=True, help="catalog CSV, or the downloaded zip")
    p.add_argument("--golden", default=str(Path(__file__).with_name("golden.csv")))
    p.add_argument("--k", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", help="optional JSON file for the same report")
    a = p.parse_args(argv)

    # TODO(loader): pass the active index as catalog_ids and quarantined_only_ids; see module docstring.
    catalog_ids = load_catalog_ids(a.catalog)
    retriever = RETRIEVERS[a.retriever](catalog_ids, a.seed)
    result = evaluate(load_golden(a.golden), retriever, catalog_ids, a.k)
    print(format_report(result, a.retriever))
    if a.out:
        payload = {"provisional": PROVISIONAL_BANNER, "retriever": a.retriever, "seed": a.seed,
                   "golden": a.golden, "catalog": a.catalog, **result}
        Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    main()
