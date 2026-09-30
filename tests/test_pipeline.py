"""src/demo/pipeline.py: catalog profiles (T2 only where rates are trusted) and hard flags."""
import pytest

from src.demo.pipeline import (FAKE, FAKE_CATALOG, CatalogProfile, Checker, profile,
                               real_catalog_path)
from src.detect.engine import InvoiceLine
from src.detect.rates import RateTable

T2_LINE = InvoiceLine.parse("1405-02-01", "2909206651897", "برنج فرضی", "1", "1000", "9", "90")
# fake-catalog stand-in for the real catalog: same rules, but Vat is not trusted
REALISH = CatalogProfile("real", FAKE_CATALOG, False, FAKE.invoices)


@pytest.fixture(scope="module")
def fake():
    return Checker.from_paths()


@pytest.fixture(scope="module")
def realish():
    return Checker.from_profile(REALISH)


def test_fake_profile_runs_t2(fake):
    r = fake.check(T2_LINE)
    assert "T2" in r.codes and r.skipped == ()


def test_untrusted_rates_skip_t2_and_say_so(realish):
    r = realish.check(T2_LINE)
    assert "T2" not in r.codes and r.skipped == ("T2",)
    assert "T2" not in {rule.code for rule in realish.rules}


def test_untrusted_rates_keep_every_other_rule(fake, realish):
    assert {r.code for r in fake.rules} - {r.code for r in realish.rules} == {"T2"}


def test_hard_flag_bypasses_the_score(fake):
    r = fake.check(InvoiceLine.parse("1405-02-01", "2999999999999", "برنج فرضی", "1", "1000",
                                     "9", "95"))
    assert [h.code for h in r.hard_flags] == ["NOT_IN_CATALOG"]
    assert r.score is None and r.contributions == {}
    assert r.needs_review and not r.high_risk
    assert "T6" in r.codes                   # other rules still run and are listed


def test_not_in_force_and_quarantined_are_hard_flags_too(tmp_path, make_catalog):
    from tests.conftest import cat_row
    path = make_catalog([cat_row("2900000000001", run="1404-01-01", exp="1404-02-01"),
                         cat_row("2900000000009", run="1404-05-01", exp="1404-01-01")])  # R3
    c = Checker(RateTable.from_catalog(path), Checker.from_paths().rules, Checker.from_paths().units)
    for sstid, code in [("2900000000001", "NOT_IN_FORCE"), ("2900000000009", "QUARANTINED_ONLY")]:
        r = c.check(InvoiceLine.parse("1404-06-01", sstid, "کالای فرضی", "1", "1000", "10", "100"))
        assert [h.code for h in r.hard_flags] == [code] and r.score is None and r.needs_review


def test_unflagged_line_is_scored(fake):
    r = fake.check(T2_LINE)
    assert r.hard_flags == () and r.score is not None and r.needs_review == r.high_risk


def test_fired_rule_always_enters_review_and_raw_score_orders_ties(fake):
    # T1 (weight 0): declared ID has nothing in common with the text
    r = fake.check(InvoiceLine.parse("1405-02-01", "2909206651897", "خدمات آموزشی آزمایشی", "1",
                                     "1000", "10", "100"))
    assert r.codes == ["T1"]
    assert r.score == 0.5 and r.needs_review and 0 < r.raw_score < 0.5
    assert r.raw_score == pytest.approx(r.score - r.contributions["review_floor"])
    clean = fake.check(InvoiceLine.parse("1405-02-01", "2909206651897", "برنج فرضی", "1", "1000",
                                         "10", "100"))
    assert clean.codes == [] and not clean.needs_review and clean.raw_score == clean.score


def test_profiles():
    assert profile("fake") is FAKE and FAKE.rates_trusted
    with pytest.raises(ValueError):
        profile("other")


def test_real_catalog_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SHENASEYAR_REAL_CATALOG", str(tmp_path / "x.zip"))
    assert real_catalog_path() == tmp_path / "x.zip"
    p = profile("real")
    assert p.name == "real" and not p.rates_trusted and p.skipped_codes == {"T2"}
