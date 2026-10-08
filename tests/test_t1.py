"""T1 (description vs declared ID): the pre-registered decision rule and its boundaries.

fires iff rate_at OK, s1 > 0, rank_declared > top_k (5) AND sim_declared < max_sim (0.5),
plus the precondition added 2026-10-08: the text has a word outside T4's vague-word list.
"""
from decimal import Decimal

import pytest

from src.detect.engine import (Context, InvoiceLine, RuleError, check_description_mismatch,
                               load_rules, run_rules)
from src.detect.features import RANK_ABSENT, CatalogSearch, Candidate, RetrievalFeatures
from src.detect.rates import RateTable, Version

SPEC = "شناسه اختصاصی تولید داخل"
D = "1404-06-01"
VAGUE = next(r for r in load_rules() if r.code == "T4").params["vague_words"]
P = {"top_k": 5, "max_sim": 0.5, "vague_words_from": "t4_vague_high_amount", "vague_words": VAGUE}


def v(sid, title, frm="1404-01-01", to=None):
    return Version(sid, title, Decimal("10"), "taxable", SPEC, frm, to)


TABLE = RateTable([v("2900000000001", "پیچ نمونه مدل 1"), v("2900000000002", "شیر فرضی"),
                   v("2900000000003", "برنج فرضی", to="1404-03-01")])
LINE = InvoiceLine.parse(D, "2900000000002", "پیچ نمونه مدل 1", "1", "1000", "10", "100")


def feats(rank, sim, s1=10.0):
    ranked = (Candidate("2900000000001", s1, TABLE.versions("2900000000001")[0]),) if s1 else ()
    return RetrievalFeatures(sim, rank, 0.5, sim * s1, ranked)


def fires(rank, sim, s1=10.0, line=LINE):
    return bool(check_description_mismatch(line, Context(TABLE, feats(rank, sim, s1)), P))


def test_committed_rule_has_the_preregistered_boundaries():
    rule = next(r for r in load_rules() if r.code == "T1")
    assert rule.params == {"top_k": 5, "max_sim": 0.5, "vague_words_from": "t4_vague_high_amount"}


def test_precondition_borrows_t4s_list_not_a_copy():
    rules = {r.code: r for r in load_rules()}
    assert rules["T1"].resolved == {"vague_words": rules["T4"].params["vague_words"]}


def _vague_line(sstt):
    return InvoiceLine.parse(D, "2900000000002", sstt, "1", "1000", "10", "100")


@pytest.mark.parametrize("sstt", ["خدمات", "کالا", "خدمات متفرقه", "بابت خدمات ۲", "ساير اقلام",
                                  "123"])
def test_abstains_on_vague_only_text(sstt):
    # features would fire (not in top 5, sim 0); the precondition alone stops T1
    assert not fires(RANK_ABSENT, 0.0, line=_vague_line(sstt))


@pytest.mark.parametrize("sstt", ["خدمات حمل", "کالای متفرقه", "اجناس فروش رفته"])
def test_one_word_outside_the_list_is_enough(sstt):
    # «حمل» is informative; «کالای» and «رفته» are not on T4's list (its known gap), so T1 inherits it
    assert fires(RANK_ABSENT, 0.0, line=_vague_line(sstt))


def test_reference_to_a_missing_rule_fails_loudly(tmp_path):
    import shutil
    from src.detect.engine import RULES_DIR
    shutil.copy(RULES_DIR / "t1_description_mismatch.yaml", tmp_path)
    with pytest.raises(RuleError, match="vague_words_from"):
        load_rules(tmp_path)


def test_t1_evidence_carries_both_tax_statuses():
    (_, _, ev), = check_description_mismatch(LINE, Context(TABLE, feats(RANK_ABSENT, 0.0)), P)
    assert ev["declared_tax_status"] == "taxable" and ev["best_tax_status"] == "taxable"


@pytest.mark.parametrize("rank, sim, expected", [
    (6, 0.49, True),               # both conditions just met
    (RANK_ABSENT, 0.0, True),      # declared ID not in the top 20 and no word in common
    (5, 0.0, False),               # among the shown alternatives: rank alone blocks
    (6, 0.5, False),               # boundary: sim exactly 0.5 does not fire
    (RANK_ABSENT, 0.9, False),     # far down the list but nearly as good a match: not T1
])
def test_decision_boundaries(rank, sim, expected):
    assert fires(rank, sim) is expected


def test_no_match_at_all_gives_no_evidence():
    assert not fires(RANK_ABSENT, 0.0, s1=0.0)


@pytest.mark.parametrize("sstid", ["2900000000003", "2999999999999"])   # not in force / absent
def test_silent_unless_rate_at_is_ok(sstid):
    line = InvoiceLine.parse(D, sstid, "پیچ نمونه مدل 1", "1", "1000", "10", "100")
    assert not fires(RANK_ABSENT, 0.0, line=line)


def test_needs_retrieval():
    with pytest.raises(RuleError):
        check_description_mismatch(LINE, Context(TABLE), P)


def test_evidence_names_the_best_alternative():
    (code, sev, ev), = check_description_mismatch(LINE, Context(TABLE, feats(RANK_ABSENT, 0.0)), P)
    assert code == "T1" and sev is None
    assert ev["best_sstid"] == "2900000000001" and ev["rank_declared"] == RANK_ABSENT


def test_end_to_end_with_bm25():
    ctx = Context(TABLE, CatalogSearch(TABLE).features(LINE.sstt, LINE.sstid, D))
    assert "T1" in [h.code for h in run_rules(load_rules(), LINE, ctx)]
    ok = InvoiceLine.parse(D, "2900000000001", "پیچ نمونه مدل 1", "1", "1000", "10", "100")
    ctx = Context(TABLE, CatalogSearch(TABLE).features(ok.sstt, ok.sstid, D))
    assert "T1" not in [h.code for h in run_rules(load_rules(), ok, ctx)]
