"""Catalog snapshot: apply data-quality rules R1-R4 (docs/data_quality.md), then keep the rows in
force on one date. This is what retrievers index and what the harness checks IDs against.

  R1  exact duplicate (all 10 columns)       keep one copy
  R2  more than one row per (ID, RunDate)    all rows of that key -> quarantine
  R3  RunDate > ExpirationDate               quarantine
  R4  ID not exactly 13 characters           quarantine
R2-R4 are checked independently on the deduplicated rows; a row can carry several reasons.

In force on `as_of`: RunDate <= as_of <= ExpirationDate (inclusive; empty = open-ended).
Dates are compared as strings, so every compared date must be zero-padded YYYY-MM-DD; any other
value is an error naming the row (never compared).

Reads the zip (one CSV entry) or a plain CSV, streamed twice. Nothing is extracted.
"""
import csv
import hashlib
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HEADER = ["ID", "DescriptionOfID", "Vat", "Taxable", "RunDate", "ExpirationDate",
          "CreateDate", "LastEditDate", "Type", "PricingDescription"]
RULES = "R1-R4 (docs/data_quality.md)"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CatalogError(Exception):
    pass


def _check_date(value, where):
    if not _DATE.fullmatch(value):
        raise CatalogError(f"{where}: date {value!r} is not zero-padded YYYY-MM-DD")
    return value


def iter_rows(path):
    """Yield (row_number, raw_row_list) for each data row of a catalog zip or CSV."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise CatalogError(f"{path}: expected exactly one .csv entry, found {len(names)}")
            with zf.open(names[0]) as raw:
                yield from _read(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), path)
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            yield from _read(f, path)


def _read(stream, path):
    reader = csv.reader(stream)
    header = next(reader, None)
    if header != HEADER:
        raise CatalogError(f"{path}: unexpected header {header}")
    for n, row in enumerate(reader, start=1):
        if len(row) != len(HEADER):
            raise CatalogError(f"{path} row {n}: {len(row)} fields, expected {len(HEADER)}")
        yield n, row


def _digest(row):
    return hashlib.blake2b("\x1f".join(row).encode("utf-8"), digest_size=16).digest()


@dataclass
class Snapshot:
    as_of: str
    rules: str = RULES
    raw_ids: set = field(default_factory=set)               # every ID in the file
    active_ids: set = field(default_factory=set)            # >= 1 row passing R1-R4 (any date)
    quarantined_only_ids: set = field(default_factory=set)  # every row quarantined
    index_ids: set = field(default_factory=set)             # >= 1 active row in force on as_of
    docs: list = field(default_factory=list)                # (ID, title) of active in-force rows
    counts: dict = field(default_factory=dict)

    def describe(self):
        return f"rules {self.rules}; index = active rows in force on {self.as_of}"


def load_snapshot(path, as_of):
    """Apply R1-R4 and the in-force filter. Returns a Snapshot."""
    _check_date(as_of, "as_of")
    # Pass 1: R1 digests and (ID, RunDate) counts over distinct rows.
    seen, per_key, raw_rows = set(), Counter(), 0
    for _, row in iter_rows(path):
        raw_rows += 1
        d = _digest(row)
        if d in seen:
            continue
        seen.add(d)
        per_key[(row[0], row[4])] += 1

    # Pass 2: classify each distinct row.
    snap = Snapshot(as_of=as_of)
    emitted = set()
    has_active, has_quarantined = set(), set()
    c = Counter()
    for n, row in iter_rows(path):
        id_, title, run, exp = row[0], row[1], row[4], row[5]
        snap.raw_ids.add(id_)
        d = _digest(row)
        if d in emitted:
            c["r1_extra_copies"] += 1
            continue
        emitted.add(d)
        where = f"row {n} (ID {id_})"
        reasons = []
        if per_key[(id_, run)] > 1:
            reasons.append("R2")
        _check_date(run, f"{where} RunDate")
        if exp:
            _check_date(exp, f"{where} ExpirationDate")
            if run > exp:
                reasons.append("R3")
        if len(id_) != 13:
            reasons.append("R4")
        for r in reasons:
            c[f"{r.lower()}_rows"] += 1
        if len(reasons) > 1:
            c["rows_with_several_reasons"] += 1
        if reasons:
            has_quarantined.add(id_)
            continue
        has_active.add(id_)
        if run <= as_of and (not exp or as_of <= exp):
            snap.docs.append((id_, title))
            snap.index_ids.add(id_)

    snap.active_ids = has_active
    snap.quarantined_only_ids = has_quarantined - has_active
    c["r2_keys"] = sum(1 for v in per_key.values() if v > 1)
    snap.counts = {"raw_rows": raw_rows, "rows_after_r1": len(emitted),
                   "r1_extra_copies": c["r1_extra_copies"], "r2_keys": c["r2_keys"],
                   "r2_rows": c["r2_rows"], "r3_rows": c["r3_rows"], "r4_rows": c["r4_rows"],
                   "rows_with_several_reasons": c["rows_with_several_reasons"],
                   "raw_ids": len(snap.raw_ids), "active_ids": len(snap.active_ids),
                   "quarantined_only_ids": len(snap.quarantined_only_ids),
                   "index_rows": len(snap.docs), "index_ids": len(snap.index_ids)}
    return snap
