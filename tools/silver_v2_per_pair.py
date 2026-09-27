"""Per-pair recall for SILVER-v2, from run_eval.py --out JSONs and eval/silver_v2_meta.csv.

Joins the harness's per-row ranks with the meta file (row -> synonym pair), so no retrieval is
re-run. Every pair in the synonym list appears in the output, including zero-coverage pairs.
Terms come from the committed synonym list; no catalog text is written.

Usage:
  python tools/silver_v2_per_pair.py --main eval/results/silver_v2_bm25.json \
      --head eval/results/silver_v2_head_bm25.json [--out eval/results/silver_v2_bm25_per_pair.json]
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_silver_v2 as v2  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _recall(ranks, k):
    return round(sum(1 for r in ranks if r is not None and r <= k) / len(ranks), 4) if ranks else None


def per_pair(pairs, meta_rows, main_result, head_result, build_report):
    main_ranks = {int(r): v for r, v in main_result["row_ranks"].items()}
    head_ranks = {int(r): v for r, v in head_result["row_ranks"].items()}
    by_pair = defaultdict(lambda: {"main": [], "head": None})
    for m in meta_rows:
        row, p = int(m["row"]), int(m["pair"])
        if m["file"] == "silver_v2" and row in main_ranks:
            by_pair[p]["main"].append((m["tier"], main_ranks[row]))
        elif m["file"] == "silver_v2_head" and row in head_ranks:
            by_pair[p]["head"] = (head_ranks[row], int(m["n_acceptable"]))
    cov = {c["pair"]: c for c in build_report["coverage"]}
    out = []
    for p, (cat, sel) in enumerate(pairs):
        d = by_pair.get(p, {"main": [], "head": None})
        ranks = [r for _, r in d["main"]]
        s_ranks = [r for t, r in d["main"] if t == "S"]
        head = d["head"]
        out.append({
            "pair": p, "catalog_term": cat, "seller_term": sel,
            "coverage_rows": cov[p]["catalog_term_rows"], "seller_head_rows": cov[p]["seller_head_rows"],
            "main_rows": len(ranks), "main_S": len(s_ranks),
            "main_recall_at_1": _recall(ranks, 1), "main_recall_at_5": _recall(ranks, 5),
            "main_S_recall_at_5": _recall(s_ranks, 5),
            "head_only_rank": head[0] if head else None,
            "head_only_hit_at_5": (head[0] is not None and head[0] <= 5) if head else None,
            "head_only_acceptable_ids": head[1] if head else None,
            "status": "zero_coverage" if cov[p]["catalog_term_rows"] == 0 else
                      ("no_main_rows" if not ranks else "ok"),
        })
    return out


def markdown(rows):
    def f(x):
        return "n/a" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
    lines = ["| # | catalog term → seller term | coverage (rows) | seller-headed rows | main rows (S) "
             "| main R@5 | head-only rank | status |",
             "|---:|---|---:|---:|---|---:|---:|---|"]
    for r in rows:
        head = "n/a" if r["head_only_acceptable_ids"] is None else (
            "miss" if r["head_only_rank"] is None else str(r["head_only_rank"]))
        lines.append(f"| {r['pair']} | {r['catalog_term']} → {r['seller_term']} | {r['coverage_rows']:,} | "
                     f"{r['seller_head_rows']:,} | {r['main_rows']} ({r['main_S']}) | "
                     f"{f(r['main_recall_at_5'])} | {head} | {r['status']} |")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Per-pair recall for SILVER-v2.")
    p.add_argument("--main", type=Path, required=True)
    p.add_argument("--head", type=Path, required=True)
    p.add_argument("--pairs", type=Path, default=REPO / "eval" / "synonyms" / "head_synonyms.csv")
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_v2_meta.csv")
    p.add_argument("--build", type=Path, default=REPO / "eval" / "results" / "silver_v2_build.json")
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(a.meta, encoding="utf-8", newline="") as f:
        meta = list(csv.DictReader(f))
    rows = per_pair(v2.load_pairs(a.pairs), meta, json.loads(a.main.read_text(encoding="utf-8")),
                    json.loads(a.head.read_text(encoding="utf-8")),
                    json.loads(a.build.read_text(encoding="utf-8")))
    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(markdown(rows))
    return rows


if __name__ == "__main__":
    main()
