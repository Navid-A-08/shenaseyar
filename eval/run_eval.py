"""Evaluate a retriever against the hand-labeled golden set.

Usage: python eval/run_eval.py --retriever random --catalog <catalog.csv|catalog.zip>

A retriever is any object with search(text, k) -> list of sstids, best first.
Every golden row lands in exactly one bucket, and every bucket is reported:
  evaluated            counted in the metrics
  missing_from_catalog expected_sstid not in the catalog: reported, not counted as a miss
  quarantined_only     expected_sstid exists only in quarantined rows: reported, not a miss
  invalid              empty query_text or expected_sstid not 13 digits

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
from eval.metrics import mrr, rank_of, recall_at_k  # noqa: E402

GOLDEN_HEADER = ["query_text", "expected_sstid", "notes"]
PROVISIONAL_BANNER = "PROVISIONAL: catalog may be truncated (1,000,000-row export cap)"
QUARANTINE_NOT_CHECKED = (
    "WARNING quarantined_only: NOT CHECKED. Loader not available: catalog IDs are raw (R1-R4 not"
    " applied), so an ID whose rows are all quarantined is counted as present."
)
LEGEND = [
    "Buckets: evaluated = counted in metrics | missing_from_catalog = expected ID not in catalog"
    " (reported, not a miss) | quarantined_only = expected ID exists only in quarantined rows"
    " (reported, not a miss) | invalid = empty query or ID not 13 digits.",
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
            rec = (rec + ["", "", ""])[:3]
            rows.append({"row": i, "query_text": rec[0], "expected_sstid": rec[1], "notes": rec[2]})
        return rows


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


def evaluate(golden_rows, retriever, catalog_ids, k=20, quarantined_only_ids=None):
    """`quarantined_only_ids=None` means the quarantine check could not run (no loader yet)."""
    if k < 5:
        raise ValueError("k must be at least 5 to report Recall@5")
    checked = quarantined_only_ids is not None
    invalid, missing, quarantined, evaluated, ranks = [], [], [], [], []
    for g in golden_rows:
        sstid = normalize_sstid(g["expected_sstid"])
        if not g["query_text"].strip() or not (len(sstid) == 13 and sstid.isascii() and sstid.isdigit()):
            invalid.append(g["row"])
            continue
        if checked and sstid in quarantined_only_ids:
            quarantined.append(g["row"])
            continue
        if sstid not in catalog_ids:
            missing.append(g["row"])
            continue
        results = list(retriever.search(g["query_text"], k))[:k]
        if not all(isinstance(r, str) for r in results):
            raise TypeError("retriever.search must return a list of sstid strings")
        evaluated.append(g["row"])
        ranks.append(rank_of(sstid, results))
    return {
        "counts": {
            "total": len(golden_rows),
            "evaluated": len(evaluated),
            "missing_from_catalog": len(missing),
            "quarantined_only": len(quarantined) if checked else None,
            "invalid": len(invalid),
        },
        "quarantine_check": "checked" if checked else "not_checked",
        "rows": {
            "missing_from_catalog": missing,
            "quarantined_only": quarantined if checked else None,
            "invalid": invalid,
        },
        "k": k,
        "metrics": {
            "recall_at_1": recall_at_k(ranks, 1),
            "recall_at_5": recall_at_k(ranks, 5),
            f"mrr_at_{k}": mrr(ranks),
        },
    }


def format_report(result, retriever_name):
    def num(x):
        return "n/a" if x is None else f"{x:.4f}"

    c, r = result["counts"], result["rows"]
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
    for key, value in result["metrics"].items():
        lines.append(f"{key}: {num(value)}")
    if r["missing_from_catalog"]:
        lines.append(f"missing_from_catalog rows: {r['missing_from_catalog']}")
    if r["quarantined_only"]:
        lines.append(f"quarantined_only rows: {r['quarantined_only']}")
    if r["invalid"]:
        lines.append(f"invalid rows: {r['invalid']}")
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
