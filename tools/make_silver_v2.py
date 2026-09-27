"""Build SILVER-v2: silver queries whose head noun is replaced by a seller synonym.

Tests vocabulary mismatch, limited to the pairs in eval/synonyms/head_synonyms.csv (drafted by
Claude, reviewed and edited by Navid: NOT independent human data). Method: docs/silver_set.md.

Two files, reported separately:
  eval/silver_v2.csv       main: v1 query rules (head + brand + 1-2 attributes), head term replaced
  eval/silver_v2_head.csv  head-only slice: one query per pair, the bare seller term
Both gitignored (catalog extract), with eval/silver_v2_meta.csv. Counts, per-pair coverage and
input hashes go to eval/results/silver_v2_build.json (committed; IDs and counts only).

Head assignment: a row belongs to catalog term T when T (as whole normalized tokens) occurs in the
row's head segment and no longer listed catalog term does. Only the head segment is looked at.

Labels (main): acceptable IDs = token-subset matches of the ORIGINAL query (as in v1) plus those of
the SUBSTITUTED query (catalog rows that really use the seller's wording). 1 -> tier S, 2..20 ->
tier C, more -> rejected. No noise, so a recall drop is the synonym's alone.
Labels (head-only): the class = every in-force ID whose head is assigned to T, plus every ID whose
head STARTS with the seller term (a genuine "گوشی ..." head, not "قاب گوشی").

Index universe: the harness snapshot (src/retrieval/catalog.py: R1-R4, in force on --as-of).
Only IDs with exactly one row in that snapshot are sampled. Nothing in src/ or eval/ may import
this file.

Usage: python tools/make_silver_v2.py [--zip PATH] [--per-pair 10] [--seed 20260927]
"""
import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_search as cs  # noqa: E402
import make_silver as ms  # noqa: E402
from src.retrieval.bm25 import tokenize  # noqa: E402
from src.retrieval.catalog import load_snapshot  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULTS = {"seed": 20260927, "per_pair": 10, "as_of": "1405-07-01", "max_matches": 20}
META_HEADER = ["row", "file", "pair", "catalog_term", "seller_term", "source_id", "tier",
               "n_acceptable", "n_orig_matches", "n_sub_matches"]


class SynonymError(Exception):
    pass


def v2_tokens(text):
    """Token set for matching: the SAME tokenizer that builds v2 queries (bm25.tokenize)."""
    return frozenset(tokenize(text))


def load_pairs(path):
    """Return [(catalog_term, seller_term)] in file order. Lines starting with # are comments."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    rows = list(csv.DictReader(lines))
    if not rows or not {"catalog_term", "seller_term"} <= set(rows[0]):
        raise SynonymError(f"{path}: header must include catalog_term,seller_term")
    pairs, seen = [], set()
    for n, r in enumerate(rows, start=1):
        cat, sel = (r["catalog_term"] or "").strip(), (r["seller_term"] or "").strip()
        if not cat or not sel:
            raise SynonymError(f"{path} pair {n}: empty term")
        if tokenize(cat) == tokenize(sel):
            raise SynonymError(f"{path} pair {n}: {cat!r} and {sel!r} are identical after normalize()")
        if (cat, sel) in seen:
            raise SynonymError(f"{path} pair {n}: duplicate pair")
        seen.add((cat, sel))
        pairs.append((cat, sel))
    return pairs


def find_span(tokens, term):
    """Index of the first whole-token occurrence of `term` (a token list) in `tokens`, or -1."""
    n = len(term)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == term:
            return i
    return -1


def assign(head_tokens, cat_terms):
    """The longest listed catalog term found in the head (ties: first listed), or None."""
    best = None
    for t in cat_terms:
        if find_span(head_tokens, t) >= 0 and (best is None or len(t) > len(best)):
            best = t
    return best


def substitute(head_tokens, cat, sel):
    i = find_span(head_tokens, cat)
    return head_tokens[:i] + sel + head_tokens[i + len(cat):]


def build(zip_path, pairs, seed, per_pair, as_of, max_matches):
    rng = random.Random(f"silver-v2-{seed}")
    snap = load_snapshot(zip_path, as_of)
    per_id = Counter(i for i, _ in snap.docs)
    single = {i for i, c in per_id.items() if c == 1}
    types = {}
    for _, _, rec in cs.iter_rows(zip_path):
        if rec["ID"] in snap.index_ids:
            types[rec["ID"]] = rec["Type"]

    cat_terms = list(dict.fromkeys(tuple(tokenize(c)) for c, _ in pairs))
    sel_terms = {tuple(tokenize(s)) for _, s in pairs}
    members = defaultdict(list)       # catalog term -> [(ID, title)] of sampleable rows, catalog order
    class_ids = defaultdict(set)      # catalog term -> every in-force ID whose head is assigned to it
    starts_with = defaultdict(set)    # seller term -> every in-force ID whose head starts with it
    for id_, title in snap.docs:
        head = tokenize(ms.derive(title, types[id_])[0])
        t = assign(head, [list(c) for c in cat_terms])
        if t is not None:
            class_ids[tuple(t)].add(id_)
            if id_ in single:
                members[tuple(t)].append((id_, title))
        for s in sel_terms:
            if tuple(head[:len(s)]) == s:
                starts_with[s].add(id_)

    # Candidates: per pair, a seeded sample of rows whose head is assigned to its catalog term.
    cands, coverage = [], []
    for p, (cat, sel) in enumerate(pairs):
        ct, st = tuple(tokenize(cat)), list(tokenize(sel))
        pool = members.get(ct, [])
        picked = sorted(rng.sample(range(len(pool)), min(per_pair, len(pool))))
        coverage.append({"pair": p, "catalog_term_rows": len(class_ids.get(ct, ())),
                         "sampleable_rows": len(pool), "seller_head_rows": len(starts_with.get(tuple(st), ())),
                         "sampled": len(picked)})
        for k in picked:
            id_, title = pool[k]
            head, brand, attrs, rules = ms.derive(title, types[id_])
            htok = tokenize(head)
            ladder = []
            for n in ms.attr_ladder(attrs):
                orig, r = ms.build_query(" ".join(htok), brand, attrs, n, rules)
                sub, _ = ms.build_query(" ".join(substitute(htok, list(ct), st)), brand, attrs, n, rules)
                ladder.append((orig, sub, r))
            cands.append((p, id_, ladder))

    queries = {q: v2_tokens(q) for _, _, ladder in cands for o, s, _ in ladder for q in (o, s)}
    matches = ms.match_ids(lambda: ({"ID": i, "DescriptionOfID": t} for i, t in snap.docs),
                           queries, max_matches + 1, tokenizer=v2_tokens)

    main_rows, meta, used = [], [], set()
    stats = defaultdict(Counter)
    for p, id_, ladder in cands:
        cat, sel = pairs[p]
        choice = None
        for orig, sub, rules in ladder:
            if id_ not in matches[orig] and len(matches[orig]) <= max_matches:
                raise AssertionError(f"original query does not match its own title: ID {id_}")
            acc = matches[orig] | matches[sub]
            choice = (orig, sub, rules, acc)
            if len(acc) == 1:
                break
        orig, sub, rules, acc = choice
        if len(acc) > max_matches or len(matches[orig]) > max_matches or len(matches[sub]) > max_matches:
            stats[p]["rejected_over_max"] += 1
            continue
        if sub in used:
            stats[p]["rejected_duplicate_query"] += 1
            continue
        used.add(sub)
        tier = "S" if len(acc) == 1 else "C"
        stats[p][tier] += 1
        stats[p]["sub_only_ids"] += len(matches[sub] - matches[orig])
        notes = f"silver-v2|pair:{p}|{'+'.join(rules + ['synonym_head'])}|{seed}"
        main_rows.append([sub, ";".join(sorted(acc)), tier, notes])
        meta.append([len(main_rows), "silver_v2", p, cat, sel, id_, tier, len(acc),
                     len(matches[orig]), len(matches[sub])])

    head_rows = []
    for p, (cat, sel) in enumerate(pairs):
        ct, st = tuple(tokenize(cat)), tuple(tokenize(sel))
        if not class_ids.get(ct):
            continue                                  # zero coverage: listed in the report
        acc = class_ids[ct] | starts_with.get(st, set())
        tier = "S" if len(acc) == 1 else "C"
        head_rows.append([" ".join(st), ";".join(sorted(acc)), tier,
                          f"silver-v2-head|pair:{p}|head_only|{seed}"])
        meta.append([len(head_rows), "silver_v2_head", p, cat, sel, "", tier, len(acc),
                     len(class_ids[ct]), len(starts_with.get(st, ()))])

    for c in coverage:
        s = stats[c["pair"]]
        c.update({"emitted_S": s["S"], "emitted_C": s["C"], "rejected_over_max": s["rejected_over_max"],
                  "rejected_duplicate_query": s["rejected_duplicate_query"],
                  "sub_only_ids": s["sub_only_ids"]})
    report = {
        "params": {"seed": seed, "per_pair": per_pair, "as_of": as_of, "max_matches": max_matches},
        "snapshot": snap.describe(),
        "pairs": len(pairs),
        "zero_coverage_pairs": [c["pair"] for c in coverage if c["catalog_term_rows"] == 0],
        "coverage": coverage,
        "totals": {"main_rows": len(main_rows), "main_S": sum(r[2] == "S" for r in main_rows),
                   "main_C": sum(r[2] == "C" for r in main_rows), "head_only_rows": len(head_rows),
                   "rejected_over_max": sum(c["rejected_over_max"] for c in coverage),
                   "rejected_duplicate_query": sum(c["rejected_duplicate_query"] for c in coverage)},
    }
    return main_rows, head_rows, meta, report


def main(argv=None):
    p = argparse.ArgumentParser(description="Build SILVER-v2 (synonym head, optimistic).")
    p.add_argument("--zip", type=Path, help="catalog zip (default: the one zip in data/catalog/)")
    p.add_argument("--pairs", type=Path, default=REPO / "eval" / "synonyms" / "head_synonyms.csv")
    p.add_argument("--out", type=Path, default=REPO / "eval" / "silver_v2.csv")
    p.add_argument("--out-head", type=Path, default=REPO / "eval" / "silver_v2_head.csv")
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_v2_meta.csv")
    p.add_argument("--report", type=Path, default=REPO / "eval" / "results" / "silver_v2_build.json")
    p.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    p.add_argument("--per-pair", type=int, default=DEFAULTS["per_pair"])
    p.add_argument("--as-of", default=DEFAULTS["as_of"])
    p.add_argument("--max-matches", type=int, default=DEFAULTS["max_matches"])
    a = p.parse_args(argv)
    if "golden" in a.out.name or "golden" in a.out_head.name:
        p.error("refusing to write a silver set to a golden file")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    zip_path = a.zip or cs.default_zip()
    pairs = load_pairs(a.pairs)
    main_rows, head_rows, meta, report = build(zip_path, pairs, a.seed, a.per_pair, a.as_of,
                                               a.max_matches)
    report = {"catalog_zip": Path(zip_path).name, "catalog_sha256": ms.sha256(zip_path),
              "pairs_file": str(a.pairs.relative_to(REPO)) if a.pairs.is_relative_to(REPO) else str(a.pairs),
              "pairs_sha256": ms.sha256(a.pairs), **report}
    ms.write_csv(a.out, ms.GOLDEN_HEADER, main_rows)
    ms.write_csv(a.out_head, ms.GOLDEN_HEADER, head_rows)
    ms.write_csv(a.meta, META_HEADER, meta)  # `row` = data row number within its own file
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out} ({len(main_rows)} rows), {a.out_head} ({len(head_rows)} rows), {a.meta}, {a.report}")
    print(json.dumps(report["totals"]), "zero coverage pairs:", report["zero_coverage_pairs"])
    return report


if __name__ == "__main__":
    main()
