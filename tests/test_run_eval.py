"""Harness tests. They build their own golden files in tmp_path and never read eval/golden.csv."""
import csv
import json
import zipfile
from pathlib import Path

import pytest

from eval.run_eval import (EXIT_MIN_TIER_S, GOLDEN_HEADER, LEGEND, PROVISIONAL_BANNER,
                           QUARANTINE_NOT_CHECKED, SILVER_BANNER, TIER_C_LOWER_BOUND, evaluate,
                           format_report, is_silver, load_catalog_ids, load_golden, main)

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
        "qc1": [X, B, D],           # {D, B}: B first -> rank 2
        "qc2": [X, Y, Z, W, V, A],  # {A, C}: A at rank 6
        "qc3": [V],                 # {V}: single-ID class row -> rank 1
        "qc4": [W, A],              # {A, NOT_IN_CATALOG}: scored on A -> rank 2
    }

    def search(self, text, k):
        return self.RESULTS.get(text, [])


class ConstantRetriever:
    def __init__(self, results):
        self.results = results

    def search(self, text, k):
        return self.results


def write_golden(path, rows, header=GOLDEN_HEADER):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


GOLDEN_ROWS = [
    ["q1", A_PERSIAN, "S", "Persian digits must match"],  # row 1
    ["q2", B, "S", ""],                                    # row 2
    ["q3", C, "S", ""],                                    # row 3
    ["q4", D, "S", ""],                                    # row 4
    ["qc1", f"{D};{B}", "C", "any ID counts"],             # row 5
    ["qc2", f"{A}; {C}", "C", "space after ; is fine"],    # row 6
    ["qc3", V, "C", "single-ID class row is allowed"],     # row 7
    ["qc4", f"{A};{NOT_IN_CATALOG}", "C", "partly missing"],  # row 8
    ["q5", NOT_IN_CATALOG, "S", "valid but missing"],      # row 9
    ["", A, "S", "empty query"],                           # row 10
    ["q7", "123", "S", "not 13 digits"],                   # row 11
    ["q8", f"{A};{B}", "S", "tier S with two IDs"],        # row 12
    ["q9", A, "s", "lowercase tier"],                      # row 13
    ["q10", f"{A};", "C", "empty piece"],                  # row 14
    ["q11", f"{A};{A}", "C", "duplicate ID"],              # row 15
    ["q12", A, "", "no tier"],                             # row 16
]


def _evaluate(tmp_path, rows=GOLDEN_ROWS, **kw):
    return evaluate(load_golden(write_golden(tmp_path / "g.csv", rows)), StubRetriever(), CATALOG, **kw)


def test_evaluate_fixture_buckets(tmp_path):
    res = _evaluate(tmp_path, k=20)
    assert res["counts"] == {"total": 16, "evaluated": 8, "missing_from_catalog": 1,
                             "quarantined_only": None, "invalid": 7}
    assert res["rows"] == {
        "missing_from_catalog": [9],
        "quarantined_only": None,
        "invalid": {10: "empty_query", 11: "bad_id", 12: "multi_id_tier_s", 13: "bad_tier",
                    14: "bad_id", 15: "duplicate_id", 16: "bad_tier"},
        "partially_missing": {8: [NOT_IN_CATALOG]},
        "partially_quarantined": None,
    }
    assert res["quarantine_check"] == "not_checked"


def test_three_way_metrics(tmp_path):
    m = _evaluate(tmp_path, k=20)["metrics"]
    # tier S ranks: 1, 3, 6, miss
    assert m["tier_S"]["evaluated"] == 4
    assert m["tier_S"]["recall_at_1"] == pytest.approx(1 / 4)
    assert m["tier_S"]["recall_at_5"] == pytest.approx(2 / 4)
    assert m["tier_S"]["mrr_at_20"] == pytest.approx(0.375)
    # tier C ranks: 2, 6, 1, 2
    assert m["tier_C"]["evaluated"] == 4
    assert m["tier_C"]["recall_at_1"] == pytest.approx(1 / 4)
    assert m["tier_C"]["recall_at_5"] == pytest.approx(3 / 4)
    assert m["tier_C"]["mrr_at_20"] == pytest.approx((1 / 2 + 1 / 6 + 1 + 1 / 2) / 4)
    # combined: all eight
    assert m["combined"]["evaluated"] == 8
    assert m["combined"]["recall_at_1"] == pytest.approx(2 / 8)
    assert m["combined"]["recall_at_5"] == pytest.approx(5 / 8)
    assert m["combined"]["mrr_at_20"] == pytest.approx((1 + 1 / 3 + 1 / 6 + 1 / 2 + 1 / 6 + 1 + 1 / 2) / 8)


def test_multi_id_row_hits_on_any_id(tmp_path):
    rows = [["qc1", f"{D};{B}", "C", ""]]
    assert _evaluate(tmp_path, rows)["metrics"]["tier_C"]["recall_at_1"] == 0.0
    assert _evaluate(tmp_path, rows)["metrics"]["tier_C"]["recall_at_5"] == 1.0
    # the same IDs as tier S are invalid, not scored
    res = _evaluate(tmp_path, [["qc1", f"{D};{B}", "S", ""]])
    assert res["rows"]["invalid"] == {1: "multi_id_tier_s"}
    assert res["metrics"]["tier_S"]["evaluated"] == 0


def test_row_with_all_ids_missing_is_missing_not_scored(tmp_path):
    res = _evaluate(tmp_path, [["qc1", f"{NOT_IN_CATALOG};2909999999998", "C", ""]])
    assert res["rows"]["missing_from_catalog"] == [1]
    assert res["rows"]["partially_missing"] == {}
    assert res["counts"]["evaluated"] == 0


def test_results_beyond_k_are_ignored(tmp_path):
    res = _evaluate(tmp_path, [["q3", C, "S", ""]], k=5)  # C sits at rank 6
    assert res["metrics"]["tier_S"]["mrr_at_5"] == 0.0


def test_header_only_golden_reports_na_for_all_tiers(tmp_path):
    res = _evaluate(tmp_path, [])
    assert res["counts"]["total"] == 0
    for tier in ("tier_S", "tier_C", "combined"):
        assert res["metrics"][tier]["evaluated"] == 0
        assert all(v is None for k, v in res["metrics"][tier].items() if k != "evaluated")
    report = format_report(res, "stub")
    assert report.splitlines()[0] == PROVISIONAL_BANNER
    assert "recall_at_5: 0" not in report
    assert report.count("recall_at_5: n/a") == 3


def test_old_three_column_header_is_rejected(tmp_path):
    p = write_golden(tmp_path / "g.csv", [["q1", A, ""]], header=["query_text", "expected_sstid", "notes"])
    with pytest.raises(ValueError, match="query_text,expected_sstid,tier,notes"):
        load_golden(p)


def test_wrong_header_is_rejected(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("query,sstid\nq1," + A + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden(p)


def test_k_below_5_is_rejected():
    with pytest.raises(ValueError):
        evaluate([], StubRetriever(), CATALOG, k=4)


# --- report --------------------------------------------------------------------------------

def test_report_three_way_order_and_lower_bound_note(tmp_path):
    report = format_report(_evaluate(tmp_path), "stub")
    lines = report.splitlines()
    s = next(i for i, l in enumerate(lines) if l.startswith("tier S (headline)"))
    c = next(i for i, l in enumerate(lines) if l.startswith("tier C (LOWER BOUND)"))
    b = next(i for i, l in enumerate(lines) if l.startswith("combined"))
    assert s < c < b
    assert "evaluated: 4   recall_at_1: 0.2500   recall_at_5: 0.5000   mrr_at_20: 0.3750" in lines[s]
    assert TIER_C_LOWER_BOUND in lines


def test_report_names_partially_missing_ids_and_invalid_reasons(tmp_path):
    report = format_report(_evaluate(tmp_path), "stub")
    assert f"partially_missing (scored; these IDs ignored): row 8: {NOT_IN_CATALOG}" in report
    assert "row 12: multi_id_tier_s" in report
    assert "row 13: bad_tier" in report
    assert "row 15: duplicate_id" in report


def test_quarantine_not_checked_is_loud_not_zero(tmp_path):
    report = format_report(_evaluate(tmp_path), "stub")
    assert QUARANTINE_NOT_CHECKED in report
    assert "quarantined_only: NOT CHECKED" in report
    assert "quarantined_only: 0" not in report


def test_quarantined_only_bucket_when_checked(tmp_path):
    # Future loader path: B's rows are all quarantined.
    res = _evaluate(tmp_path, quarantined_only_ids={B})
    assert res["quarantine_check"] == "checked"
    assert res["counts"] == {"total": 16, "evaluated": 7, "missing_from_catalog": 1,
                             "quarantined_only": 1, "invalid": 7}
    assert res["rows"]["quarantined_only"] == [2]            # S row, only B
    assert res["rows"]["partially_quarantined"] == {5: [B]}  # C row {D, B}: scored on D
    # tier S left: rank 1, rank 6, miss.  Row 5 on D alone: rank 3.
    assert res["metrics"]["tier_S"]["recall_at_5"] == pytest.approx(1 / 3)
    assert res["metrics"]["tier_C"]["mrr_at_20"] == pytest.approx((1 / 3 + 1 / 6 + 1 + 1 / 2) / 4)
    report = format_report(res, "stub")
    assert QUARANTINE_NOT_CHECKED not in report
    assert "quarantined_only: 1" in report
    assert f"partially_quarantined (scored; these IDs ignored): row 5: {B}" in report


def test_report_legend_says_not_in_force_cannot_occur(tmp_path):
    report = format_report(_evaluate(tmp_path, []), "stub")
    for line in LEGEND:
        assert line in report
    assert "not_in_force / ambiguous: CANNOT OCCUR" in report
    assert "NOT a passing result" in report


# --- Phase 1 exit criterion: tier S only, >= 60 rows ---------------------------------------

def _exit(tmp_path, s_rows, c_rows=0, expected=A):
    rows = [[f"s{i}", expected, "S", ""] for i in range(s_rows)]
    rows += [[f"c{i}", A, "C", ""] for i in range(c_rows)]
    res = evaluate(load_golden(write_golden(tmp_path / "g.csv", rows)),
                   ConstantRetriever([A]), CATALOG)
    return res, format_report(res, "const")


def test_exit_not_measurable_below_min_rows_even_if_perfect(tmp_path):
    res, report = _exit(tmp_path, EXIT_MIN_TIER_S - 1)
    assert res["metrics"]["tier_S"]["recall_at_5"] == 1.0
    assert res["exit_criterion"]["status"] == "not_yet_measurable"
    assert f"NOT YET MEASURABLE ({EXIT_MIN_TIER_S - 1} tier S rows evaluated)" in report
    assert " MET " not in report


def test_exit_ignores_tier_c_rows(tmp_path):
    res, report = _exit(tmp_path, EXIT_MIN_TIER_S - 1, c_rows=500)
    assert res["exit_criterion"]["status"] == "not_yet_measurable"
    assert res["exit_criterion"]["evaluated"] == EXIT_MIN_TIER_S - 1


def test_exit_met_and_not_met_at_min_rows(tmp_path):
    res, report = _exit(tmp_path, EXIT_MIN_TIER_S)
    assert res["exit_criterion"]["status"] == "met"
    assert f"MET (1.0000 on {EXIT_MIN_TIER_S} rows; provisional)" in report
    res, report = _exit(tmp_path, EXIT_MIN_TIER_S, expected=B)
    assert res["exit_criterion"]["status"] == "not_met"
    assert f"NOT MET (0.0000 on {EXIT_MIN_TIER_S} rows; provisional)" in report


# --- catalog and CLI -----------------------------------------------------------------------

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
        ["نمونه یک", ids[0], "S", ""],
        ["نمونه دو", f"{ids[1]};{ids[2]}", "C", ""],
        ["نمونه سه", NOT_IN_CATALOG, "S", ""],
    ])
    out = tmp_path / "r.json"
    args = ["--retriever", "random", "--catalog", str(FAKE_CATALOG), "--golden", str(golden),
            "--seed", "7", "--out", str(out)]
    first = main(args)
    second = main(args)
    assert first == second  # deterministic for a fixed seed
    assert first["counts"] == {"total": 3, "evaluated": 2, "missing_from_catalog": 1,
                               "quarantined_only": None, "invalid": 0}
    assert first["metrics"]["tier_S"]["evaluated"] == 1
    assert first["metrics"]["tier_C"]["evaluated"] == 1
    assert capsys.readouterr().out.startswith(PROVISIONAL_BANNER)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["counts"] == first["counts"] and saved["retriever"] == "random"
    assert saved["metrics"] == first["metrics"]
    assert saved["exit_criterion"]["status"] == "not_yet_measurable"


# --- silver set ----------------------------------------------------------------------------

SILVER_NOTE = "silver|rule-derived|head+attr1|20260925"


def test_silver_detected_by_name_or_notes(tmp_path):
    plain = [["q1", A, "S", ""]]
    tagged = [["q1", A, "S", SILVER_NOTE]]
    assert is_silver(write_golden(tmp_path / "silver.csv", plain), load_golden(tmp_path / "silver.csv"))
    p = write_golden(tmp_path / "other.csv", tagged)
    assert is_silver(p, load_golden(p))
    p = write_golden(tmp_path / "golden.csv", plain)
    assert not is_silver(p, load_golden(p))


def test_golden_with_a_silver_row_is_an_error(tmp_path):
    p = write_golden(tmp_path / "golden.csv", [["q1", A, "S", ""], ["q2", B, "S", SILVER_NOTE]])
    with pytest.raises(ValueError, match=r"silver rows \[2\]"):
        is_silver(p, load_golden(p))
    with pytest.raises(ValueError):
        main(["--retriever", "random", "--catalog", str(FAKE_CATALOG), "--golden", str(p)])


def test_silver_exit_criterion_not_applicable_even_when_perfect(tmp_path):
    rows = [[f"s{i}", A, "S", SILVER_NOTE] for i in range(EXIT_MIN_TIER_S + 10)]
    res = evaluate(load_golden(write_golden(tmp_path / "silver.csv", rows)),
                   ConstantRetriever([A]), CATALOG, silver=True)
    assert res["metrics"]["tier_S"]["recall_at_5"] == 1.0
    assert res["exit_criterion"]["status"] == "not_applicable"
    report = format_report(res, "const")
    assert report.splitlines()[0] == SILVER_BANNER
    assert "NOT APPLICABLE: silver set" in report
    assert " MET " not in report and "MET (" not in report


def test_golden_report_has_no_silver_banner(tmp_path):
    report = format_report(_evaluate(tmp_path), "stub")
    assert "SILVER" not in report
    assert report.splitlines()[0] == PROVISIONAL_BANNER


def test_cli_prints_file_and_silver_banner(tmp_path, capsys):
    ids = sorted(load_catalog_ids(FAKE_CATALOG))
    silver = write_golden(tmp_path / "silver.csv", [["نمونه", ids[0], "S", SILVER_NOTE]])
    res = main(["--retriever", "random", "--catalog", str(FAKE_CATALOG), "--golden", str(silver)])
    out = capsys.readouterr().out
    assert out.splitlines()[0] == SILVER_BANNER
    assert f"golden file: {silver}   set: silver" in out
    assert res["set"] == "silver" and res["golden_file"] == str(silver)
