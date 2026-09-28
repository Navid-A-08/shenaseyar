"""src/detect/score.py: hand-set weights, bounded score, no text input."""
import inspect

import pytest

from src.detect import score
from src.detect.features import RANK_ABSENT
from src.detect.score import WEIGHTS, risk_score

BEST = {"sim_declared": 1.0, "rank_declared": 1, "margin_top1": 0.8}
WORST = {"sim_declared": 0.0, "rank_declared": RANK_ABSENT, "margin_top1": 1.0}


def test_docstring_says_hand_set_and_placeholder():
    doc = score.__doc__
    assert "HAND-SET, NOT LEARNED" in doc and "LightGBM" in doc and "PLACEHOLDER" in doc


def test_clean_well_matched_line_scores_zero():
    s, contrib = risk_score([], BEST)
    assert s == 0.0 and contrib["confident_alternative"] == 0.0     # margin ignored at rank 1


def test_findings_add_their_weights():
    s, contrib = risk_score(["T3"], BEST)
    assert s == pytest.approx(WEIGHTS["T3"]) and contrib == {
        "T3": WEIGHTS["T3"], "text_mismatch": 0.0, "rank_declared": 0.0,
        "confident_alternative": 0.0}


def test_duplicate_codes_count_once_and_score_is_capped():
    assert risk_score(["T6", "T6"], BEST)[0] == pytest.approx(WEIGHTS["T6"])
    assert risk_score(["T2", "T6", "NOT_IN_CATALOG"], WORST)[0] == 1.0


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


def test_severity_order_is_respected():
    assert WEIGHTS["NOT_IN_CATALOG"] > WEIGHTS["NOT_IN_FORCE"] > WEIGHTS["AMBIGUOUS"]


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
