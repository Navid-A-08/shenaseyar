"""Evaluate a retriever against the hand-labeled golden set.

Usage: python eval/run_eval.py --retriever {random,bm25} --catalog <catalog.csv|catalog.zip>
                              [--golden eval/golden.csv | eval/silver.csv] [--as-of 1405-07-01]

--golden picks the file. A SILVER file (named silver.csv, or any notes starting `silver|`) is
machine-generated and optimistic: the report prints a loud banner and the exit criterion reads
NOT APPLICABLE. golden.csv containing a silver row is an error. See docs/silver_set.md.

A retriever is any object with search(text, k) -> list of sstids, best first.
Golden header: query_text,expected_sstid,tier,notes
  tier S  specific: exactly one correct ID
  tier C  class: one or more acceptable IDs, separated by ";". A hit is ANY of them in the top k.
Metrics are reported for tier S (the headline), tier C (a lower bound) and combined.
The Phase 1 exit criterion gates on tier S only, and only with >= EXIT_MIN_TIER_S rows evaluated.

The catalog is loaded as a snapshot (src/retrieval/catalog.py): rules R1-R4 applied, then the
rows in force on --as-of. Retrievers index exactly those rows. Each expected ID is checked
against the same snapshot, so an ID the retriever can never return is reported, not scored.

Every golden row lands in exactly one bucket, and every bucket is reported:
  evaluated            counted in the metrics
  missing_from_catalog none usable, and some ID is not in the catalog file at all
  quarantined_only     none usable, and all of its IDs exist only in quarantined rows (R2-R4)
  not_in_index         none usable, and some ID passes R1-R4 but has no row in force on as_of
  invalid              with a reason: empty_query, bad_tier, bad_id, duplicate_id, multi_id_tier_s
None of the first three is counted as a miss. A row with some (not all) IDs unusable is scored on
the usable IDs, and the others are listed per row (partially_missing / partially_quarantined /
partially_not_in_index) so typos can be found.

evaluate() without `quarantined_only_ids` / `index_ids` reports that check as NOT CHECKED; it
never prints 0 for a check that did not run. Through main(), both checks always run.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

# Run as `python eval/run_eval.py`, the repo root is not on sys.path; add it so `eval.*` imports work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.dummy_retriever import RandomRetriever  # noqa: E402
from eval.metrics import mrr, rank_of_any, recall_at_k  # noqa: E402
from src.retrieval.bm25 import BM25Retriever  # noqa: E402
from src.retrieval.catalog import load_snapshot  # noqa: E402

DEFAULT_AS_OF = "1405-07-01"  # the silver set's snapshot date; golden rows carry no date

GOLDEN_HEADER = ["query_text", "expected_sstid", "tier", "notes"]
TIERS = ("S", "C")
EXIT_MIN_TIER_S = 60
EXIT_RECALL_AT_5 = 0.85
PROVISIONAL_BANNER = "PROVISIONAL: catalog may be truncated (1,000,000-row export cap)"
SILVER_BANNER = (
    "*** SILVER (OPTIMISTIC, MACHINE-GENERATED) ***  Queries are derived from catalog titles, so"
    " scores favour lexical retrieval and are NOT an accuracy estimate. Never gates Phase 1."
    " See docs/silver_set.md."
)
SILVER_NOTE_PREFIX = "silver|"
QUARANTINE_NOT_CHECKED = (
    "WARNING quarantined_only: NOT CHECKED. No snapshot was given: catalog IDs are raw (R1-R4 not"
    " applied), so an ID whose rows are all quarantined is counted as present."
)
INDEX_NOT_CHECKED = (
    "WARNING not_in_index: NOT CHECKED. No index IDs were given, so an expected ID the retriever"
    " cannot return would be scored as a miss."
)
TIER_C_LOWER_BOUND = (
    "Tier C numbers are a LOWER BOUND: the acceptable-ID set (hand-made in golden, rule-made in"
    " silver) cannot be complete, so a correct ID outside the set counts as a miss."
)
LEGEND = [
    "Buckets: evaluated = counted in metrics | missing_from_catalog = an ID not in the catalog file"
    " | quarantined_only = row's IDs exist only in quarantined rows | not_in_index = an ID passes"
    " R1-R4 but has no row in force on as_of, so no retriever over this snapshot can return it."
    " None of these is a miss. invalid = see the reason per row.",
    "partially_missing / partially_quarantined / partially_not_in_index: the row IS scored, on its"
    " usable IDs only; the listed IDs were ignored. Check them for typos.",
    "rate_at statuses NOT_IN_FORCE / AMBIGUOUS: CANNOT OCCUR in this report. Golden rows have no"
    " invoice date, so rate_at is never called. Their absence is NOT a passing result.",
]
# Temporary: replace with src/text/normalize.py once it exists.
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Each factory gets the snapshot and the seed. Retrievers index snapshot.docs / snapshot.index_ids.
RETRIEVERS = {
    "random": lambda snap, seed: RandomRetriever(snap.index_ids, seed),
    "bm25": lambda snap, seed: BM25Retriever(snap.docs),
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


def _metrics(ranks, k):
    return {"evaluated": len(ranks), "recall_at_1": recall_at_k(ranks, 1),
            "recall_at_5": recall_at_k(ranks, 5), f"mrr_at_{k}": mrr(ranks)}


def is_silver(path, golden_rows):
    """A file is silver if it is named silver.csv or any row's notes start with `silver|`.

    Machine rows must never enter the human set: golden.csv with a silver row is an error.
    """
    tagged = [g["row"] for g in golden_rows if g["notes"].startswith(SILVER_NOTE_PREFIX)]
    if Path(path).name == "golden.csv" and tagged:
        raise ValueError(f"{path}: golden set contains machine-generated silver rows {tagged}")
    return Path(path).name == "silver.csv" or bool(tagged)


def exit_criterion(tier_s, silver=False):
    n, r5 = tier_s["evaluated"], tier_s["recall_at_5"]
    if silver:
        status = "not_applicable"
    elif n < EXIT_MIN_TIER_S:
        status = "not_yet_measurable"
    else:
        status = "met" if r5 >= EXIT_RECALL_AT_5 else "not_met"
    return {"tier": "S", "metric": "recall_at_5", "threshold": EXIT_RECALL_AT_5,
            "min_rows": EXIT_MIN_TIER_S, "evaluated": n, "status": status}


def evaluate(golden_rows, retriever, catalog_ids, k=20, quarantined_only_ids=None, silver=False,
             index_ids=None):
    """Score `retriever` on `golden_rows`.

    catalog_ids: every ID in the catalog file. quarantined_only_ids: IDs whose rows all failed
    R2-R4 (None = check not run). index_ids: IDs the retriever can return, i.e. with an active row
    in force on as_of (None = check not run). `silver=True`: the exit criterion does not apply.
    """
    if k < 5:
        raise ValueError("k must be at least 5 to report Recall@5")
    checked = quarantined_only_ids is not None
    index_checked = index_ids is not None
    quarantined_ids = quarantined_only_ids or set()
    invalid, missing, quarantined, not_in_index = {}, [], [], []
    part_missing, part_quarantined, part_not_in_index = {}, {}, {}
    ranks = {t: [] for t in TIERS}
    for g in golden_rows:
        tier, ids, reason = parse_row(g)
        if reason:
            invalid[g["row"]] = reason
            continue
        q = [i for i in ids if i in quarantined_ids]
        gone = [i for i in ids if i not in quarantined_ids and i not in catalog_ids]
        out = [i for i in ids
               if index_checked and i not in q and i not in gone and i not in index_ids]
        usable = [i for i in ids if i not in q and i not in gone and i not in out]
        if not usable:
            if gone:
                missing.append(g["row"])
            elif out:
                not_in_index.append(g["row"])
            else:
                quarantined.append(g["row"])
            continue
        if gone:
            part_missing[g["row"]] = gone
        if q:
            part_quarantined[g["row"]] = q
        if out:
            part_not_in_index[g["row"]] = out
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
            "not_in_index": len(not_in_index) if index_checked else None,
            "invalid": len(invalid),
        },
        "quarantine_check": "checked" if checked else "not_checked",
        "index_check": "checked" if index_checked else "not_checked",
        "rows": {
            "missing_from_catalog": missing,
            "quarantined_only": quarantined if checked else None,
            "not_in_index": not_in_index if index_checked else None,
            "invalid": invalid,
            "partially_missing": part_missing,
            "partially_quarantined": part_quarantined if checked else None,
            "partially_not_in_index": part_not_in_index if index_checked else None,
        },
        "k": k,
        "metrics": metrics,
        "set": "silver" if silver else "golden",
        "exit_criterion": exit_criterion(metrics["tier_S"], silver),
    }


def format_report(result, retriever_name):
    def num(x):
        return "n/a" if x is None else f"{x:.4f}"

    def per_row(d):
        return "; ".join(f"row {row}: {', '.join(v) if isinstance(v, list) else v}"
                         for row, v in d.items())

    c, r, m = result["counts"], result["rows"], result["metrics"]
    quarantined = "NOT CHECKED" if c["quarantined_only"] is None else c["quarantined_only"]
    out = "NOT CHECKED" if c.get("not_in_index") is None else c["not_in_index"]
    silver = result.get("set") == "silver"
    lines = [SILVER_BANNER] if silver else []
    lines.append(PROVISIONAL_BANNER)
    if c["quarantined_only"] is None:
        lines.append(QUARANTINE_NOT_CHECKED)
    if c.get("not_in_index") is None:
        lines.append(INDEX_NOT_CHECKED)
    snap = result.get("catalog_snapshot")
    if snap:
        sc = snap["counts"]
        lines.append(f"catalog snapshot: {snap['describe']}. Measured: {sc['quarantined_only_ids']:,}"
                     f" quarantined-only IDs, {sc['index_ids']:,} IDs in the index"
                     f" ({sc['index_rows']:,} rows).")
    lines += [
        f"golden file: {result.get('golden_file', '(not given)')}   set: {result.get('set', 'golden')}",
        f"retriever: {retriever_name}   k: {result['k']}",
        f"golden rows: {c['total']}   evaluated: {c['evaluated']}   "
        f"missing_from_catalog: {c['missing_from_catalog']}   quarantined_only: {quarantined}   "
        f"not_in_index: {out}   invalid: {c['invalid']}",
    ]
    for key, label in (("tier_S", "tier S (headline)"), ("tier_C", "tier C (LOWER BOUND)"),
                       ("combined", "combined")):
        vals = "   ".join(f"{name}: {num(v)}" for name, v in m[key].items() if name != "evaluated")
        lines.append(f"{label:<21} evaluated: {m[key]['evaluated']}   {vals}")
    lines.append(TIER_C_LOWER_BOUND)

    e = result["exit_criterion"]
    rule = (f"Phase 1 exit (tier S recall_at_5 >= {e['threshold']}, needs >= {e['min_rows']} "
            f"evaluated tier S rows):")
    if e["status"] == "not_applicable":
        lines.append(f"{rule} NOT APPLICABLE: silver set. The criterion is measured on the human"
                     f" golden set only.")
    elif e["status"] == "not_yet_measurable":
        lines.append(f"{rule} NOT YET MEASURABLE ({e['evaluated']} tier S rows evaluated)")
    else:
        lines.append(f"{rule} {e['status'].upper().replace('_', ' ')} "
                     f"({num(m['tier_S']['recall_at_5'])} on {e['evaluated']} rows; provisional)")

    if r["missing_from_catalog"]:
        lines.append(f"missing_from_catalog rows: {r['missing_from_catalog']}")
    if r["quarantined_only"]:
        lines.append(f"quarantined_only rows: {r['quarantined_only']}")
    if r.get("not_in_index"):
        lines.append(f"not_in_index rows: {r['not_in_index']}")
    if r["partially_missing"]:
        lines.append(f"partially_missing (scored; these IDs ignored): {per_row(r['partially_missing'])}")
    if r["partially_quarantined"]:
        lines.append(f"partially_quarantined (scored; these IDs ignored): "
                     f"{per_row(r['partially_quarantined'])}")
    if r.get("partially_not_in_index"):
        lines.append(f"partially_not_in_index (scored; these IDs ignored): "
                     f"{per_row(r['partially_not_in_index'])}")
    rt = result.get("runtime")
    if rt:
        lines.append(f"runtime: snapshot {rt['snapshot_s']:.1f} s, index build {rt['index_build_s']:.1f} s,"
                     f" search {rt['search_ms_per_query']:.1f} ms/query, process peak memory"
                     f" {rt['process_peak_mb']} MB")
    if r["invalid"]:
        lines.append(f"invalid rows: {per_row(r['invalid'])}")
    lines += LEGEND
    return "\n".join(lines)


class TimedRetriever:
    """Wraps a retriever and accumulates wall time spent in search()."""

    def __init__(self, inner):
        self.inner, self.calls, self.seconds = inner, 0, 0.0

    def search(self, text, k):
        t = time.perf_counter()
        try:
            return self.inner.search(text, k)
        finally:
            self.seconds += time.perf_counter() - t
            self.calls += 1


def peak_memory_mb():
    """Peak resident memory of this process in MB, or None if the platform can't say."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return round(pmc.PeakWorkingSetSize / 2**20)
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (2**20 if sys.platform == "darwin" else 2**10))
    except Exception:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--retriever", required=True, choices=sorted(RETRIEVERS))
    p.add_argument("--catalog", required=True, help="catalog CSV, or the downloaded zip")
    p.add_argument("--golden", default=str(Path(__file__).with_name("golden.csv")),
                   help="golden.csv (human) or silver.csv (machine-generated, optimistic)")
    p.add_argument("--as-of", default=DEFAULT_AS_OF,
                   help=f"snapshot: index rows in force on this Jalali date (default {DEFAULT_AS_OF})")
    p.add_argument("--k", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", help="optional JSON file for the same report")
    a = p.parse_args(argv)

    golden_rows = load_golden(a.golden)
    silver = is_silver(a.golden, golden_rows)
    t0 = time.perf_counter()
    snap = load_snapshot(a.catalog, a.as_of)
    t1 = time.perf_counter()
    retriever = TimedRetriever(RETRIEVERS[a.retriever](snap, a.seed))
    t2 = time.perf_counter()
    result = evaluate(golden_rows, retriever, snap.raw_ids, a.k, silver=silver,
                      quarantined_only_ids=snap.quarantined_only_ids, index_ids=snap.index_ids)
    result["golden_file"] = a.golden
    result["catalog_snapshot"] = {"rules": snap.rules, "as_of": snap.as_of,
                                  "describe": snap.describe(), "counts": snap.counts}
    result["runtime"] = {
        "snapshot_s": round(t1 - t0, 2), "index_build_s": round(t2 - t1, 2),
        "queries": retriever.calls, "search_total_s": round(retriever.seconds, 3),
        "search_ms_per_query": round(1000 * retriever.seconds / max(retriever.calls, 1), 2),
        "process_peak_mb": peak_memory_mb()}
    if hasattr(retriever.inner, "stats"):
        result["runtime"]["index"] = retriever.inner.stats()
    print(format_report(result, a.retriever))
    if a.out:
        payload = {"provisional": PROVISIONAL_BANNER, "retriever": a.retriever, "seed": a.seed,
                   "golden": a.golden, "catalog": a.catalog, **result}
        Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


if __name__ == "__main__":
    main()
