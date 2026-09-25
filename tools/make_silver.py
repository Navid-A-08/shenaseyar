"""Build the machine-generated SILVER set (eval/silver.csv). It is NOT the golden set.

Queries are derived from catalog titles by fixed rules, so the set favours lexical retrieval and
its scores are OPTIMISTIC. It never gates Phase 1. Method and caveats: docs/silver_set.md.

Nothing in src/ or eval/ may import this file. eval/silver.csv and eval/silver_meta.csv are
gitignored (catalog extract); only eval/results/silver_build.json (counts, no text) is committed.

Usage:
  python tools/make_silver.py [--zip PATH] [--seed 20260925] [--n 300] [--as-of 1405-07-01]

Deterministic: the same zip (check the SHA-256) and the same parameters give byte-identical output.
"""
import argparse
import csv
import hashlib
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_search as cs  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
GOLDEN_HEADER = ["query_text", "expected_sstid", "tier", "notes"]  # = eval/run_eval.GOLDEN_HEADER
META_HEADER = ["row", "source_id", "type", "vat", "length", "n_matches", "tier"]
DEFAULTS = {"seed": 20260925, "n": 300, "as_of": "1405-07-01", "noise_frac": 0.30,
            "floor": 5, "max_matches": 20, "pool_factor": 10}

ZWNJ = "‌"
_TOKEN_SPLIT = re.compile(r"[\s،,/\\()\[\]:;«»\"']+")
_ID13 = re.compile(r"[0-9]{13}")

ADMIN_MARKER = "سازمان امور مالیاتی"
MFR_PREFIXES = ("سازنده", "تولید کننده", "تولیدکننده", "شرکت")
COUNTRY_PREFIXES = ("کشور", "ساخت ")
COUNTRIES = frozenset({
    "ایران", "چین", "ژاپن", "کره جنوبی", "کره", "المان", "آلمان", "ایتالیا", "ترکیه", "هند",
    "امریکا", "آمریکا", "ایالات متحده", "ایالات متحده امریکا", "فرانسه", "انگلستان", "تایوان",
    "امارات", "امارات متحده عربی", "روسیه", "اسپانیا", "سوید", "سوئد", "سوییس", "سوئیس", "هلند",
    "بلژیک", "اتریش", "لهستان", "چک", "مالزی", "تایلند", "ویتنام", "اندونزی", "سنگاپور", "برزیل",
    "کانادا", "مکزیک", "دانمارک", "فنلاند", "اوکراین", "بلاروس", "پاکستان", "عراق", "عمان",
    "قطر", "مصر", "استرالیا", "هنگ کنگ", "اسلوونی", "مجارستان", "رومانی", "پرتغال", "ارمنستان",
})
PACK_MARKERS = ("بسته بندی", "بسته‌بندی")
PAPERWEIGHT_MARKERS = ("g\\m^2", "g/m^2", "گرم بر متر مربع")
PARTNO_PREFIXES = ("شماره فنی",)
BRAND_PREFIXES = ("برند ", "نام تجارتی ")
# Catalog placeholders ("brand: none", "model: none"); a seller never types these.
PLACEHOLDER = re.compile(r"(?:(?:برند|مدل|نام تجارتی)\s+)?فاقد\s+(?:نام تجارتی|برند|مدل)")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9 .&+\-]*")
MAX_ATTR_WORDS = 4
NOISE_RULES = ("noise_arabic_yk", "noise_no_zwnj", "noise_fa_digits", "noise_typo")


# --- query derivation ----------------------------------------------------------------------

def _add(rules, rule):
    if rule not in rules:
        rules.append(rule)


def derive(title, type_):
    """Split a catalog title into (head, brand, attrs, rules). attrs keep catalog order."""
    rules = []
    t = " ".join(title.split())
    if ADMIN_MARKER in t:
        t = t[:t.index(ADMIN_MARKER)].rstrip(" /")
        _add(rules, "drop_admin")
    if type_.endswith("خدمت"):
        segs = t.split("/")
    else:
        m = re.search(r"/\s*شرکت[^/]*$", t)
        if m:
            t = t[:m.start()]
            _add(rules, "drop_mfr")
        segs = t.split("،")
        if len(segs) > 1:
            _add(rules, "drop_commas")
    segs = [s.strip() for s in segs if s.strip()]
    head, rest = segs[0], segs[1:]
    brand, attrs = None, []
    for i, s in enumerate(rest):
        if PLACEHOLDER.fullmatch(s.replace(ZWNJ, " ")):
            _add(rules, "drop_placeholder")
        elif s.startswith(MFR_PREFIXES):
            _add(rules, "drop_mfr")
        elif s in COUNTRIES or s.startswith(COUNTRY_PREFIXES):
            _add(rules, "drop_country")
        elif any(m in s for m in PACK_MARKERS):
            _add(rules, "drop_pack")
        elif any(m in s for m in PAPERWEIGHT_MARKERS):
            _add(rules, "drop_paperweight")
        elif s.startswith(PARTNO_PREFIXES):
            _add(rules, "drop_partno")
        elif brand is None and s.startswith(BRAND_PREFIXES):
            brand = s.split(" ", 2)[-1] if s.startswith("نام تجارتی ") else s.split(" ", 1)[1]
            _add(rules, "brand")
        elif brand is None and i < 2 and LATIN.fullmatch(s):
            brand = s
            _add(rules, "brand_latin")
        else:
            attrs.append(s)
    return head, brand, attrs, rules


def build_query(head, brand, attrs, n_attr, rules):
    """Join head, brand and the first n_attr attributes. Returns (query, rules fired)."""
    rules = ["head"] + list(rules)
    parts = [head] + ([brand] if brand else [])
    for j, a in enumerate(attrs[:n_attr], start=1):
        words = a.split()
        if len(words) > MAX_ATTR_WORDS:
            a = " ".join(words[:MAX_ATTR_WORDS])
            _add(rules, "trim_attr")
        parts.append(a)
        _add(rules, f"attr{j}")
    query = " ".join(parts)
    if "،" in query:
        query = query.replace("،", " ")
        _add(rules, "drop_commas")
    return " ".join(query.split()), rules


def attr_ladder(attrs):
    """Attribute counts to try, most general first: seller queries keep one or two attributes."""
    return [1, 2] if len(attrs) >= 2 else [len(attrs)]


def tokens(text):
    t = cs.fold(text).casefold().replace(ZWNJ, "")
    return frozenset(x for x in _TOKEN_SPLIT.split(t) if x)


# --- noise ---------------------------------------------------------------------------------

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _typo_words(q):
    return [i for i, w in enumerate(q.split(" ")) if len(w) >= 4 and sum(c.isalpha() for c in w) >= 4]


def eligible_noise(q):
    rules = []
    if "ی" in q or "ک" in q:
        rules.append("noise_arabic_yk")
    if ZWNJ in q:
        rules.append("noise_no_zwnj")
    if re.search(r"[0-9]", q):
        rules.append("noise_fa_digits")
    if _typo_words(q):
        rules.append("noise_typo")
    return rules


def apply_noise(q, rule, rng):
    if rule == "noise_arabic_yk":
        return q.replace("ی", "ي").replace("ک", "ك")
    if rule == "noise_no_zwnj":
        return q.replace(ZWNJ, "")
    if rule == "noise_fa_digits":
        return q.translate(_FA_DIGITS)
    words = q.split(" ")
    i = rng.choice(_typo_words(q))
    w = words[i]
    pos = rng.randrange(len(w) - 1)
    if rng.random() < 0.5 and w[pos] != w[pos + 1]:
        w = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2:]   # swap adjacent letters
    else:
        w = w[:pos] + w[pos + 1:]                         # drop one letter
    words[i] = w
    return " ".join(words)


# --- sampling ------------------------------------------------------------------------------

def allocate(sizes, n, floor):
    """Quota per cell: proportional to size, floor of min(floor, size), largest remainder.

    Sums to min(n, total size). Raises if the floors alone exceed n.
    """
    sizes = {c: s for c, s in sizes.items() if s > 0}
    total = min(n, sum(sizes.values()))
    if sum(min(floor, s) for s in sizes.values()) > total:
        raise ValueError(f"n={n} is too small for a floor of {floor} in {len(sizes)} cells")
    fixed = {}
    while True:
        free = {c: s for c, s in sizes.items() if c not in fixed}
        budget = total - sum(fixed.values())
        denom = sum(free.values())
        shares = {c: budget * s / denom for c, s in free.items()} if denom else {}
        low = [c for c in free if shares[c] < min(floor, sizes[c])]
        if not low:
            break
        for c in low:
            fixed[c] = min(floor, sizes[c])
    alloc = {c: int(v) for c, v in shares.items()}
    left = budget - sum(alloc.values())
    for c in sorted(shares, key=lambda c: (-(shares[c] - int(shares[c])), c))[:left]:
        alloc[c] += 1
    alloc.update(fixed)
    return dict(sorted(alloc.items()))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(zip_path, seed, n, as_of, noise_frac, floor, max_matches, pool_factor):
    """Return (silver_rows, meta_rows, report). Streams the zip five times; nothing is extracted."""
    cs.check_date(as_of, "--as-of")
    rng = random.Random(f"silver-{seed}")

    def in_force_rows():
        for i, line, rec in cs.iter_rows(zip_path):
            if cs.in_force(rec, as_of, f"row {i} (CSV line {line}, ID {rec['ID']})"):
                yield rec

    # Pass 1: in-force rows per ID. IDs with two rows in force are AMBIGUOUS and never sampled.
    per_id = Counter(rec["ID"] for rec in in_force_rows())
    ambiguous = {i for i, c in per_id.items() if c > 1}
    bad_ids = {i for i in per_id if not _ID13.fullmatch(i)}

    def eligible(rec):
        return rec["ID"] not in ambiguous and rec["ID"] not in bad_ids

    # Pass 2: title lengths -> median -> cells (Type x short/long).
    lengths = defaultdict(list)
    for rec in in_force_rows():
        if eligible(rec):
            lengths[rec["Type"]].append(len(rec["DescriptionOfID"]))
    median = statistics.median(x for v in lengths.values() for x in v)

    def cell(rec):
        return (rec["Type"], "short" if len(rec["DescriptionOfID"]) < median else "long")

    sizes = Counter()
    for t, v in lengths.items():
        short = sum(1 for x in v if x < median)
        sizes[(t, "short")] += short
        sizes[(t, "long")] += len(v) - short
    quota = allocate(sizes, n, floor)

    # Pass 3: a seeded random pool of candidates per cell, in random order.
    picks = {c: rng.sample(range(sizes[c]), min(sizes[c], q * pool_factor)) for c, q in quota.items()}
    order = {c: {ordinal: k for k, ordinal in enumerate(p)} for c, p in picks.items()}
    seen, pool = Counter(), defaultdict(dict)
    for rec in in_force_rows():
        if not eligible(rec):
            continue
        c = cell(rec)
        k = order.get(c, {}).get(seen[c])
        seen[c] += 1
        if k is not None:
            pool[c][k] = rec
    pool = {c: [recs[k] for k in sorted(recs)] for c, recs in pool.items()}

    # Candidate queries: for each candidate, the attribute ladder (1 attr, then 2).
    cand = {}
    for c, recs in pool.items():
        for rec in recs:
            head, brand, attrs, rules = derive(rec["DescriptionOfID"], rec["Type"])
            cand[rec["ID"]] = [build_query(head, brand, attrs, k, rules) for k in attr_ladder(attrs)]
    queries = {q: tokens(q) for ladder in cand.values() for q, _ in ladder}

    # Pass 4: document frequency of query tokens, to index each query on its rarest token.
    vocab = set().union(*queries.values()) if queries else set()
    df = Counter()
    for rec in in_force_rows():
        if rec["ID"] not in bad_ids:
            df.update(tokens(rec["DescriptionOfID"]) & vocab)
    by_key = defaultdict(list)
    for q, toks in queries.items():
        if toks:
            by_key[min(toks, key=lambda t: (df[t], t))].append(q)

    # Pass 5: which in-force IDs contain every token of each query (capped at max_matches + 1).
    matches = defaultdict(set)
    for rec in in_force_rows():
        if rec["ID"] in bad_ids:
            continue
        toks = tokens(rec["DescriptionOfID"])
        for key in toks & by_key.keys():
            for q in by_key[key]:
                if len(matches[q]) <= max_matches and queries[q] <= toks:
                    matches[q].add(rec["ID"])

    # Emit per cell until its quota is met: unique -> S, <= max_matches -> C, else reject.
    rows, meta, cells, used_queries = [], [], [], set()
    for c, q_c in quota.items():
        stats = Counter()
        for rec in pool.get(c, []):
            if stats["S"] + stats["C"] >= q_c:
                break
            stats["tried"] += 1
            choice = None
            for q, rules in cand[rec["ID"]]:
                if rec["ID"] not in matches[q] and len(matches[q]) <= max_matches:
                    raise AssertionError(f"query does not match its own title: ID {rec['ID']}")
                choice = (q, rules, matches[q])
                if len(matches[q]) == 1:
                    break
            q, rules, ids = choice
            if q in used_queries:
                stats["rejected_duplicate_query"] += 1
                continue
            if len(ids) > max_matches:
                stats["rejected_over_max"] += 1
                continue
            used_queries.add(q)
            tier = "S" if len(ids) == 1 else "C"
            stats[tier] += 1
            rows.append({"query_text": q, "ids": sorted(ids), "tier": tier, "rules": rules})
            meta.append({"source_id": rec["ID"], "type": rec["Type"], "vat": rec["Vat"],
                         "length": c[1], "n_matches": len(ids), "tier": tier})
        cells.append({"type": c[0], "length": c[1], "eligible_rows": sizes[c], "quota": q_c,
                      "pool": len(pool.get(c, [])), "tried": stats["tried"],
                      "emitted_S": stats["S"], "emitted_C": stats["C"],
                      "rejected_over_max": stats["rejected_over_max"],
                      "rejected_duplicate_query": stats["rejected_duplicate_query"],
                      "shortfall": q_c - stats["S"] - stats["C"]})

    # Noise: exactly round(noise_frac * rows) rows, among rows where some noise rule applies.
    noisy = [i for i, r in enumerate(rows) if eligible_noise(r["query_text"])]
    k = min(len(noisy), round(noise_frac * len(rows)))
    noise_count = Counter()
    for i in sorted(rng.sample(noisy, k)):
        r = rows[i]
        rule = rng.choice(eligible_noise(r["query_text"]))
        r["query_text"] = apply_noise(r["query_text"], rule, rng)
        r["rules"] = r["rules"] + [rule]
        noise_count[rule] += 1

    silver = []
    for r in rows:
        mark = "silver|rule-derived|" + ("" if r["tier"] == "S" else "non-unique|")
        silver.append([r["query_text"], ";".join(r["ids"]), r["tier"],
                       f"{mark}{'+'.join(r['rules'])}|{seed}"])
    meta_rows = [[i, m["source_id"], m["type"], m["vat"], m["length"], m["n_matches"], m["tier"]]
                 for i, m in enumerate(meta, start=1)]
    report = {
        "catalog_zip": Path(zip_path).name,
        "params": {"seed": seed, "n": n, "as_of": as_of, "noise_frac": noise_frac, "floor": floor,
                   "max_matches": max_matches, "pool_factor": pool_factor},
        "median_title_length": median,
        "excluded_ids": {"ambiguous_two_in_force": len(ambiguous), "not_13_digits": len(bad_ids)},
        "cells": cells,
        "totals": {"rows": len(rows), "tier_S": sum(r["tier"] == "S" for r in rows),
                   "tier_C": sum(r["tier"] == "C" for r in rows),
                   "rejected_over_max": sum(c["rejected_over_max"] for c in cells),
                   "rejected_duplicate_query": sum(c["rejected_duplicate_query"] for c in cells),
                   "shortfall": sum(c["shortfall"] for c in cells)},
        "noise": {"rows": k, **{r: noise_count[r] for r in NOISE_RULES}},
    }
    return silver, meta_rows, report


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def markdown_table(report):
    out = ["| Type | Length | Eligible rows | Quota | Tried | S | C | Rejected >max | Rejected dup | Shortfall |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in report["cells"]:
        out.append(f"| {c['type']} | {c['length']} | {c['eligible_rows']:,} | {c['quota']} | {c['tried']} | "
                   f"{c['emitted_S']} | {c['emitted_C']} | {c['rejected_over_max']} | "
                   f"{c['rejected_duplicate_query']} | {c['shortfall']} |")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the SILVER set (optimistic, machine-generated).")
    p.add_argument("--zip", type=Path, help="catalog zip (default: the one zip in data/catalog/)")
    p.add_argument("--out", type=Path, default=REPO / "eval" / "silver.csv")
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_meta.csv")
    p.add_argument("--report", type=Path, default=REPO / "eval" / "results" / "silver_build.json")
    p.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    p.add_argument("--n", type=int, default=DEFAULTS["n"])
    p.add_argument("--as-of", default=DEFAULTS["as_of"])
    p.add_argument("--noise-frac", type=float, default=DEFAULTS["noise_frac"])
    p.add_argument("--floor", type=int, default=DEFAULTS["floor"])
    p.add_argument("--max-matches", type=int, default=DEFAULTS["max_matches"])
    p.add_argument("--pool-factor", type=int, default=DEFAULTS["pool_factor"])
    a = p.parse_args(argv)
    if a.out.name == "golden.csv":
        p.error("refusing to write the silver set to golden.csv")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    zip_path = a.zip or cs.default_zip()
    silver, meta, report = build(zip_path, a.seed, a.n, a.as_of, a.noise_frac, a.floor,
                                 a.max_matches, a.pool_factor)
    report["catalog_sha256"] = sha256(zip_path)
    write_csv(a.out, GOLDEN_HEADER, silver)
    write_csv(a.meta, META_HEADER, meta)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"catalog: {report['catalog_zip']}  sha256: {report['catalog_sha256']}")
    print(f"wrote {a.out} ({len(silver)} rows), {a.meta}, {a.report}")
    print(json.dumps(report["totals"]), json.dumps(report["noise"]))
    print(markdown_table(report))
    return report


if __name__ == "__main__":
    main()
