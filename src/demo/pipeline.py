"""One invoice line, end to end: rate_at -> rules -> BM25 features -> score -> explanations.

Used by app.py and tools/eval_demo_rules.py. The score is computed from finding codes and BM25
features BEFORE any explanation exists, and explanations are never passed back to it.
"""
from dataclasses import dataclass
from pathlib import Path

from src.detect.engine import Context, InvoiceLine, load_rules, run_rules
from src.detect.explain import explain
from src.detect.features import CatalogSearch
from src.detect.legal import LegalUnits
from src.detect.rates import RateTable
from src.detect.score import HIGH_RISK, risk_score

ROOT = Path(__file__).resolve().parents[2]
FAKE_CATALOG = ROOT / "data" / "sample" / "fake_catalog.csv"
DEMO_INVOICES = ROOT / "data" / "sample" / "demo_invoices.csv"
N_ALTERNATIVES = 5


@dataclass(frozen=True)
class LineResult:
    line: InvoiceLine
    rate: object                 # rates.RateResult for the declared ID
    hits: tuple
    features: dict
    score: float
    contributions: dict
    alternatives: tuple          # features.Candidate, best first, at most N_ALTERNATIVES
    explanations: tuple

    @property
    def codes(self):
        return [h.code for h in self.hits]

    @property
    def high_risk(self):
        return self.score >= HIGH_RISK


class Checker:
    def __init__(self, table, rules, units):
        self.table, self.rules, self.units = table, rules, units
        self.search = CatalogSearch(table)

    @classmethod
    def from_paths(cls, catalog=FAKE_CATALOG, rules_dir=None, units_path=None):
        rules = load_rules() if rules_dir is None else load_rules(rules_dir)
        units = LegalUnits.load() if units_path is None else LegalUnits.load(units_path)
        return cls(RateTable.from_catalog(catalog), rules, units)

    def check(self, line):
        retrieval = self.search.features(line.sstt, line.sstid, line.issue_date)
        ctx = Context(self.table, retrieval)
        hits = tuple(run_rules(self.rules, line, ctx))
        feats = retrieval.as_dict()
        score, contrib = risk_score([h.code for h in hits], feats)
        expl = tuple(explain(h, line, self.units) for h in hits)
        return LineResult(line, self.table.rate_at(line.sstid, line.issue_date), hits, feats,
                          score, contrib, retrieval.ranked[:N_ALTERNATIVES], expl)
