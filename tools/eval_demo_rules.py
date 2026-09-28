"""Per-finding precision / recall of the demo pipeline on the synthetic lines.

    python tools/eval_demo_rules.py [--profile fake|real] [--invoices PATH] [--catalog PATH]
                                    [--out PATH]

Profiles (src/demo/pipeline.py): `fake` (default) runs every rule on the committed lines and
writes eval/results/demo_rules.json; `real` runs everything but T2 on the lines generated from the
real catalog (tools/make_demo_invoices.py --profile real) and writes demo_rules_real.json.
A rule the profile does not run is listed under "not_run", never scored as 0 or 1.

Pre-registered bar (CLAUDE.md, 2026-09-28): T2 and T6 precision AND recall = 1.00, on the fake
profile. Anything less is a bug. T1, T3 and T4 have no bar. NOT_IN_CATALOG / NOT_IN_FORCE are
reported next to them, and the number of lines of each ID-status kind that ALSO got T2 is
reported (must be 0).

precision = TP / (TP + FP), recall = TP / (TP + FN), per code, over all lines; None if undefined.
fp_by_label: for each code, its false positives split by the line's true label ("clean" = none).
Hard flags: lines carrying NOT_IN_CATALOG / NOT_IN_FORCE / QUARANTINED_ONLY are never scored; the
score table covers the other lines only, by true label.
Labels come from tools/make_demo_invoices.py (circular for T1/T3/T4, see its docstring).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.demo.pipeline import Checker, profile  # noqa: E402
from src.detect.engine import InvoiceLine  # noqa: E402
from src.detect.score import HARD_FLAGS, HIGH_RISK  # noqa: E402

CODES = ["T1", "T2", "T3", "T4", "T6", "NOT_IN_CATALOG", "NOT_IN_FORCE"]
BARRED = {"T2": 1.0, "T6": 1.0}
OUT = {"fake": ROOT / "eval" / "results" / "demo_rules.json",
       "real": ROOT / "eval" / "results" / "demo_rules_real.json"}


def read_lines(path):
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            line = InvoiceLine.parse(row["issue_date"], row["sstid"], row["sstt"], row["am"],
                                     row["fee"], row["vra"], row["vam"], row["mu"])
            labels = set(filter(None, row["labels"].split(";")))
            yield row["line_id"], line, labels


def _ratio(a, b):
    return None if b == 0 else round(a / b, 4)


def _stats(xs):
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 4), "min": round(min(xs), 4),
            "max": round(max(xs), 4), "high_risk": sum(x >= HIGH_RISK for x in xs)}


def evaluate(checker, rows):
    codes = [c for c in CODES if c not in checker.skipped]
    per = {c: {"tp": 0, "fp": 0, "fn": 0, "fp_lines": [], "fn_lines": [], "fp_by_label": {}}
           for c in codes}
    by_label, flagged_by_label = {}, {}
    status_with_t2 = scored_hard_flags = 0
    for line_id, line, labels in rows:
        res = checker.check(line)
        pred = set(res.codes)
        truth = ";".join(sorted(labels)) or "clean"
        for c in codes:
            if c in pred and c in labels:
                per[c]["tp"] += 1
            elif c in pred:
                per[c]["fp"] += 1
                per[c]["fp_lines"].append(line_id)
                per[c]["fp_by_label"][truth] = per[c]["fp_by_label"].get(truth, 0) + 1
            elif c in labels:
                per[c]["fn"] += 1
                per[c]["fn_lines"].append(line_id)
        if "T2" in pred and pred & (HARD_FLAGS | {"AMBIGUOUS"}):
            status_with_t2 += 1
        if res.hard_flags:
            flagged_by_label[truth] = flagged_by_label.get(truth, 0) + 1
            scored_hard_flags += res.score is not None
        else:
            by_label.setdefault(truth, []).append(res.score)
    out = {}
    for c, m in per.items():
        p, r = _ratio(m["tp"], m["tp"] + m["fp"]), _ratio(m["tp"], m["tp"] + m["fn"])
        out[c] = {"precision": p, "recall": r, "support": m["tp"] + m["fn"], **m}
        if c in BARRED and checker.profile.name == "fake":   # the bar is on the fake lines
            out[c]["bar"] = BARRED[c]
            out[c]["meets_bar"] = p == BARRED[c] and r == BARRED[c]
    n = sum(len(v) for v in by_label.values()) + sum(flagged_by_label.values())
    return {"per_code": out, "not_run": list(checker.skipped),
            "id_status_lines_with_t2": status_with_t2,
            "hard_flagged_by_label": dict(sorted(flagged_by_label.items())),
            "hard_flagged_lines_with_a_score": scored_hard_flags,
            "score_by_label": {k: _stats(v) for k, v in sorted(by_label.items())},
            "n_lines": n}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", choices=sorted(OUT), default="fake")
    ap.add_argument("--invoices")
    ap.add_argument("--catalog")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    prof = profile(a.profile, a.catalog)
    invoices = Path(a.invoices) if a.invoices else prof.invoices
    out = Path(a.out) if a.out else OUT[a.profile]
    res = evaluate(Checker.from_profile(prof), read_lines(invoices))
    res["profile"] = prof.name
    res["invoices"] = invoices.name
    res["catalog"] = prof.path.name
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{res['n_lines']} lines ({res['invoices']}, {prof.name} profile, catalog {res['catalog']})")
    print(f"{'code':<16}{'P':>8}{'R':>8}{'support':>9}{'FP':>5}{'FN':>5}  bar   FP by label")
    for c, m in res["per_code"].items():
        bar = ("PASS" if m["meets_bar"] else "FAIL") if "bar" in m else "-"
        print(f"{c:<16}{str(m['precision']):>8}{str(m['recall']):>8}{m['support']:>9}"
              f"{m['fp']:>5}{m['fn']:>5}  {bar:<5} {m['fp_by_label'] or ''}")
    if res["not_run"]:
        print(f"not run under this profile: {', '.join(res['not_run'])}")
    print(f"ID-status lines that also got T2: {res['id_status_lines_with_t2']} (must be 0)")
    print(f"hard-flagged lines by label: {res['hard_flagged_by_label']} "
          f"(with a score: {res['hard_flagged_lines_with_a_score']}, must be 0)")
    for k, s in res["score_by_label"].items():
        print(f"score {k:<8} {s}")
    return res


if __name__ == "__main__":
    main()
