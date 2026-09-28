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


def _stub(mode, pick=0, manual=None):
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
    st.radio = rec("radio", mode)
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
    monkeypatch.delenv("SHENASEYAR_CATALOG", raising=False)
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
