"""src/detect/score.py: hand-set weights, bounded score, no text input."""
import inspect

import pytest

from src.detect import score
from src.detect.features import RANK_ABSENT
from src.detect.score import HIGH_RISK, WEIGHTS, risk_score

BEST = {"sim_declared": 1.0, "rank_declared": 1, "margin_top1": 0.8}
WORST = {"sim_declared": 0.0, "rank_declared": RANK_ABSENT, "margin_top1": 1.0}


def test_docstring_says_hand_set_and_placeholder():
    doc = score.__doc__
    assert "HAND-SET, NOT LEARNED" in doc and "LightGBM" in doc and "PLACEHOLDER" in doc


def test_clean_well_matched_line_scores_zero():
    s, contrib = risk_score([], BEST)
    assert s == 0.0 and contrib["confident_alternative"] == 0.0     # margin ignored at rank 1


def test_findings_add_their_weights():
    s, contrib = risk_score(["T2"], BEST)
    assert s == pytest.approx(WEIGHTS["T2"]) and contrib == {
        "T2": WEIGHTS["T2"], "text_mismatch": 0.0, "rank_declared": 0.0,
        "confident_alternative": 0.0}


@pytest.mark.parametrize("code", ["T1", "T3", "T4"])
def test_any_fired_rule_floors_the_score_at_the_review_threshold(code):
    s, contrib = risk_score([code], BEST)
    assert s == HIGH_RISK
    assert contrib["review_floor"] == pytest.approx(HIGH_RISK - WEIGHTS[code])
    assert sum(contrib.values()) == pytest.approx(s)          # the terms still explain the score


def test_floor_never_lowers_and_is_absent_when_not_needed():
    s, contrib = risk_score(["T2"], WORST)
    assert s > HIGH_RISK and "review_floor" not in contrib
    assert "review_floor" not in risk_score([], WORST)[1]      # no rule fired: no floor
    assert risk_score([], WORST)[0] < HIGH_RISK                # BM25 terms alone never gate


def test_floor_keeps_the_weighted_sum_as_the_order_above_it():
    assert risk_score(["T2"], BEST)[0] > risk_score(["T6"], BEST)[0] >= HIGH_RISK


def test_codes_may_be_a_generator():
    assert risk_score((c for c in ["T1"]), BEST)[0] == HIGH_RISK


def test_duplicate_codes_count_once_and_score_is_capped():
    assert risk_score(["T2", "T2"], BEST)[0] == pytest.approx(WEIGHTS["T2"])
    assert risk_score(["T2", "T6", "T3"], WORST)[0] == 1.0


def test_retrieval_signals():
    feats = {"sim_declared": 0.4, "rank_declared": 11, "margin_top1": 0.5}
    s, c = risk_score([], feats)
    assert c["text_mismatch"] == pytest.approx(0.6 * WEIGHTS["text_mismatch"])
    assert c["rank_declared"] == pytest.approx(0.5 * WEIGHTS["rank_declared"])
    assert c["confident_alternative"] == pytest.approx(0.5 * WEIGHTS["confident_alternative"])
    assert s == pytest.approx(sum(c.values()))


def test_ambiguous_never_raises_the_score():
    assert WEIGHTS["AMBIGUOUS"] == 0.0
    assert risk_score(["AMBIGUOUS"], BEST)[0] == risk_score([], BEST)[0]


def test_hand_set_weights_unchanged():
    # pinned: the weights were not touched when T1 and the hard flags were added (2026-09-28)
    assert WEIGHTS == {"T2": 0.60, "T6": 0.50, "T3": 0.30, "T4": 0.30, "T1": 0.0,
                       "AMBIGUOUS": 0.0, "text_mismatch": 0.15, "rank_declared": 0.10,
                       "confident_alternative": 0.05}


@pytest.mark.parametrize("code", sorted(score.HARD_FLAGS))
def test_hard_flags_bypass_the_score(code):
    assert code not in WEIGHTS
    with pytest.raises(ValueError, match="hard flag"):
        risk_score([code], BEST)


def test_hard_flags_are_the_three_id_facts():
    assert score.HARD_FLAGS == {"NOT_IN_CATALOG", "NOT_IN_FORCE", "QUARANTINED_ONLY"}


def test_unknown_code_and_out_of_range_feature_fail():
    with pytest.raises(KeyError):
        risk_score(["T5"], BEST)
    with pytest.raises(ValueError):
        risk_score([], dict(BEST, sim_declared=1.5))


def test_score_cannot_see_text():
    params = list(inspect.signature(risk_score).parameters)
    assert params == ["codes", "features"]
    src = inspect.getsource(score)
    assert "explain" not in src.replace("explanation", "") and "sstt" not in src
