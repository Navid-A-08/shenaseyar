"""Report for SILVER-v2b: recall per detail level, per pair, and per slice.

Joins a run_eval.py --out JSON (per-row ranks) with eval/silver_v2b_meta.csv and the build JSON.
No retrieval is re-run.

Slices (each reported per level L0-L4):
  headline                 original-direction pairs
  original, seller-headed  the catalog also heads rows with the seller term (lexical match possible)
  original, pure mismatch  the catalog heads no row with the seller term
  reversed                 direction=reversed pairs: a valid synonym test, NOT a realistic seller query
Numbers per slice and level:
  macro  mean over pairs of each pair's mean hit rate (each pair counts once): the headline number
  micro  mean over queries (shown alongside)
  random expected hit rate of a uniform random retriever, given each label's size (chance level)

Usage: python tools/silver_v2b_report.py --results eval/results/silver_v2b_bm25.json
                                         [--out eval/results/silver_v2b_bm25_report.json]
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_silver_v2b import LEVEL_DESC, LEVELS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SLICES = ("headline", "original_seller_headed", "original_pure_mismatch", "reversed")


def random_hit(label_size, n_ids, k):
    """P(a uniform random draw of k distinct IDs out of n_ids hits a label of label_size IDs)."""
    miss = 1.0
    for i in range(k):
        miss *= max(n_ids - label_size - i, 0) / (n_ids - i)
    return 1.0 - miss


def _mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def slice_of(pair):
    if pair["direction"] == "reversed":
        return ["reversed"]
    return ["headline", "original_seller_headed" if pair["seller_head_rows"] > 0 else "original_pure_mismatch"]


def report(results, meta_rows, build):
    ranks = {int(r): v for r, v in results["row_ranks"].items()}
    pairs = {p["pair_id"]: p for p in build["pair_details"]}
    n_ids = build["index_ids"]
    # per pair, per level: list of (hit@1, hit@5, random@1, random@5)
    per = defaultdict(lambda: defaultdict(list))
    for m in meta_rows:
        row = int(m["row"])
        if row not in ranks:
            continue                      # not evaluated (reported by the harness)
        r, size = ranks[row], int(m["n_label"])
        per[m["pair_id"]][m["level"]].append((
            r is not None and r <= 1, r is not None and r <= 5,
            random_hit(size, n_ids, 1), random_hit(size, n_ids, 5)))

    slices = {}
    for sl in SLICES:
        slices[sl] = {}
        for lv in LEVELS:
            pair_means, queries = [], []
            for pid, levels in per.items():
                if sl not in slice_of(pairs[pid]) or not levels.get(lv):
                    continue
                q = levels[lv]
                queries += q
                pair_means.append([sum(x[i] for x in q) / len(q) for i in range(4)])
            slices[sl][lv] = {
                "pairs": len(pair_means), "queries": len(queries),
                "macro_recall_at_1": _mean([p[0] for p in pair_means]),
                "macro_recall_at_5": _mean([p[1] for p in pair_means]),
                "micro_recall_at_1": _mean([q[0] for q in queries]),
                "micro_recall_at_5": _mean([q[1] for q in queries]),
                "random_macro_at_5": _mean([p[3] for p in pair_means]),
            }
    per_pair = []
    for pid, p in pairs.items():
        entry = {"pair_id": pid, "catalog_term": p["catalog_term"], "seller_term": p["seller_term"],
                 "direction": p["direction"], "class_rows": p["class_rows"],
                 "seller_head_rows": p["seller_head_rows"],
                 "status": "zero_coverage" if p["class_rows"] == 0 else "ok", "levels": {}}
        for lv in LEVELS:
            q = per.get(pid, {}).get(lv, [])
            entry["levels"][lv] = {"queries": len(q), "recall_at_5": _mean([x[1] for x in q])}
        per_pair.append(entry)
    return {"levels": LEVEL_DESC, "index_ids": n_ids, "slices": slices, "per_pair": per_pair}


def _f(x):
    return "n/a" if x is None else f"{x:.2f}"


def markdown(rep):
    out = []
    names = {"headline": "Headline: original-direction pairs",
             "original_seller_headed": "Original pairs, seller term also heads catalog rows",
             "original_pure_mismatch": "Original pairs, pure vocabulary mismatch",
             "reversed": "Reversed pairs (unrealistic direction; own slice)"}
    for sl in SLICES:
        out += [f"**{names[sl]}**", "",
                "| Level | Pairs | Queries | R@5 macro | R@5 micro | R@1 macro | random R@5 |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        for lv in LEVELS:
            s = rep["slices"][sl][lv]
            out.append(f"| {lv} {rep['levels'][lv]} | {s['pairs']} | {s['queries']} | "
                       f"{_f(s['macro_recall_at_5'])} | {_f(s['micro_recall_at_5'])} | "
                       f"{_f(s['macro_recall_at_1'])} | {_f(s['random_macro_at_5'])} |")
        out.append("")
    out += ["**Per pair: R@5 per level (queries)**", "",
            "| pair | dir | catalog term → seller term | seller-headed rows | " + " | ".join(LEVELS) + " |",
            "|---|---|---|---:|" + "---:|" * len(LEVELS)]
    for p in rep["per_pair"]:
        if p["status"] == "zero_coverage":
            cells = ["zero coverage"] + [""] * (len(LEVELS) - 1)
        else:
            cells = [f"{_f(p['levels'][lv]['recall_at_5'])} ({p['levels'][lv]['queries']})" for lv in LEVELS]
        out.append(f"| {p['pair_id']} | {p['direction'][:3]} | {p['catalog_term']} → {p['seller_term']} | "
                   f"{p['seller_head_rows']:,} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="SILVER-v2b report per level, pair and slice.")
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_v2b_meta.csv")
    p.add_argument("--build", type=Path, default=REPO / "eval" / "results" / "silver_v2b_build.json")
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with open(a.meta, encoding="utf-8", newline="") as f:
        meta = list(csv.DictReader(f))
    rep = report(json.loads(a.results.read_text(encoding="utf-8")), meta,
                 json.loads(a.build.read_text(encoding="utf-8")))
    if a.out:
        a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(markdown(rep))
    return rep


if __name__ == "__main__":
    main()
