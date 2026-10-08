"""app.py runs top to bottom against a recording stub of `streamlit` (Streamlit itself is not
installed in the test venv), for a picked line and a manual line. Plus src/demo/view.py helpers."""
import runpy
import sys
import types
from pathlib import Path

import pytest

from src.demo.pipeline import Checker
from src.demo.view import LABELS_FA, alternative_rows, md_escape
from src.detect.engine import InvoiceLine

APP = Path(__file__).resolve().parents[1] / "app.py"


class _Stop(Exception):
    pass


def _stub(mode, pick=0, manual=None, profile="fake"):
    calls = []
    st = types.ModuleType("streamlit")

    def rec(name, ret=None):
        def f(*a, **k):
            calls.append((name, a, k))
            return ret
        return f

    for name in ["set_page_config", "markdown", "title", "caption", "warning", "info", "subheader",
                 "code", "error", "table", "write", "metric"]:
        setattr(st, name, rec(name))
    st.cache_resource = lambda fn: fn
    def radio(label, options, **k):
        calls.append(("radio", (label, list(options)), k))
        options = list(options)
        return profile if profile in options else mode if mode in options else options[0]
    st.radio = radio
    st.selectbox = rec("selectbox", pick)
    st.text_input = lambda label, value="": (manual or {}).get(label, value)
    st.columns = lambda n: [st] * n

    class _Exp:
        def __enter__(self):
            return st

        def __exit__(self, *a):
            return False
    st.expander = lambda *a, **k: _Exp()

    def stop():
        raise _Stop
    st.stop = stop
    return st, calls


def _run(monkeypatch, st):
    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.delenv("SHENASEYAR_REAL_CATALOG", raising=False)
    try:
        runpy.run_path(str(APP), run_name="__main__")
    except _Stop:
        return "stopped"
    return "done"


def _texts(calls, name):
    return [str(a[0]) for n, a, _ in calls if n == name and a]


def test_picked_line_renders_everything(monkeypatch):
    # L001 in the committed file; any line must render the four sections
    st, calls = _stub("انتخاب از ردیف‌های نمونه", pick=0)
    assert _run(monkeypatch, st) == "done"
    names = {n for n, _, _ in calls}
    assert {"metric", "subheader", "code", "set_page_config"} <= names
    assert any("direction: rtl" in t for t in _texts(calls, "markdown"))
    assert len([1 for n, *_ in calls if n == "subheader"]) == 4


def test_manual_t2_line_shows_explanation_and_citation(monkeypatch):
    values = {"issue_date": "1405-02-01", "sstid": "2909206651897",
              "sstt": "برنج فرضی <script>x</script>", "am": "1", "fee": "1000", "vra": "9",
              "vam": "90"}
    st, calls = _stub("ورود دستی", manual={LABELS_FA[k]: v for k, v in values.items()})
    assert _run(monkeypatch, st) == "done"
    md = _texts(calls, "markdown")
    assert any(t.startswith("**T2**") for t in md)
    assert any("TODO\\(legal\\)" in t or "TODO(legal)" in t for t in md)
    assert any("بازه اعتبار مستند" in t for t in _texts(calls, "caption"))
    assert not any("<script>" in t for t in md + _texts(calls, "write"))   # only in st.code
    assert any("<script>" in t for t in _texts(calls, "code"))


def test_hard_flag_is_shown_first_and_bypasses_the_score(monkeypatch):
    values = {"issue_date": "1405-02-01", "sstid": "2999999999999", "sstt": "برنج فرضی",
              "am": "1", "fee": "1000", "vra": "9", "vam": "90"}
    st, calls = _stub("ورود دستی", manual={LABELS_FA[k]: v for k, v in values.items()})
    assert _run(monkeypatch, st) == "done"
    names = [n for n, *_ in calls]
    first_error = names.index("error")
    assert first_error < names.index("subheader") < names.index("metric")
    assert md_escape("NOT_IN_CATALOG") in _texts(calls, "error")[0]
    metrics = [a for n, a, _ in calls if n == "metric"]
    assert metrics[0][1] == "محاسبه نشد" and metrics[1][1] == "بله"


def test_real_profile_does_not_run_t2_or_show_rates(monkeypatch):
    from src.demo.pipeline import FAKE_CATALOG
    monkeypatch.setenv("SHENASEYAR_REAL_CATALOG", str(FAKE_CATALOG))   # stand-in, no real data
    values = {"issue_date": "1405-02-01", "sstid": "2909206651897", "sstt": "برنج فرضی",
              "am": "1", "fee": "1000", "vra": "9", "vam": "90"}
    st, calls = _stub("ورود دستی", manual={LABELS_FA[k]: v for k, v in values.items()},
                      profile="real")
    monkeypatch.setitem(sys.modules, "streamlit", st)
    runpy.run_path(str(APP), run_name="__main__")
    assert not any(t.startswith("**T2**") for t in _texts(calls, "markdown"))
    assert any("T2" in t and "اجرا نشد" in t for t in _texts(calls, "info"))
    tables = [a[0] for n, a, _ in calls if n == "table" and a and a[0] and "رتبه" in a[0][0]]
    assert tables and all("نرخ" not in row for row in tables[0])
    assert not any("نرخ ثبت‌شده" in t for t in _texts(calls, "markdown"))


def test_invalid_manual_line_stops_with_an_error(monkeypatch):
    st, calls = _stub("ورود دستی", manual={LABELS_FA["issue_date"]: "bad"})
    assert _run(monkeypatch, st) == "stopped"
    assert _texts(calls, "error")


def test_md_escape_neutralizes_markup():
    assert md_escape("<b>*x*</b> [a](b)") == r"\<b\>\*x\*\</b\> \[a\]\(b\)"


def test_alternative_rows_mark_the_declared_id():
    c = Checker.from_paths()
    r = c.check(InvoiceLine.parse("1405-02-01", "2729744617259", "پیچ نمونه", "1", "1000", "10",
                                  "100"))
    rows = alternative_rows(r)
    assert rows[0]["شناسه"] == "2729744617259" and rows[0]["اعلام‌شده"] == "✓"
    assert len(rows) <= 5


# ---- demo-page fixes (2026-10-08) ---------------------------------------------------------------

def test_default_sample_line_is_a_caught_injected_error():
    from src.demo.pipeline import DEMO_INVOICES
    from src.demo.view import default_pick, read_demo_rows
    c = Checker.from_paths()
    rows = read_demo_rows(DEMO_INVOICES)
    i = default_pick(rows, c)
    r = rows[i]
    assert r["labels"].startswith("T")
    line = InvoiceLine.parse(*(r[f] for f in ("issue_date", "sstid", "sstt", "am", "fee", "vra",
                                              "vam")))
    assert r["labels"] in c.check(line).codes


def test_app_opens_on_the_default_pick_and_keeps_labels_in_the_dropdown(monkeypatch):
    st, calls = _stub("انتخاب از ردیف‌های نمونه", pick=0)
    assert _run(monkeypatch, st) == "done"
    (_, args, kw), = [c for c in calls if c[0] == "selectbox"]
    from src.demo.pipeline import DEMO_INVOICES
    from src.demo.view import default_pick, read_demo_rows
    assert kw["index"] == default_pick(read_demo_rows(DEMO_INVOICES), Checker.from_paths())
    assert "[" in kw["format_func"](kw["index"])            # ground-truth label still shown
    assert "بدون خطای تزریقی" not in kw["format_func"](kw["index"])


def _line(sstid, sstt, vra="10", vam="100"):
    return InvoiceLine.parse("1405-02-01", sstid, sstt, "1", "1000", vra, vam)


def test_floored_score_is_labeled_with_the_raw_score_next_to_it():
    from src.demo.view import score_texts
    r = Checker.from_paths().check(_line("2909206651897", "خدمات آموزشی آزمایشی"))   # T1 fires
    assert r.codes == ["T1"]
    value, note = score_texts(r)
    assert value == "0.50 (کف بررسی)" and f"{r.raw_score:.2f}" in note


def test_unfloored_and_hard_flagged_scores():
    from src.demo.view import score_texts
    c = Checker.from_paths()
    assert score_texts(c.check(_line("2909206651897", "برنج فرضی")))[1] is None
    assert score_texts(c.check(_line("2999999999999", "برنج فرضی")))[0] == "محاسبه نشد"


def test_score_breakdown_is_never_an_empty_table():
    from src.demo.view import contribution_rows
    c = Checker.from_paths()
    clean = contribution_rows(c.check(_line("2909206651897", "برنج فرضی")))
    names = [r["جزء"] for r in clean]
    assert len(clean) == 5 and names[-2:] == ["امتیاز خام (جمع اجزا)", "امتیاز نهایی"]
    t1 = contribution_rows(c.check(_line("2909206651897", "خدمات آموزشی آزمایشی")))
    assert {"جزء": "قاعده T1 (وزن دستی)", "سهم": "0.000"} in t1   # weight 0 is shown, not hidden
    assert t1[-1] == {"جزء": "امتیاز نهایی", "سهم": "0.500"}
    assert contribution_rows(c.check(_line("2999999999999", "برنج فرضی"))) == []


def test_app_shows_floor_metric_and_breakdown(monkeypatch):
    values = {"issue_date": "1405-02-01", "sstid": "2909206651897", "sstt": "خدمات آموزشی آزمایشی",
              "am": "1", "fee": "1000", "vra": "10", "vam": "100"}
    st, calls = _stub("ورود دستی", manual={LABELS_FA[k]: v for k, v in values.items()})
    assert _run(monkeypatch, st) == "done"
    metrics = {a[0]: a[1] for n, a, _ in calls if n == "metric"}
    assert metrics["امتیاز ریسک (0 تا 1، وزن‌دهی دستی)"] == "0.50 (کف بررسی)"
    assert "امتیاز خام (پیش از کف بررسی)" in metrics
    tables = [a[0] for n, a, _ in calls if n == "table" and a and a[0] and "جزء" in a[0][0]]
    assert tables and len(tables[0]) >= 4


def test_tied_alternatives_are_marked():
    from src.demo.view import tie_groups
    assert tie_groups([1.54, 1.54, 1.54, 0.9, 0.9, 0.5]) == [[1, 2, 3]] * 3 + [[4, 5]] * 2 + [[]]
    assert tie_groups([3.0, 2.0]) == [[], []]
    r = Checker.from_paths().check(_line("2909206651897", "خدمات آموزشی آزمایشی"))
    rows = alternative_rows(r)
    scores = [row["امتیاز BM25"] for row in rows]
    for row in rows:
        tied = scores.count(row["امتیاز BM25"]) > 1
        assert bool(row["هم‌امتیاز"]) is tied
