"""tools/catalog_search.py tests. Zips are built in tmp_path from the fake catalog or invented rows."""
import csv
import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAKE_CATALOG = REPO / "data" / "sample" / "fake_catalog.csv"

# Load by path: tools/ is deliberately not an importable package.
_spec = importlib.util.spec_from_file_location("catalog_search", REPO / "tools" / "catalog_search.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

AS_OF = "1405-07-03"


def _zip(tmp_path, csv_bytes, name="catalog.csv"):
    path = tmp_path / "catalog.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, csv_bytes)
    return path


def _rows_csv(rows, header=cs.HEADER):
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _row(id_, title, run="1404-01-01", exp=""):
    return [id_, title, "10", "مشمول", run, exp, "1404-01-01", "1404-01-01",
            "شناسه اختصاصی تولید داخل", ""]


@pytest.fixture
def fake_zip(tmp_path):
    return _zip(tmp_path, FAKE_CATALOG.read_bytes())


# --- normalization -------------------------------------------------------------------------

def test_normalize_removes_whitespace_and_zwnj_and_casefolds():
    assert cs.normalize("مي‌كند  ABC\t") == cs.normalize("ميكند abc")


def test_fold_arabic_letters_and_digits():
    assert cs.fold("كيك ۱۲٣") == "کیک 123"
    assert cs.normalize("كيك", literal=True) == "كيك"


# --- title search --------------------------------------------------------------------------

def test_title_search_is_case_and_space_insensitive(fake_zip):
    rows, _, _ = cs.search_titles(fake_zip, "برنجفرضی  دارای", 20, AS_OF)
    assert {r[0] for r in rows} == {"2909206651897"}
    assert len(rows) == 3  # all history rows match


def test_title_search_zwnj_and_arabic_letters(tmp_path):
    z = _zip(tmp_path, _rows_csv([_row("2909000000001", "دستگاه مي‌كند فرضی"),
                                  _row("2909000000002", "کالای دیگر فرضی")]))
    for query in ("میکند", "ميكند", "می کند", "مي‌كند"):
        rows, _, _ = cs.search_titles(z, query, 20, AS_OF)
        assert [r[0] for r in rows] == ["2909000000001"], query
    assert cs.search_titles(z, "میکند", 20, AS_OF, literal=True)[0] == []


def test_title_search_folds_digits_both_sides(tmp_path):
    z = _zip(tmp_path, _rows_csv([_row("2909000000001", "پیچ فرضی مدل ۶۵")]))
    assert len(cs.search_titles(z, "مدل 65", 20, AS_OF)[0]) == 1
    assert len(cs.search_titles(z, "مدل ٦٥", 20, AS_OF)[0]) == 1


def test_limit_stops_early(fake_zip):
    rows, scanned, hit = cs.search_titles(fake_zip, "فرضی", 2, AS_OF)
    assert len(rows) == 2 and hit
    assert scanned < 50


def test_printed_title_is_raw(tmp_path):
    z = _zip(tmp_path, _rows_csv([_row("2909000000001", "كالاي ABC")]))
    rows, _, _ = cs.search_titles(z, "abc", 20, AS_OF)
    assert rows[0][1] == "كالاي ABC"


# --- in_force ------------------------------------------------------------------------------

@pytest.mark.parametrize("run,exp,expected", [
    ("1404-01-01", "", True),               # open-ended
    ("1404-01-01", "1405-07-03", True),     # end date inclusive
    ("1404-01-01", "1405-07-02", False),    # expired
    ("1405-07-04", "", False),              # not started
])
def test_in_force(run, exp, expected):
    assert cs.in_force({"RunDate": run, "ExpirationDate": exp}, AS_OF, "x") is expected


@pytest.mark.parametrize("bad", ["1405-7-03", "1405/07/03", "۱۴۰۵-۰۷-۰۳", "1405-07-03 ", ""])
def test_bad_date_raises_naming_row(tmp_path, bad):
    z = _zip(tmp_path, _rows_csv([_row("2909000000001", "کالای فرضی"),
                                  _row("2909000000002", "کالای فرضی", run=bad)]))
    with pytest.raises(cs.CatalogError, match=r"row 2 .*ID 2909000000002"):
        cs.search_titles(z, "کالا", 20, AS_OF)


def test_bad_as_of_is_an_error(fake_zip, capsys):
    assert cs.main(["برنج", "--zip", str(fake_zip), "--as-of", "1405-7-3"]) == 2
    assert "--as-of" in capsys.readouterr().err


# --- --ids ---------------------------------------------------------------------------------

def test_ids_returns_history_and_any_in_force(fake_zip):
    rows, any_force = cs.lookup_ids(
        fake_zip, ["2909206651897", "2809734954277", "2909999999999"], AS_OF)
    assert sum(r[0] == "2909206651897" for r in rows) == 3
    assert any_force == {"2909206651897": True,   # one open row
                         "2809734954277": False,  # expired 1404-06-30: fully retired
                         "2909999999999": None}   # not found


def test_ids_cli_output(fake_zip, capsys):
    persian = "2809734954277".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert cs.main(["--ids", persian, "2909999999999", "--zip", str(fake_zip),
                    "--as-of", AS_OF]) == 0
    out, err = capsys.readouterr()
    assert "note: folded IDs" in err
    assert "2809734954277\tno" in out
    assert "2909999999999\tNOT FOUND" in out


# --- CLI -----------------------------------------------------------------------------------

def test_cli_title_output_and_fold_note(fake_zip, capsys):
    assert cs.main(["برنج فرضي دارای سابقه", "--zip", str(fake_zip), "--as-of", AS_OF]) == 0
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert lines[0].split("\t") == cs.OUT_COLUMNS
    assert len(lines) == 4
    assert err.count("note: folded query") == 1
    assert "3 match(es)" in err


def test_cli_no_fold_note_when_query_unchanged(fake_zip, capsys):
    cs.main(["برنج", "--zip", str(fake_zip), "--as-of", AS_OF])
    assert "note:" not in capsys.readouterr().err


def test_cli_needs_exactly_one_mode(fake_zip):
    with pytest.raises(SystemExit):
        cs.main(["--zip", str(fake_zip)])
    with pytest.raises(SystemExit):
        cs.main(["برنج", "--ids", "2909206651897", "--zip", str(fake_zip)])


# --- bad zips ------------------------------------------------------------------------------

def test_zip_without_csv(tmp_path):
    z = _zip(tmp_path, b"x", name="readme.txt")
    with pytest.raises(cs.CatalogError, match="exactly one .csv"):
        list(cs.iter_rows(z))


def test_zip_with_wrong_header(tmp_path):
    z = _zip(tmp_path, _rows_csv([], header=["ID", "Title"]))
    with pytest.raises(cs.CatalogError, match="unexpected header"):
        list(cs.iter_rows(z))


# --- guard: pipeline code must not import the tool -----------------------------------------

_IMPORT = re.compile(
    r"^\s*(?:import\s+(?:tools\.)?catalog_search\b"
    r"|from\s+(?:tools\.)?catalog_search\s+import\b"
    r"|from\s+tools\s+import\s+[^#\n]*\bcatalog_search\b)",
    re.MULTILINE,
)


@pytest.mark.parametrize("line", [
    "import catalog_search", "import tools.catalog_search as cs",
    "from tools.catalog_search import main", "from tools import x, catalog_search",
    "    from catalog_search import normalize",
])
def test_guard_pattern_catches_imports(line):
    assert _IMPORT.search(line)


@pytest.mark.parametrize("line", ["# see tools/catalog_search.py", "import tools_extra",
                                  "x = 'catalog_search'", "from tools import other"])
def test_guard_pattern_ignores_non_imports(line):
    assert not _IMPORT.search(line)


def test_src_and_eval_do_not_import_catalog_search():
    offenders = [str(p.relative_to(REPO))
                 for d in ("src", "eval") if (REPO / d).is_dir()
                 for p in (REPO / d).rglob("*.py")
                 if _IMPORT.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
