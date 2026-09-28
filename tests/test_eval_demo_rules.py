"""tools/eval_demo_rules.py: metric arithmetic, and the pre-registered T2/T6 bar on the committed
synthetic lines (a regression guard: T2 and T6 must stay at precision = recall = 1.0)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import eval_demo_rules as ev  # noqa: E402
from src.demo.pipeline import Checker  # noqa: E402
from src.detect.engine import InvoiceLine  # noqa: E402


def test_bar_on_committed_lines(tmp_path):
    out = tmp_path / "r.json"
    res = ev.main(["--out", str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["n_lines"] == 200
    for code in ("T2", "T6"):
        m = res["per_code"][code]
        assert (m["precision"], m["recall"], m["meets_bar"]) == (1.0, 1.0, True), m
        assert m["support"] == 10
    assert res["id_status_lines_with_t2"] == 0


def test_metric_arithmetic():
    checker = Checker.from_paths()
    L = lambda vra, vam: InvoiceLine.parse("1405-02-01", "2909206651897", "برنج فرضی", "1",  # noqa
                                           "1000", vra, vam)
    rows = [("a", L("10", "100"), set()),          # TN
            ("b", L("9", "90"), {"T2"}),            # TP
            ("c", L("10", "100"), {"T2"}),          # FN
            ("d", L("9", "90"), set())]             # FP
    m = ev.evaluate(checker, rows)["per_code"]["T2"]
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert (m["precision"], m["recall"], m["meets_bar"]) == (0.5, 0.5, False)
    assert (m["fp_lines"], m["fn_lines"]) == (["d"], ["c"])


def test_undefined_precision_is_none():
    m = ev.evaluate(Checker.from_paths(), [])["per_code"]["T3"]
    assert m["precision"] is None and m["recall"] is None
