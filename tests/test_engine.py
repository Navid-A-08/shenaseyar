"""src/detect/engine.py: rule loading/validation and the T2, T3, T4, T6 and ID-status checks
(T1: tests/test_t1.py)."""
from decimal import Decimal

import pytest
import yaml

from src.detect.engine import (Context, InvoiceLine, LineError, RuleError, load_rules,
                               run_rules)
from src.detect.features import CatalogSearch
from src.detect.rates import RateTable, Status, Version

GEN, SPEC = "شناسه عمومی تولید داخل", "شناسه اختصاصی تولید داخل"
D = "1404-06-01"


def v(sid, title, rate="10", status="taxable", type_=SPEC, frm="1404-01-01", to=None):
    return Version(sid, title, Decimal(rate), status, type_, frm, to)


TABLE = RateTable([
    v("2900000000001", "پیچ نمونه مدل 1 شرکت الف"),
    v("2720000000001", "پیچ نمونه", type_=GEN),
    v("2900000000002", "نان فرضی", rate="0", status="exempt"),
    v("2900000000003", "برنج فرضی", rate="9", to="1404-03-01"),      # not in force on D
    v("2900000000004", "شیر فرضی", rate="12.5"),
], quarantined_only={"2900000000009"})
RULES = load_rules()


def line(sstid="2900000000001", sstt="پیچ نمونه مدل 1", am="2", fee="1000", vra="10",
         vam="200", date=D):
    return InvoiceLine.parse(date, sstid, sstt, am, fee, vra, vam)


def codes(ln, table=TABLE):
    ctx = Context(table, CatalogSearch(table).features(ln.sstt, ln.sstid, ln.issue_date))
    return [h.code for h in run_rules(RULES, ln, ctx)]


# ---- loading ---------------------------------------------------------------------------------

def test_committed_rules_load():
    assert {r.code for r in RULES} == {"T1", "T2", "T3", "T4", "T6", "ID_STATUS"}


def _write(tmp_path, **over):
    rule = {"id": "x", "code": "T6", "check": "vat_arithmetic", "severity": "high",
            "description": "d", "params": {"tolerance_rial": 1}}
    rule.update(over)
    (tmp_path / "r.yaml").write_text(yaml.safe_dump(rule, allow_unicode=True), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("over", [
    {"check": "no_such_check"}, {"code": "T9"}, {"severity": "urgent"},
    {"params": {}}, {"params": {"tolerance_rial": 1, "extra": 2}}, {"extra_key": 1},
])
def test_invalid_rule_files_fail_loudly(tmp_path, over):
    with pytest.raises(RuleError):
        load_rules(_write(tmp_path, **over))


def test_empty_rules_dir_is_an_error(tmp_path):
    with pytest.raises(RuleError):
        load_rules(tmp_path)


# ---- line parsing ----------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [("am", "abc"), ("vam", "-1"), ("vra", "nan"),
                                         ("date", "1404-13-01")])
def test_bad_lines_are_rejected(field, value):
    kw = {field: value}
    with pytest.raises(LineError):
        line(**kw)


# ---- T2 ---------------------------------------------------------------------------------------

def test_t2_fires_only_on_a_rate_mismatch():
    assert "T2" not in codes(line())
    assert "T2" not in codes(line(vra="10.00", vam="200"))                 # Decimal equality
    assert "T2" in codes(line(vra="9", vam="180"))
    assert "T2" in codes(line("2900000000002", "نان فرضی", vra="10", vam="200"))  # exempt -> 0
    assert "T2" not in codes(line("2900000000004", "شیر فرضی", vra="12.5", vam="250"))


def test_t2_reads_the_rate_only_through_rate_at(monkeypatch):
    calls = []
    real = TABLE.rate_at

    def spy(sstid, issue_date):
        calls.append((sstid, issue_date))
        return real(sstid, issue_date)

    monkeypatch.setattr(TABLE, "rate_at", spy)
    t2 = next(r for r in RULES if r.code == "T2")
    t2.run(line(vra="9", vam="180"), Context(TABLE))
    assert calls == [("2900000000001", D)]


def test_t2_follows_the_table_not_a_literal():
    odd = RateTable([v("2900000000001", "پیچ", rate="7")])
    assert "T2" not in codes(line(sstt="پیچ", vra="7", vam="140"), odd)
    assert "T2" in codes(line(sstt="پیچ", vra="10", vam="200"), odd)


@pytest.mark.parametrize("sstid,date,status", [
    ("2999999999999", D, "NOT_IN_CATALOG"),
    ("2900000000003", D, "NOT_IN_FORCE"),
    ("2900000000009", D, "QUARANTINED_ONLY"),
])
def test_id_status_is_its_own_finding_never_t2(sstid, date, status):
    got = codes(line(sstid, vra="37", vam="740", date=date))    # a rate no table has
    assert status in got and "T2" not in got


def test_ambiguous_is_its_own_finding_never_t2():
    t = RateTable([v("2900000000001", "پیچ", rate="10"), v("2900000000001", "پیچ", rate="9")])
    got = codes(line(sstt="پیچ", vra="37", vam="740"), t)
    assert "AMBIGUOUS" in got and "T2" not in got


def test_hit_carries_rule_id_and_severity():
    ctx = Context(TABLE, CatalogSearch(TABLE).features("پیچ", "2999999999999", D))
    hits = {h.code: h for h in run_rules(RULES, line("2999999999999", vra="9", vam="180"), ctx)}
    assert (hits["NOT_IN_CATALOG"].rule_id, hits["NOT_IN_CATALOG"].severity) == ("id_status", "high")
    ctx = Context(TABLE, CatalogSearch(TABLE).features("پیچ", "2900000000001", D))
    (h,) = run_rules(RULES, line(vra="9", vam="180"), ctx)
    assert (h.code, h.rule_id, h.severity) == ("T2", "t2_rate_mismatch", "high")


def test_id_status_severities_follow_architecture():
    rule = next(r for r in RULES if r.code == "ID_STATUS")
    assert rule.params["severity"] == {"NOT_IN_CATALOG": "high", "NOT_IN_FORCE": "medium",
                                       "AMBIGUOUS": "none", "QUARANTINED_ONLY": "none"}
    assert {s.value for s in Status} - set(rule.params["severity"]) == {"OK"}


# ---- T6 --------------------------------------------------------------------------------------

def test_t6_tolerance_is_one_rial_inclusive():
    t6 = next(r for r in RULES if r.code == "T6")
    assert Decimal(str(t6.params["tolerance_rial"])) == 1


# base = 3 x 333.5 = 1000.5; vra 10 -> exact vam = 100.05
@pytest.mark.parametrize("vam,fires", [
    ("100.05", False),       # exact
    ("100", False),          # floor
    ("101", False),          # ceiling
    ("101.05", False),       # +1.00 exactly: at the boundary, accepted
    ("99.05", False),        # -1.00 exactly: at the boundary, accepted
    ("101.06", True),        # +1.01: just over
    ("99.04", True),         # -1.01: just under
    ("101.0500001", True),   # smallest excess still fires (exact Decimal arithmetic)
    ("99.0499999", True),
])
def test_t6_boundary_both_directions(vam, fires):
    got = codes(line(am="3", fee="333.5", vra="10", vam=vam))
    assert ("T6" in got) is fires


def test_t6_large_amounts_have_no_float_error():
    # 123,456,789,012 x 9% = 11,111,111,011.08 ; float would drift at this size
    assert "T6" not in codes(line("2900000000003", "برنج", am="1", fee="123456789012", vra="9",
                                  vam="11111111012", date="1404-02-01"))
    assert "T6" in codes(line("2900000000003", "برنج", am="1", fee="123456789012", vra="9",
                              vam="11111111013", date="1404-02-01"))


def test_t6_uses_declared_rate_not_reference():
    # rate is wrong (T2) but vam agrees with the declared rate: T6 must stay silent
    got = codes(line(vra="9", vam="180"))
    assert "T2" in got and "T6" not in got


# ---- T3 --------------------------------------------------------------------------------------

def test_t3_fires_when_a_specific_id_matches_better():
    assert "T3" in codes(line("2720000000001", "پیچ نمونه مدل 1 شرکت الف"))


def test_t3_silent_when_the_general_id_is_the_best_match():
    assert "T3" not in codes(line("2720000000001", "پیچ نمونه"))


def test_t3_silent_for_specific_declared_ids():
    assert "T3" not in codes(line("2900000000001", "پیچ نمونه"))


def test_t3_silent_when_text_does_not_match_the_declared_id():
    assert "T3" not in codes(line("2720000000001", "شیر فرضی"))


# ---- T4 --------------------------------------------------------------------------------------

def _t4(sstt, fee):
    return "T4" in codes(line(sstt=sstt, am="1", fee=fee, vra="10", vam=str(int(fee) // 10)))


def test_t4_vague_and_high():
    assert _t4("کالا", "1000000000")                  # exactly the threshold counts
    assert _t4("ساير اقلام", "2000000000")            # Arabic ي normalized
    assert not _t4("کالاهای متفرقه", "2000000000")    # known gap: inflected form not listed
    assert _t4("خدمات ۲", "2000000000")               # digits ignored


def test_t4_needs_both_conditions():
    assert not _t4("کالا", "999999990")               # below threshold
    assert not _t4("پیچ نمونه", "5000000000")         # specific text
    assert not _t4("کالا پیچ", "5000000000")          # one non-vague word
    assert not _t4("123", "5000000000")               # no words at all
