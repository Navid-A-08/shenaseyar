"""Find catalog rows by hand: a labeling aid for the golden set. NOT part of the pipeline.

Nothing in src/ or eval/ may import this file (tests/test_catalog_search.py enforces it).
It must stay dumb: plain substring match, no ranking, no fuzzy matching, no embeddings.
Anything smarter would bias the hand labels in eval/golden.csv.

Usage:
  python tools/catalog_search.py "شرح" [--limit 20] [--zip PATH] [--as-of 1405-07-03] [--literal]
  python tools/catalog_search.py --ids 2809734954277 2719635367398 [--zip PATH]

Matching (title mode): the query and each DescriptionOfID are compared after the same steps:
  1. fold Arabic ي/ك to Persian ی/ک and Persian/Arabic-Indic digits to Latin (off with --literal)
  2. casefold
  3. remove all whitespace, including ZWNJ (U+200C)
Then it's a plain substring test. Printed titles are always the raw catalog text.

in_force: RunDate <= as_of <= ExpirationDate (empty = open-ended). ExpirationDate is inclusive
(docs/data_dictionary.md §9). as_of defaults to today's Jalali date. Dates are compared as
strings, so every compared date must be zero-padded YYYY-MM-DD; any other value is an error.
Vat is printed raw; what it means is still TODO(legal).

The catalog zip is streamed row by row; nothing is extracted or fully loaded.
"""
import argparse
import csv
import io
import re
import sys
import zipfile
from pathlib import Path

import jdatetime

REPO = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO / "data" / "catalog"
HEADER = ["ID", "DescriptionOfID", "Vat", "Taxable", "RunDate", "ExpirationDate",
          "CreateDate", "LastEditDate", "Type", "PricingDescription"]
OUT_COLUMNS = ["ID", "title", "Vat", "Taxable", "Type", "valid_from", "valid_to", "in_force"]
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # [0-9], not \d: \d also matches Persian digits
ZWNJ = "‌"
_FOLD = str.maketrans("يك۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "یک01234567890123456789")


class CatalogError(Exception):
    pass


def fold(text):
    return text.translate(_FOLD)


def normalize(text, literal=False):
    if not literal:
        text = fold(text)
    return "".join(ch for ch in text.casefold() if not ch.isspace() and ch != ZWNJ)


def check_date(value, where):
    if not DATE.fullmatch(value):
        raise CatalogError(f"{where}: date {value!r} is not zero-padded YYYY-MM-DD")
    return value


def in_force(rec, as_of, where):
    start = check_date(rec["RunDate"], f"{where} RunDate")
    end = rec["ExpirationDate"]
    if end:
        check_date(end, f"{where} ExpirationDate")
    return start <= as_of and (not end or as_of <= end)


def default_zip():
    zips = sorted(CATALOG_DIR.glob("*.zip"))
    if len(zips) != 1:
        raise CatalogError(f"found {len(zips)} zip files in {CATALOG_DIR}; pass --zip PATH")
    return zips[0]


def iter_rows(zip_path):
    """Yield (row_number, csv_line, record) for each data row, streamed from the zip."""
    with zipfile.ZipFile(zip_path) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise CatalogError(f"{zip_path}: expected exactly one .csv entry, found {len(csvs)}")
        with zf.open(csvs[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            header = next(reader, None)
            if header != HEADER:
                raise CatalogError(f"{zip_path}: unexpected header {header}")
            for n, row in enumerate(reader, start=1):
                if len(row) != len(HEADER):
                    raise CatalogError(f"row {n} (CSV line {reader.line_num}): "
                                       f"{len(row)} fields, expected {len(HEADER)}")
                yield n, reader.line_num, dict(zip(HEADER, row))


def output_row(rec, force):
    title = " ".join(rec["DescriptionOfID"].split())  # keep the TSV one line per row
    return [rec["ID"], title, rec["Vat"], rec["Taxable"], rec["Type"],
            rec["RunDate"], rec["ExpirationDate"], "yes" if force else "no"]


def search_titles(zip_path, query, limit, as_of, literal=False):
    """Return (rows, scanned, hit_limit). Stops reading at the limit."""
    needle = normalize(query, literal)
    rows, scanned = [], 0
    for n, line, rec in iter_rows(zip_path):
        scanned = n
        if needle in normalize(rec["DescriptionOfID"], literal):
            where = f"row {n} (CSV line {line}, ID {rec['ID']})"
            rows.append(output_row(rec, in_force(rec, as_of, where)))
            if len(rows) >= limit:
                return rows, scanned, True
    return rows, scanned, False


def lookup_ids(zip_path, ids, as_of):
    """Return (rows, any_in_force) where any_in_force maps each wanted ID to True/False/None.

    None means the ID was not found. Scans the whole file: history rows are not adjacent.
    """
    wanted = set(ids)
    rows = []
    any_force = dict.fromkeys(ids)
    for n, line, rec in iter_rows(zip_path):
        if rec["ID"] in wanted:
            force = in_force(rec, as_of, f"row {n} (CSV line {line}, ID {rec['ID']})")
            rows.append(output_row(rec, force))
            any_force[rec["ID"]] = bool(any_force[rec["ID"]]) or force
    return rows, any_force


def _print_tsv(rows):
    print("\t".join(OUT_COLUMNS))
    for r in rows:
        print("\t".join(r))


def main(argv=None):
    p = argparse.ArgumentParser(description="Dumb catalog lookup for hand labeling.")
    p.add_argument("query", nargs="?", help="substring to find in the title")
    p.add_argument("--ids", nargs="+", metavar="ID", help="exact ID lookup instead of title search")
    p.add_argument("--limit", type=int, default=20, help="max rows in title mode (default 20)")
    p.add_argument("--zip", type=Path, help="catalog zip (default: the one zip in data/catalog/)")
    p.add_argument("--as-of", help="Jalali date YYYY-MM-DD for in_force (default: today)")
    p.add_argument("--literal", action="store_true", help="no ي/ك or digit folding")
    args = p.parse_args(argv)
    if (args.query is None) == (args.ids is None):
        p.error("give either a query or --ids, not both")
    if args.limit < 1:
        p.error("--limit must be at least 1")

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        as_of = check_date(args.as_of or jdatetime.date.today().strftime("%Y-%m-%d"), "--as-of")
        zip_path = args.zip or default_zip()
        print(f"catalog: {zip_path}  as_of: {as_of}", file=sys.stderr)

        if args.ids:
            ids = args.ids if args.literal else [fold(i) for i in args.ids]
            if ids != args.ids:
                print(f"note: folded IDs to {' '.join(ids)} (use --literal to turn off)",
                      file=sys.stderr)
            rows, any_force = lookup_ids(zip_path, ids, as_of)
            _print_tsv(rows)
            print("\nID\tany_row_in_force")
            for i, f in any_force.items():
                print(f"{i}\t{'NOT FOUND' if f is None else 'yes' if f else 'no'}")
        else:
            if not args.literal and fold(args.query) != args.query:
                print(f"note: folded query to {fold(args.query)!r} (use --literal to turn off)",
                      file=sys.stderr)
            rows, scanned, hit_limit = search_titles(zip_path, args.query, args.limit, as_of,
                                                     args.literal)
            _print_tsv(rows)
            stop = f"stopped at --limit {args.limit}" if hit_limit else "full scan"
            print(f"{len(rows)} match(es), {scanned} rows scanned, {stop}", file=sys.stderr)
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
