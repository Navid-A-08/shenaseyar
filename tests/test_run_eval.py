"""Harness tests. They build their own golden files in tmp_path and never read eval/golden.csv."""
import csv
import json
import zipfile
from pathlib import Path

import pytest

from eval.run_eval import (PROVISIONAL_BANNER, evaluate, format_report, load_catalog_ids,
                           load_golden, main)

FAKE_CATALOG = Path(__file__).resolve().parents[1] / "data" / "sample" / "fake_catalog.csv"

# Invented 13-digit IDs
A, B, C, D = "2909000000001", "2909000000002", "2909000000003", "2909000000004"
X, Y, Z, W, V = "2909000000011", "2909000000012", "2909000000013", "2909000000014", "2909000000015"
NOT_IN_CATALOG = "2909999999999"
CATALOG = {A, B, C, D, X, Y, Z, W, V}
A_PERSIAN = A.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


class StubRetriever:
    RESULTS = {
        "q1": [A, X],               # rank 1
        "q2": [X, X, Y, B],         # X repeated: B is the 3rd distinct ID -> rank 3
        "q3": [X, Y, Z, W, V, C],   # rank 6: outside top 5
        "q4": [X, Y],               # miss
    }

    def search(self, text, k):
        return self.RESULTS.get(text, [])


def write_golden(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query_text", "expected_sstid", "notes"])
        w.writerows(rows)
    return path


GOLDEN_ROWS = [
    ["q1", A_PERSIAN, "Persian digits must match"],  # row 1
    ["q2", B, ""],                                    # row 2
    ["q3", C, ""],                                    # row 3
    ["q4", D, ""],                                    # row 4
    ["q5", NOT_IN_CATALOG, "valid but missing"],      # row 5
    ["", A, "empty query"],                           # row 6
    ["q7", "123", "not 13 digits"],                   # row 7
]


def test_evaluate_fixture(tmp_path):
    golden = load_golden(write_golden(tmp_path / "g.csv", GOLDEN_ROWS))
    res = evaluate(golden, StubRetriever(), CATALOG, k=20)
    assert res["counts"] == {"total": 7, "evaluated": 4, "missing_from_catalog": 1, "invalid": 2}
    assert res["rows"] == {"missing_from_catalog": [5], "invalid": [6, 7]}
    m = res["metrics"]
    assert m["recall_at_1"] == pytest.approx(0.25)
    assert m["recall_at_5"] == pytest.approx(0.5)
    assert m["mrr_at_20"] == pytest.approx(0.375)


def test_results_beyond_k_are_ignored(tmp_path):
    golden = load_golden(write_golden(tmp_path / "g.csv", [["q3", C, ""]]))
    res = evaluate(golden, StubRetriever(), CATALOG, k=5)  # C sits at rank 6
    assert res["metrics"]["mrr_at_5"] == 0.0


def test_header_only_golden_reports_na(tmp_path):
    golden = load_golden(write_golden(tmp_path / "g.csv", []))
    res = evaluate(golden, StubRetriever(), CATALOG)
    assert res["counts"]["total"] == 0
    assert all(v is None for v in res["metrics"].values())
    report = format_report(res, "stub")
    assert report.splitlines()[0] == PROVISIONAL_BANNER
    assert "recall_at_5: n/a" in report


def test_wrong_header_is_rejected(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("query,sstid\nq1," + A + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden(p)


def test_k_below_5_is_rejected():
    with pytest.raises(ValueError):
        evaluate([], StubRetriever(), CATALOG, k=4)


def test_catalog_ids_from_csv_and_zip_agree(tmp_path):
    from_csv = load_catalog_ids(FAKE_CATALOG)
    zpath = tmp_path / "cat.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(FAKE_CATALOG, "fake_catalog.csv")
    assert load_catalog_ids(zpath) == from_csv
    assert len(from_csv) == 44  # 50 rows, 4 IDs with history


def test_end_to_end_random_retriever(tmp_path, capsys):
    ids = sorted(load_catalog_ids(FAKE_CATALOG))
    golden = write_golden(tmp_path / "g.csv", [
        ["نمونه یک", ids[0], ""],
        ["نمونه دو", ids[1], ""],
        ["نمونه سه", NOT_IN_CATALOG, ""],
    ])
    out = tmp_path / "r.json"
    args = ["--retriever", "random", "--catalog", str(FAKE_CATALOG), "--golden", str(golden),
            "--seed", "7", "--out", str(out)]
    first = main(args)
    second = main(args)
    assert first == second  # deterministic for a fixed seed
    assert first["counts"] == {"total": 3, "evaluated": 2, "missing_from_catalog": 1, "invalid": 0}
    assert capsys.readouterr().out.startswith(PROVISIONAL_BANNER)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["counts"] == first["counts"] and saved["retriever"] == "random"
