"""Per-finding precision / recall of the demo pipeline on the synthetic lines.

    python tools/eval_demo_rules.py [--invoices data/sample/demo_invoices.csv]
                                    [--catalog data/sample/fake_catalog.csv]
                                    [--out eval/results/demo_rules.json]

Pre-registered bar (CLAUDE.md, 2026-09-28): T2 and T6 precision AND recall = 1.00. Anything less
is a bug. T3 and T4 have no bar. NOT_IN_CATALOG / NOT_IN_FORCE are reported next to them, and the
number of lines of each ID-status kind that ALSO got T2 is reported (must be 0).

precision = TP / (TP + FP), recall = TP / (TP + FN), per code, over all lines; None if undefined.
Labels come from tools/make_demo_invoices.py (circular for T3/T4, see its docstring).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.demo.pipeline import DEMO_INVOICES, FAKE_CATALOG, Checker  # noqa: E402
from src.detect.engine import InvoiceLine  # noqa: E402

CODES = ["T2", "T3", "T4", "T6", "NOT_IN_CATALOG", "NOT_IN_FORCE"]
BARRED = {"T2": 1.0, "T6": 1.0}


def read_lines(path):
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            line = InvoiceLine.parse(row["issue_date"], row["sstid"], row["sstt"], row["am"],
                                     row["fee"], row["vra"], row["vam"], row["mu"])
            labels = set(filter(None, row["labels"].split(";")))
            yield row["line_id"], line, labels


def _ratio(a, b):
    return None if b == 0 else round(a / b, 4)


def evaluate(checker, rows):
    per = {c: {"tp": 0, "fp": 0, "fn": 0, "fp_lines": [], "fn_lines": []} for c in CODES}
    scores = {"flagged": [], "clean": []}
    status_with_t2 = 0
    for line_id, line, labels in rows:
        res = checker.check(line)
        pred = set(res.codes)
        for c in CODES:
            if c in pred and c in labels:
                per[c]["tp"] += 1
            elif c in pred:
                per[c]["fp"] += 1
                per[c]["fp_lines"].append(line_id)
            elif c in labels:
                per[c]["fn"] += 1
                per[c]["fn_lines"].append(line_id)
        if "T2" in pred and pred & {"NOT_IN_CATALOG", "NOT_IN_FORCE", "AMBIGUOUS",
                                     "QUARANTINED_ONLY"}:
            status_with_t2 += 1
        scores["flagged" if labels else "clean"].append(res.score)
    out = {}
    for c, m in per.items():
        p, r = _ratio(m["tp"], m["tp"] + m["fp"]), _ratio(m["tp"], m["tp"] + m["fn"])
        out[c] = {"precision": p, "recall": r, "support": m["tp"] + m["fn"], **m}
        if c in BARRED:
            out[c]["bar"] = BARRED[c]
            out[c]["meets_bar"] = p == BARRED[c] and r == BARRED[c]
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    return {"per_code": out, "id_status_lines_with_t2": status_with_t2,
            "mean_score": {k: mean(v) for k, v in scores.items()},
            "n_lines": len(scores["flagged"]) + len(scores["clean"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--invoices", default=str(DEMO_INVOICES))
    ap.add_argument("--catalog", default=str(FAKE_CATALOG))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results" / "demo_rules.json"))
    a = ap.parse_args(argv)
    res = evaluate(Checker.from_paths(a.catalog), read_lines(a.invoices))
    res["invoices"] = Path(a.invoices).name
    res["catalog"] = Path(a.catalog).name
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{res['n_lines']} lines ({res['invoices']}, catalog {res['catalog']})")
    print(f"{'code':<16}{'P':>8}{'R':>8}{'support':>9}{'FP':>5}{'FN':>5}  bar")
    for c, m in res["per_code"].items():
        bar = ("PASS" if m["meets_bar"] else "FAIL") if "bar" in m else "-"
        print(f"{c:<16}{str(m['precision']):>8}{str(m['recall']):>8}{m['support']:>9}"
              f"{m['fp']:>5}{m['fn']:>5}  {bar}")
    print(f"ID-status lines that also got T2: {res['id_status_lines_with_t2']} (must be 0)")
    print(f"mean score: labeled {res['mean_score']['flagged']}, clean {res['mean_score']['clean']}")
    return res


if __name__ == "__main__":
    main()
