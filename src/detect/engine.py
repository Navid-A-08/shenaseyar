"""YAML rule engine (architecture.md §3-4).

A rule file (src/detect/rules/*.yaml) names a registered Python check and gives its parameters:

    id: t6_vat_arithmetic      # unique
    code: T6                   # T2 | T3 | T4 | T6 | ID_STATUS
    check: vat_arithmetic      # a name in CHECKS
    severity: high             # high | medium | low | none   (ID_STATUS sets it per status)
    description: ...           # one line, shown in the UI
    params: {...}              # exactly the keys the check declares

Unknown keys, unknown checks, missing or extra params are errors at load time, so a typo can never
silently disable a rule. The checks are Python because they are arithmetic and lookups; the YAML
holds everything a reviewer may want to see or tune.

Every check reads the rate only through table.rate_at(sstid, issue_date). The ID-status outcomes
(NOT_IN_CATALOG, NOT_IN_FORCE, AMBIGUOUS, QUARANTINED_ONLY) come from the ID_STATUS rule; T2 fires
only when rate_at is OK, so they are never reported as T2.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from src.detect.rates import Status, parse_jalali
from src.text.normalize import normalize
from src.retrieval.bm25 import tokenize

RULES_DIR = Path(__file__).resolve().parent / "rules"
RULE_KEYS = {"id", "code", "check", "severity", "description", "params"}
CODES = {"T2", "T3", "T4", "T6", "ID_STATUS"}
SEVERITIES = {"high", "medium", "low", "none"}
ID_STATUSES = {s.value for s in Status if s is not Status.OK}


class RuleError(Exception):
    pass


class LineError(ValueError):
    pass


def _dec(value, name):
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        raise LineError(f"{name}: {value!r} is not a number") from None
    if not d.is_finite():
        raise LineError(f"{name}: {value!r} is not finite")
    return d


@dataclass(frozen=True)
class InvoiceLine:
    """One invoice line. Numbers are Decimal (parsed from their text form, so no float error)."""
    issue_date: str
    sstid: str
    sstt: str
    am: Decimal
    fee: Decimal
    vra: Decimal
    vam: Decimal
    mu: str = ""

    @classmethod
    def parse(cls, issue_date, sstid, sstt, am, fee, vra, vam, mu=""):
        try:
            parse_jalali(issue_date)
        except ValueError as e:
            raise LineError(f"issue_date: {e}") from None
        sstid = str(sstid).strip()
        line = cls(issue_date, sstid, str(sstt), _dec(am, "am"), _dec(fee, "fee"),
                   _dec(vra, "vra"), _dec(vam, "vam"), str(mu))
        for name in ("am", "fee", "vra", "vam"):
            if getattr(line, name) < 0:
                raise LineError(f"{name}: must not be negative")
        return line

    @property
    def base(self):
        """Taxable base = am x fee. The demo line has no discount field (Moadian `dis`)."""
        return self.am * self.fee


@dataclass(frozen=True)
class Hit:
    code: str                 # T2 | T3 | T4 | T6 | NOT_IN_CATALOG | NOT_IN_FORCE | AMBIGUOUS | ...
    rule_id: str
    severity: str
    description: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Context:
    table: object                        # rates.RateTable
    retrieval: object = None             # features.RetrievalFeatures (T3 needs it)


# ---- checks --------------------------------------------------------------------------------
# Each check: fn(line, ctx, params) -> list of (code, severity_or_None, evidence).

def check_id_status(line, ctx, p):
    res = ctx.table.rate_at(line.sstid, line.issue_date)
    if res.status is Status.OK:
        return []
    ev = {"status": res.status.value}
    if res.candidates:
        ev["tied_rates"] = [str(v.rate) for v in res.candidates]
    return [(res.status.value, p["severity"][res.status.value], ev)]


def check_rate_mismatch(line, ctx, p):
    res = ctx.table.rate_at(line.sstid, line.issue_date)
    if res.status is not Status.OK:
        return []                         # ID_STATUS reports it; never T2
    v = res.version
    if line.vra == v.rate:                # Decimal: 10 == 10.0, 12.5 == 12.50
        return []
    return [("T2", None, {"declared_rate": line.vra, "reference_rate": v.rate,
                          "tax_status": v.tax_status, "valid_from": v.valid_from,
                          "valid_to_incl": v.valid_to_incl})]


def check_vat_arithmetic(line, ctx, p):
    tol = Decimal(str(p["tolerance_rial"]))
    expected = line.base * line.vra / 100
    diff = line.vam - expected
    if abs(diff) <= tol:
        return []
    return [("T6", None, {"base": line.base, "declared_rate": line.vra, "expected_vam": expected,
                          "declared_vam": line.vam, "difference": diff, "tolerance": tol})]


def check_general_with_specific(line, ctx, p):
    res = ctx.table.rate_at(line.sstid, line.issue_date)
    if res.status is not Status.OK or not res.version.is_general:
        return []
    r = ctx.retrieval
    if r is None:
        raise RuleError("T3 needs retrieval features in the context")
    if r.declared_score <= 0:
        return []                         # text does not match the declared ID at all: not T3
    better = [c for c in r.ranked[:p["top_k"]]
              if not c.version.is_general and c.score > r.declared_score]
    if not better:
        return []
    return [("T3", None, {"declared_type": res.version.id_type,
                          "specific_ids": [c.sstid for c in better],
                          "best_specific": better[0].sstid,
                          "best_specific_title": better[0].version.title})]


def check_vague_high_amount(line, ctx, p):
    vague = {normalize(w) for w in p["vague_words"]}
    toks = tokenize(line.sstt)
    words = [t for t in toks if not t.isdigit()]
    if not words or not all(t in vague for t in words):
        return []
    if line.base < Decimal(str(p["min_amount_rial"])):
        return []
    return [("T4", None, {"base": line.base, "min_amount": Decimal(str(p["min_amount_rial"]))})]


CHECKS = {
    "id_status": (check_id_status, {"severity"}),
    "rate_mismatch": (check_rate_mismatch, set()),
    "vat_arithmetic": (check_vat_arithmetic, {"tolerance_rial"}),
    "general_with_specific": (check_general_with_specific, {"top_k"}),
    "vague_high_amount": (check_vague_high_amount, {"vague_words", "min_amount_rial"}),
}


@dataclass(frozen=True)
class Rule:
    id: str
    code: str
    check: str
    severity: str
    description: str
    params: dict

    def run(self, line, ctx):
        fn = CHECKS[self.check][0]
        return [Hit(code=code, rule_id=self.id, severity=sev or self.severity,
                    description=self.description, evidence=ev)
                for code, sev, ev in fn(line, ctx, self.params)]


def _rule(raw, src):
    if not isinstance(raw, dict) or set(raw) != RULE_KEYS:
        got = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise RuleError(f"{src}: keys must be exactly {sorted(RULE_KEYS)}, got {got}")
    if raw["code"] not in CODES:
        raise RuleError(f"{src}: unknown code {raw['code']!r}")
    if raw["check"] not in CHECKS:
        raise RuleError(f"{src}: unknown check {raw['check']!r}")
    if raw["severity"] not in SEVERITIES:
        raise RuleError(f"{src}: unknown severity {raw['severity']!r}")
    params = raw["params"] or {}
    want = CHECKS[raw["check"]][1]
    if set(params) != want:
        raise RuleError(f"{src}: params must be exactly {sorted(want)}, got {sorted(params)}")
    if raw["check"] == "id_status":
        sev = params["severity"]
        if set(sev) != ID_STATUSES or not set(sev.values()) <= SEVERITIES:
            raise RuleError(f"{src}: severity must map each of {sorted(ID_STATUSES)}")
    return Rule(raw["id"], raw["code"], raw["check"], raw["severity"], raw["description"], params)


def load_rules(rules_dir=RULES_DIR):
    rules = []
    for path in sorted(Path(rules_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules.append(_rule(data, path.name))
    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise RuleError(f"duplicate rule ids in {rules_dir}")
    if not rules:
        raise RuleError(f"no rules in {rules_dir}")
    return rules


def run_rules(rules, line, ctx):
    hits = []
    for r in rules:
        hits.extend(r.run(line, ctx))
    return hits
