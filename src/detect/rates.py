"""Versioned rate table and rate_at(sstid, issue_date) (docs/architecture.md §5).

Built from the catalog's ACTIVE rows (R1-R4 applied by src.retrieval.catalog.classify_rows).
Each row is one version with a half-open validity interval, converted ONCE here:

    valid_from    = RunDate
    valid_to_excl = ExpirationDate + 1 day   (jdatetime; the source date is inclusive, §9)
                    empty ExpirationDate -> None (open-ended)

No other code adds or subtracts a day. All dates are zero-padded Jalali 'YYYY-MM-DD' strings, so
string comparison is date comparison (the format is checked on the way in).

rate_at returns an explicit status, never a guessed rate:
    OK                one version in force, or several with a unique latest valid_from
    AMBIGUOUS         several in force, tied on the latest valid_from (our data problem)
    NOT_IN_FORCE      the ID exists but no version is in force on the date (gap / before first)
    NOT_IN_CATALOG    the ID is not in the catalog file at all
    QUARANTINED_ONLY  the ID is in the file but every row of it was quarantined by R1-R4.
                      OPEN (Navid, architecture.md §3): which status such an ID should get is
                      undecided. It is kept distinct so no decision is made silently; it never
                      triggers T2 and carries score weight 0 until decided.

TODO(legal): what the catalog's `Vat` column means is unresolved (data_dictionary.md §8). The
table stores it as `rate` without interpreting it. v0.1 only runs on the invented fake catalog.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re

import jdatetime

from src.retrieval.catalog import classify_rows

TAX_STATUS = {"مشمول": "taxable", "معاف": "exempt", "غیر مشمول": "out_of_scope"}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Status(str, Enum):
    OK = "OK"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_IN_FORCE = "NOT_IN_FORCE"
    NOT_IN_CATALOG = "NOT_IN_CATALOG"
    QUARANTINED_ONLY = "QUARANTINED_ONLY"


class RateTableError(ValueError):
    pass


def parse_jalali(value):
    """Validate a zero-padded Jalali 'YYYY-MM-DD' string; return a jdatetime.date."""
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise RateTableError(f"date {value!r} is not zero-padded Jalali YYYY-MM-DD")
    y, m, d = map(int, value.split("-"))
    try:
        return jdatetime.date(y, m, d)
    except ValueError as e:
        raise RateTableError(f"date {value!r} is not a valid Jalali date: {e}") from None


def next_day(value):
    """Jalali 'YYYY-MM-DD' + 1 day (jdatetime), e.g. 1403-12-30 -> 1404-01-01."""
    return (parse_jalali(value) + jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Version:
    sstid: str
    title: str
    rate: Decimal
    tax_status: str          # taxable | exempt | out_of_scope
    id_type: str             # raw `Type` value, e.g. شناسه عمومی تولید داخل
    valid_from: str
    valid_to_excl: str | None

    @property
    def is_general(self):
        return "عمومی" in self.id_type

    @property
    def valid_to_incl(self):
        """Last day in force, for display only (the source convention)."""
        if self.valid_to_excl is None:
            return None
        return (parse_jalali(self.valid_to_excl) - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")

    def in_force(self, date):
        return self.valid_from <= date and (self.valid_to_excl is None or date < self.valid_to_excl)


@dataclass(frozen=True)
class RateResult:
    status: Status
    version: Version | None = None      # set only for OK
    candidates: tuple = ()              # the tied versions for AMBIGUOUS

    @property
    def rate(self):
        return self.version.rate if self.version else None


class RateTable:
    def __init__(self, versions, quarantined_only=()):
        self._by_id = {}
        for v in versions:
            self._by_id.setdefault(v.sstid, []).append(v)
        self.quarantined_only = frozenset(quarantined_only) - set(self._by_id)

    @classmethod
    def from_catalog(cls, path):
        versions, active, quarantined = [], set(), set()
        for n, row, reasons in classify_rows(path):
            if reasons is None:
                continue
            id_, title, vat, taxable, run, exp, *_ = row
            id_type = row[8]
            if reasons:
                quarantined.add(id_)
                continue
            active.add(id_)
            try:
                rate = Decimal(vat)
            except InvalidOperation:
                raise RateTableError(f"row {n} (ID {id_}): Vat {vat!r} is not a number") from None
            if taxable not in TAX_STATUS:
                raise RateTableError(f"row {n} (ID {id_}): unknown Taxable {taxable!r}")
            parse_jalali(run)
            versions.append(Version(id_, title, rate, TAX_STATUS[taxable], id_type, run,
                                    next_day(exp) if exp else None))
        return cls(versions, quarantined - active)

    def versions(self, sstid):
        return list(self._by_id.get(sstid, ()))

    def ids(self):
        return self._by_id.keys()

    def all_versions(self):
        for vs in self._by_id.values():
            yield from vs

    def rate_at(self, sstid, issue_date):
        """Status and version of `sstid` in force on `issue_date` (see module docstring)."""
        parse_jalali(issue_date)
        vs = self._by_id.get(sstid)
        if vs is None:
            if sstid in self.quarantined_only:
                return RateResult(Status.QUARANTINED_ONLY)
            return RateResult(Status.NOT_IN_CATALOG)
        live = [v for v in vs if v.in_force(issue_date)]
        if not live:
            return RateResult(Status.NOT_IN_FORCE)
        latest = max(v.valid_from for v in live)
        top = [v for v in live if v.valid_from == latest]
        if len(top) > 1:
            return RateResult(Status.AMBIGUOUS, candidates=tuple(top))
        return RateResult(Status.OK, top[0])

    def distinct_rates(self):
        """Every rate value present in the table (e.g. for generators; never a literal list)."""
        return sorted({v.rate for v in self.all_versions()})
