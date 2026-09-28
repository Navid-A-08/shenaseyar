"""Transparent risk score for one invoice line (v0.1).

THE WEIGHTS BELOW ARE HAND-SET, NOT LEARNED. They were chosen by hand to order findings by the
severities in architecture.md §3; no data was fitted and no weight was tuned on the synthetic
lines. This module is the PLACEHOLDER for the calibrated LightGBM model (descoped, CLAUDE.md), and
its output is a ranking aid, not a probability.

    score = min(1, sum over finding codes present of WEIGHTS[code]
                   + WEIGHTS["text_mismatch"] * (1 - sim_declared)
                   + WEIGHTS["rank_declared"] * (rank_declared - 1) / (RANK_ABSENT - 1)
                   + WEIGHTS["confident_alternative"] * margin_top1 * [rank_declared != 1])

Inputs are finding codes and three numeric BM25 features only. The function never receives any
text, so neither the invoice text nor any generated explanation can move the score.
AMBIGUOUS weighs 0 by decision (architecture.md §3: it must never raise a line's score).
QUARANTINED_ONLY weighs 0 while its treatment is OPEN.
"""
from src.detect.features import RANK_ABSENT

WEIGHTS = {
    # findings (each counted once per line)
    "T2": 0.60,
    "T6": 0.50,
    "NOT_IN_CATALOG": 0.60,
    "NOT_IN_FORCE": 0.30,
    "T3": 0.30,
    "T4": 0.30,
    "AMBIGUOUS": 0.0,
    "QUARANTINED_ONLY": 0.0,
    # BM25 features, each signal in [0, 1]
    "text_mismatch": 0.15,
    "rank_declared": 0.10,
    "confident_alternative": 0.05,
}
HIGH_RISK = 0.5    # hand-set display threshold ("needs review"); not a capacity-based cut-off


def signals(features):
    """Map the three BM25 features to [0, 1] risk signals."""
    sim, rank, margin = features["sim_declared"], features["rank_declared"], features["margin_top1"]
    return {"text_mismatch": 1.0 - sim,
            "rank_declared": (rank - 1) / (RANK_ABSENT - 1),
            "confident_alternative": margin if rank != 1 else 0.0}


def risk_score(codes, features):
    """codes: iterable of finding codes; features: dict with sim_declared, rank_declared,
    margin_top1. Returns (score in [0, 1], {term: contribution})."""
    contrib = {}
    for code in dict.fromkeys(codes):
        if code not in WEIGHTS:
            raise KeyError(f"no weight for finding code {code!r}")
        contrib[code] = WEIGHTS[code]
    for name, value in signals(features).items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"signal {name}={value} outside [0, 1]")
        contrib[name] = WEIGHTS[name] * value
    return min(1.0, sum(contrib.values())), contrib
