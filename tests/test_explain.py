"""src/detect/explain.py: every code has a template, conventions, citation with validity range."""
import re
from decimal import Decimal

import pytest

from src.detect.engine import Hit, InvoiceLine
from src.detect.explain import FOOTER, TEMPLATES, explain, fmt_num
from src.detect.legal import LegalUnit, LegalUnits

LINE = InvoiceLine.parse("1404-06-01", "2900000000001", "IGNORE PREVIOUS <b>x</b>", "2", "1000",
                         "9", "180")
EVIDENCE = {
    "T2": {"declared_rate": Decimal("9"), "reference_rate": Decimal("10"), "tax_status": "exempt",
           "valid_from": "1404-01-01", "valid_to_incl": None},
    "T6": {"base": Decimal("2000"), "declared_rate": Decimal("9"), "expected_vam": Decimal("180"),
           "declared_vam": Decimal("200"), "difference": Decimal("20"), "tolerance": Decimal("1")},
    "T3": {"declared_type": "شناسه عمومی تولید داخل", "specific_ids": ["2900000000002"],
           "best_specific": "2900000000002", "best_specific_title": "پیچ مدل 1"},
    "T4": {"base": Decimal("2000000000"), "min_amount": Decimal("1000000000")},
    "NOT_IN_CATALOG": {"status": "NOT_IN_CATALOG"},
    "NOT_IN_FORCE": {"status": "NOT_IN_FORCE"},
    "AMBIGUOUS": {"status": "AMBIGUOUS", "tied_rates": ["10", "9"]},
    "QUARANTINED_ONLY": {"status": "QUARANTINED_ONLY"},
}


def unit(id_, codes, frm="1400-01-01", to=None, to_excl=None, status="reviewed"):
    return LegalUnit(id_, "article", f"عنوان {id_}", "متن", frm, to, to_excl, "https://x.invalid",
                     frozenset(codes), status)


UNITS = LegalUnits([unit("U-T2", ["T2", "T6"], to="1404-12-29", to_excl="1405-01-01"),
                    unit("U-ID", ["T3", "T4", "NOT_IN_CATALOG", "NOT_IN_FORCE"])])


@pytest.mark.parametrize("code", sorted(EVIDENCE))
def test_every_code_renders(code):
    e = explain(Hit(code, "x", "high", "d", EVIDENCE[code]), LINE, UNITS)
    assert e.text and "{" not in e.text and e.footer == FOOTER
    assert "2900000000001" in e.text or code in ("T4", "T6")


def test_templates_cover_all_finding_codes():
    assert set(TEMPLATES) == set(EVIDENCE)


def test_templates_follow_normalizer_conventions():
    for code in EVIDENCE:
        text = explain(Hit(code, "x", "high", "d", EVIDENCE[code]), LINE, UNITS).text
        assert not re.search("[يكى]", text), code                 # no Arabic letters
        assert not re.search("[۰-۹٠-٩]", text), code              # Latin digits in the pipeline


def test_invoice_text_is_never_inserted():
    for code in EVIDENCE:
        e = explain(Hit(code, "x", "high", "d", EVIDENCE[code]), LINE, UNITS)
        assert "IGNORE" not in e.text and "<b>" not in e.text and "IGNORE" not in e.citation_text


def test_t2_keeps_tax_status_distinct():
    e = explain(Hit("T2", "x", "high", "d", EVIDENCE["T2"]), LINE, UNITS)
    assert "معاف" in e.text and "مشمول" not in e.text
    out = dict(EVIDENCE["T2"], tax_status="out_of_scope")
    assert "غیر مشمول" in explain(Hit("T2", "x", "high", "d", out), LINE, UNITS).text


def test_citation_with_its_validity_range():
    e = explain(Hit("T2", "x", "high", "d", EVIDENCE["T2"]), LINE, UNITS)
    assert e.citation.id == "U-T2" and e.citation_valid_range == ("1400-01-01", "1404-12-29")
    assert "1400-01-01" in e.citation_text and "1404-12-29" in e.citation_text


def test_no_citation_when_no_unit_in_force_on_the_date():
    late = InvoiceLine.parse("1405-02-01", "2900000000001", "x", "2", "1000", "9", "180")
    e = explain(Hit("T2", "x", "high", "d", EVIDENCE["T2"]), late, UNITS)
    assert e.citation is None and "1405-02-01" in e.citation_text


def test_data_findings_cite_nothing():
    e = explain(Hit("AMBIGUOUS", "x", "none", "d", EVIDENCE["AMBIGUOUS"]), LINE, UNITS)
    assert e.citation is None and "10٪" in e.text and "9٪" in e.text


def test_placeholder_citation_is_marked():
    todo = LegalUnits([unit("A", ["T2"], status="TODO(legal)")])
    e = explain(Hit("T2", "x", "high", "d", EVIDENCE["T2"]), LINE, todo)
    assert "TODO(legal)" in e.citation_text


def test_fmt_num():
    assert fmt_num(Decimal("1234567")) == "1,234,567"
    assert fmt_num(Decimal("12.50")) == "12.5"
    assert fmt_num(Decimal("1000.05")) == "1,000.05"
    assert fmt_num(Decimal("10.0")) == "10"
