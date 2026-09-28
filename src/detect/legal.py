"""Loader for data/legal/units.json: the legal units the explanation templates cite.

The file is filled by hand (see its `_doc` field). This module only validates and looks up; it
never supplies legal text. Validity uses the catalog's convention: `valid_to` in the file is the
LAST day in force (inclusive); it is converted once here to a half-open end with jdatetime.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from src.detect.rates import RateTableError, next_day, parse_jalali

UNITS_PATH = Path(__file__).resolve().parents[2] / "data" / "legal" / "units.json"
KINDS = {"article", "note", "clause"}
CODES = {"T2", "T3", "T4", "T6", "NOT_IN_CATALOG", "NOT_IN_FORCE"}
STATUSES = {"reviewed", "TODO(legal)"}
FIELDS = {"id", "kind", "title", "body", "valid_from", "valid_to", "source_url", "applies_to",
          "status"}


class LegalUnitsError(Exception):
    pass


@dataclass(frozen=True)
class LegalUnit:
    id: str
    kind: str
    title: str
    body: str
    valid_from: str
    valid_to: str | None          # inclusive, as written in the file (display)
    valid_to_excl: str | None     # half-open end used for comparisons
    source_url: str
    applies_to: frozenset
    status: str

    @property
    def is_placeholder(self):
        return self.status != "reviewed"

    def in_force(self, date):
        return self.valid_from <= date and (self.valid_to_excl is None or date < self.valid_to_excl)


def _unit(raw, n):
    where = f"units[{n}]"
    if not isinstance(raw, dict):
        raise LegalUnitsError(f"{where}: not an object")
    missing, extra = FIELDS - raw.keys(), raw.keys() - FIELDS
    if missing or extra:
        raise LegalUnitsError(f"{where}: missing {sorted(missing)}, unknown {sorted(extra)}")
    for f in ("id", "title", "body", "valid_from", "source_url", "status"):
        if not isinstance(raw[f], str):
            raise LegalUnitsError(f"{where}.{f}: must be a string")
    if not raw["id"].strip() or not raw["title"].strip() or not raw["body"].strip():
        raise LegalUnitsError(f"{where}: id, title and body must be non-empty")
    if raw["kind"] not in KINDS:
        raise LegalUnitsError(f"{where}.kind: {raw['kind']!r} not in {sorted(KINDS)}")
    if raw["status"] not in STATUSES:
        raise LegalUnitsError(f"{where}.status: {raw['status']!r} not in {sorted(STATUSES)}")
    codes = raw["applies_to"]
    if not isinstance(codes, list) or not codes or not set(codes) <= CODES:
        raise LegalUnitsError(f"{where}.applies_to: must be a non-empty list from {sorted(CODES)}")
    try:
        parse_jalali(raw["valid_from"])
        end = raw["valid_to"]
        if end is not None:
            parse_jalali(end)
            if end < raw["valid_from"]:
                raise LegalUnitsError(f"{where}: valid_to {end} is before valid_from")
    except RateTableError as e:
        raise LegalUnitsError(f"{where}: {e}") from None
    if raw["status"] == "reviewed" and not raw["source_url"].strip():
        raise LegalUnitsError(f"{where}: a reviewed unit needs a source_url")
    return LegalUnit(raw["id"], raw["kind"], raw["title"], raw["body"], raw["valid_from"], end,
                     next_day(end) if end else None, raw["source_url"], frozenset(codes),
                     raw["status"])


class LegalUnits:
    def __init__(self, units):
        self.units = list(units)
        ids = [u.id for u in self.units]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise LegalUnitsError(f"duplicate unit ids: {sorted(dup)}")
        self._by_id = {u.id: u for u in self.units}

    @classmethod
    def load(cls, path=UNITS_PATH):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise LegalUnitsError(f"{path}: invalid JSON: {e}") from None
        if data.get("schema_version") != 1 or not isinstance(data.get("units"), list):
            raise LegalUnitsError(f"{path}: expected schema_version 1 and a 'units' list")
        return cls(_unit(u, n) for n, u in enumerate(data["units"]))

    def get(self, unit_id):
        return self._by_id.get(unit_id)

    def for_finding(self, code, date):
        """Units that may be cited for `code` and are in force on `date`, reviewed ones first."""
        parse_jalali(date)
        hits = [u for u in self.units if code in u.applies_to and u.in_force(date)]
        return sorted(hits, key=lambda u: (u.is_placeholder, u.id))
