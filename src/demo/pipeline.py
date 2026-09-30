"""One invoice line, end to end: rate_at -> rules -> BM25 features -> score -> explanations.

Used by app.py and tools/eval_demo_rules.py. The score is computed from finding codes and BM25
features BEFORE any explanation exists, and explanations are never passed back to it.

CATALOG PROFILES (2026-09-28). A line declares one sstid, so all its rules must read ONE catalog.
The split "T2 on the fake catalog, everything else on the real one" is therefore a choice of
profile per run, not per rule:
    fake   data/sample/fake_catalog.csv (invented). Every rule runs, including T2.
    real   the manually downloaded catalog (data/catalog/, never committed). T1, T3, T4, T6,
           ID status and all retrieval run; T2 does NOT, because what the `Vat` column means is
           TODO(legal) (docs/data_dictionary.md §8). A skipped rule is reported as "not run",
           never as "no finding".
Tests always use the fake profile.

HARD FLAGS (2026-09-28). NOT_IN_CATALOG, NOT_IN_FORCE and QUARANTINED_ONLY are facts about the
declared ID, not risk. They bypass the score: a line that carries one gets score None and
needs_review True. The other rules still run and are listed.

THE SCORE IS A RANKING, NOT A GATE (2026-09-30). Any fired rule floors the score at the review
threshold (src/detect/score.py), so needs_review = hard flag or a fired rule. Order the queue by
(score, raw_score).
"""
import os
from dataclasses import dataclass
from pathlib import Path

from src.detect.engine import Context, InvoiceLine, load_rules, run_rules
from src.detect.explain import explain
from src.detect.features import CatalogSearch
from src.detect.legal import LegalUnits
from src.detect.rates import RateTable
from src.detect.score import FLOOR_TERM, HARD_FLAGS, HIGH_RISK, risk_score

ROOT = Path(__file__).resolve().parents[2]
FAKE_CATALOG = ROOT / "data" / "sample" / "fake_catalog.csv"
DEMO_INVOICES = ROOT / "data" / "sample" / "demo_invoices.csv"
# Lines generated from the real catalog carry real titles: kept out of git (data/interim/).
REAL_DEMO_INVOICES = ROOT / "data" / "interim" / "demo_invoices_real.csv"
N_ALTERNATIVES = 5
RATE_CODES = frozenset({"T2"})       # rules that read the catalog's Vat column as a VAT rate


def real_catalog_path():
    """SHENASEYAR_REAL_CATALOG, else the single zip in data/catalog/, else None."""
    env = os.environ.get("SHENASEYAR_REAL_CATALOG")
    if env:
        return Path(env)
    zips = sorted((ROOT / "data" / "catalog").glob("*.zip"))
    return zips[0] if len(zips) == 1 else None


@dataclass(frozen=True)
class CatalogProfile:
    name: str                # fake | real
    path: Path
    rates_trusted: bool      # may the catalog's Vat column be read as a VAT rate (T2)?
    invoices: Path           # the synthetic lines generated from this catalog

    @property
    def skipped_codes(self):
        return frozenset() if self.rates_trusted else RATE_CODES


FAKE = CatalogProfile("fake", FAKE_CATALOG, True, DEMO_INVOICES)


def real_profile(path=None):
    """TODO(legal): rates_trusted stays False until the Vat column is resolved."""
    path = path or real_catalog_path()
    if path is None:
        raise FileNotFoundError("no real catalog: set SHENASEYAR_REAL_CATALOG or put exactly one "
                                "zip in data/catalog/")
    return CatalogProfile("real", Path(path), False, REAL_DEMO_INVOICES)


def profile(name, path=None):
    if name == "fake":
        return FAKE if path is None else CatalogProfile("fake", Path(path), True, DEMO_INVOICES)
    if name == "real":
        return real_profile(path)
    raise ValueError(f"unknown profile {name!r}")


@dataclass(frozen=True)
class LineResult:
    line: InvoiceLine
    rate: object                 # rates.RateResult for the declared ID
    hits: tuple
    features: dict
    score: float | None          # None when a hard flag is present
    contributions: dict
    alternatives: tuple          # features.Candidate, best first, at most N_ALTERNATIVES
    explanations: tuple
    skipped: tuple = ()          # codes of rules not run under this profile (e.g. T2 on real)

    @property
    def codes(self):
        return [h.code for h in self.hits]

    @property
    def hard_flags(self):
        return tuple(h for h in self.hits if h.code in HARD_FLAGS)

    @property
    def raw_score(self):
        """The weighted sum without the review floor: orders lines that tie at the floor."""
        return None if self.score is None else self.score - self.contributions.get(FLOOR_TERM, 0.0)

    @property
    def high_risk(self):
        return self.score is not None and self.score >= HIGH_RISK

    @property
    def needs_review(self):
        return bool(self.hard_flags) or self.high_risk


class Checker:
    def __init__(self, table, rules, units, profile=FAKE):
        self.profile = profile
        self.skipped = tuple(sorted(r.code for r in rules if r.code in profile.skipped_codes))
        self.table, self.units = table, units
        self.rules = [r for r in rules if r.code not in profile.skipped_codes]
        self.search = CatalogSearch(table)

    @classmethod
    def from_profile(cls, prof=FAKE, rules_dir=None, units_path=None):
        rules = load_rules() if rules_dir is None else load_rules(rules_dir)
        units = LegalUnits.load() if units_path is None else LegalUnits.load(units_path)
        return cls(RateTable.from_catalog(prof.path), rules, units, prof)

    @classmethod
    def from_paths(cls, catalog=FAKE_CATALOG, rules_dir=None, units_path=None):
        """Fake profile on `catalog` (all rules). Kept for tests and old callers."""
        return cls.from_profile(profile("fake", catalog), rules_dir, units_path)

    def check(self, line):
        retrieval = self.search.features(line.sstt, line.sstid, line.issue_date)
        ctx = Context(self.table, retrieval)
        hits = tuple(run_rules(self.rules, line, ctx))
        feats = retrieval.as_dict()
        codes = [h.code for h in hits]
        if any(c in HARD_FLAGS for c in codes):
            score, contrib = None, {}
        else:
            score, contrib = risk_score(codes, feats)
        expl = tuple(explain(h, line, self.units) for h in hits)
        return LineResult(line, self.table.rate_at(line.sstid, line.issue_date), hits, feats,
                          score, contrib, retrieval.ranked[:N_ALTERNATIVES], expl, self.skipped)
