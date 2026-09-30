"""Transparent risk score for one invoice line (v0.1).

THE WEIGHTS BELOW ARE HAND-SET, NOT LEARNED. They were chosen by hand to order findings by the
severities in architecture.md §3; no data was fitted and no weight was tuned on the synthetic
lines. This module is the PLACEHOLDER for the calibrated LightGBM model (descoped, CLAUDE.md), and
its output is a ranking aid, not a probability.

    raw   = sum over finding codes present of WEIGHTS[code]
            + WEIGHTS["text_mismatch"] * (1 - sim_declared)
            + WEIGHTS["rank_declared"] * (rank_declared - 1) / (RANK_ABSENT - 1)
            + WEIGHTS["confident_alternative"] * margin_top1 * [rank_declared != 1]
    score = min(1, max(raw, HIGH_RISK))   if a rule fired (any finding code except AMBIGUOUS)
            min(1, raw)                   otherwise

THE SCORE IS A RANKING, NOT A GATE (decided 2026-09-30, architecture.md §6). A fired rule floors
the score at the review threshold, so whether a line enters the review queue is decided by the
rules (and the hard flags), never by the weights. The weighted sum only orders lines inside the
queue. This is structural: no weight was changed. The floor's share is reported as the
`review_floor` term, so the terms still add up to the score. Several floored lines tie at exactly
HIGH_RISK; `raw` (score minus `review_floor`) breaks those ties.
AMBIGUOUS does not floor: it is our data problem and must never raise a score (architecture §3).

Inputs are finding codes and three numeric BM25 features only. The function never receives any
text, so neither the invoice text nor any generated explanation can move the score.
AMBIGUOUS weighs 0 by decision (architecture.md §3: it must never raise a line's score).

HARD FLAGS bypass the score (decided 2026-09-28): NOT_IN_CATALOG, NOT_IN_FORCE and
QUARANTINED_ONLY are facts about the declared ID, not risk. They have no weight; passing one here
is an error, and the pipeline does not score a line that carries one (src/demo/pipeline.py).

T1 weighs 0 (2026-09-28): the weights were not touched when T1 was added. T1's evidence already
enters through the three BM25 terms below; giving T1 its own weight is an open decision.
"""
from src.detect.features import RANK_ABSENT

HARD_FLAGS = frozenset({"NOT_IN_CATALOG", "NOT_IN_FORCE", "QUARANTINED_ONLY"})

WEIGHTS = {
    # findings (each counted once per line)
    "T2": 0.60,
    "T6": 0.50,
    "T3": 0.30,
    "T4": 0.30,
    "T1": 0.0,
    "AMBIGUOUS": 0.0,
    # BM25 features, each signal in [0, 1]
    "text_mismatch": 0.15,
    "rank_declared": 0.10,
    "confident_alternative": 0.05,
}
HIGH_RISK = 0.5    # review threshold: every line with a fired rule scores at least this
NO_FLOOR = frozenset({"AMBIGUOUS"})     # informational findings: never raise a score
FLOOR_TERM = "review_floor"


def signals(features):
    """Map the three BM25 features to [0, 1] risk signals."""
    sim, rank, margin = features["sim_declared"], features["rank_declared"], features["margin_top1"]
    return {"text_mismatch": 1.0 - sim,
            "rank_declared": (rank - 1) / (RANK_ABSENT - 1),
            "confident_alternative": margin if rank != 1 else 0.0}


def risk_score(codes, features):
    """codes: iterable of finding codes; features: dict with sim_declared, rank_declared,
    margin_top1. Returns (score in [0, 1], {term: contribution}); the contributions include
    FLOOR_TERM when the floor lifted the score."""
    contrib = {}
    codes = list(dict.fromkeys(codes))
    for code in codes:
        if code in HARD_FLAGS:
            raise ValueError(f"{code} is a hard flag and bypasses the score")
        if code not in WEIGHTS:
            raise KeyError(f"no weight for finding code {code!r}")
        contrib[code] = WEIGHTS[code]
    for name, value in signals(features).items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"signal {name}={value} outside [0, 1]")
        contrib[name] = WEIGHTS[name] * value
    raw = sum(contrib.values())
    if raw < HIGH_RISK and any(c not in NO_FLOOR for c in codes):
        contrib[FLOOR_TERM] = HIGH_RISK - raw
        return HIGH_RISK, contrib
    return min(1.0, raw), contrib
