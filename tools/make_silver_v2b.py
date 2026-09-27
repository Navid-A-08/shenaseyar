"""Build SILVER-v2b: the query-detail ladder over the synonym pairs. Method: docs/silver_set.md.

For each pair (eval/synonyms/head_synonyms.csv), up to K source rows whose head segment is assigned
to the catalog term, picked round-robin across distinct heads. From each source row, one query
per detail level, with the catalog term in the head replaced by the seller term:
  L0  the bare seller term (one per pair, not per row)
  L1  the full substituted head
  L2  L1 + first attribute
  L3  L1 + first two attributes
  L4  L1 + brand + first two attributes (only when the row has a brand)
Attributes are cut to 4 words, as in v1. A level that has nothing new for a row is skipped.
Identical queries within a pair and level are kept once.

Label (every level, no cap). An in-force ID is acceptable when BOTH hold:
  head:  its head segment contains the source row's original head and is assigned to the same
         catalog term (longest listed term wins), OR its head starts with the substituted head.
         --class-rule prefix: "contains" becomes "starts with" on the catalog side too, which
         keeps rows like کارتریج چاپگر or پخش خودرو out of the class (measured: 26.9% of
         "contains" class rows do not start with the term).
         For L0: its head is assigned to the catalog term, OR its head starts with the seller term.
  text:  its title contains every brand/attribute token the level adds.
Exactly one ID -> tier S, otherwise tier C.

Re-runnable as the list grows: a pair's ID is a hash of its normalized terms (not its row
position), and each pair samples with its own seed, so adding pairs never changes the queries of
existing pairs. Pairs whose note contains `direction=reversed` are tagged and reported apart.

Outputs: eval/silver_v2b.csv + eval/silver_v2b_meta.csv (gitignored, catalog extract) and
eval/results/silver_v2b_build.json (committed; counts, pair IDs, list terms, hashes).
Nothing in src/ or eval/ may import this file.
"""
import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_search as cs  # noqa: E402
import make_silver as ms  # noqa: E402
import make_silver_v2 as v2  # noqa: E402
from src.retrieval.bm25 import tokenize  # noqa: E402
from src.retrieval.catalog import load_snapshot  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_DESC = {"L0": "bare seller term", "L1": "substituted head", "L2": "head + 1 attribute",
              "L3": "head + 2 attributes", "L4": "head + brand + 2 attributes"}
DEFAULTS = {"seed": 20260928, "k_rows": 10, "as_of": "1405-07-01", "class_rule": "contains"}
CLASS_RULES = ("contains", "prefix")
META_HEADER = ["row", "pair_id", "level", "direction", "source_id", "tier", "n_label"]


def pair_id(cat, sel):
    key = " ".join(tokenize(cat)) + "\x1f" + " ".join(tokenize(sel))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def direction(note):
    return "reversed" if "direction=reversed" in note.replace(" ", "") else "original"


def _trim(attr):
    return tokenize(" ".join(attr.split()[:ms.MAX_ATTR_WORDS]))


def ladder(sub_head, brand, attrs):
    """{level: (query tokens, tokens the text condition requires)} for one source row (L1-L4)."""
    a = [_trim(x) for x in attrs[:2]]
    b = tokenize(brand) if brand else []
    out = {"L1": (sub_head, [])}
    if len(a) >= 1:
        out["L2"] = (sub_head + a[0], a[0])
    if len(a) >= 2:
        out["L3"] = (sub_head + a[0] + a[1], a[0] + a[1])
    if b:
        extra = b + [t for x in a for t in x]
        out["L4"] = (sub_head + extra, extra)
    return out


def _head_has(head, term, rule):
    """contains: term anywhere in the head (whole tokens). prefix: the head starts with term."""
    return head[:len(term)] == term if rule == "prefix" else v2.find_span(head, term) >= 0


def assign(head, cat_terms, rule="contains"):
    """The longest listed catalog term the head has under `rule` (ties: first listed), or None."""
    best = None
    for t in cat_terms:
        if _head_has(head, t, rule) and (best is None or len(t) > len(best)):
            best = t
    return best


class Index:
    """Head tokens and head assignment for every in-force row of the snapshot."""

    def __init__(self, snap, types, cat_terms, class_rule="contains"):
        self.docs = snap.docs
        self.heads, self.assigned = [], []
        self.by_cat, self.by_first = defaultdict(list), defaultdict(list)
        self._titles = {}
        self.class_rule = class_rule
        for n, (id_, title) in enumerate(snap.docs):
            h = tuple(tokenize(ms.derive(title, types[id_])[0]))
            a = assign(list(h), cat_terms, class_rule)
            a = tuple(a) if a is not None else None
            self.heads.append(h)
            self.assigned.append(a)
            if a is not None:
                self.by_cat[a].append(n)
            if h:
                self.by_first[h[0]].append(n)

    def title_tokens(self, n):
        if n not in self._titles:
            self._titles[n] = frozenset(tokenize(self.docs[n][1]))
        return self._titles[n]

    def starts_with(self, prefix):
        prefix = tuple(prefix)
        if not prefix:
            return []
        return [n for n in self.by_first.get(prefix[0], ()) if self.heads[n][:len(prefix)] == prefix]

    def label(self, cat, orig_head, sub_head, extra):
        """Acceptable IDs (see module docstring). orig_head=None means L0 (whole class)."""
        if orig_head is None:
            docs = set(self.by_cat.get(cat, ()))
        else:
            docs = {n for n in self.by_cat.get(cat, ())
                    if _head_has(list(self.heads[n]), list(orig_head), self.class_rule)}
        docs |= set(self.starts_with(sub_head))
        need = set(extra)
        return {self.docs[n][0] for n in docs if need <= self.title_tokens(n)}


def pick_rows(rows_by_head, k, rng):
    """Round-robin across distinct heads (seeded order), up to k rows."""
    heads = sorted(rows_by_head)
    rng.shuffle(heads)
    queues = []
    for h in heads:
        q = sorted(rows_by_head[h])
        rng.shuffle(q)
        queues.append(q)
    out = []
    while len(out) < k and any(queues):
        for q in queues:
            if q and len(out) < k:
                out.append(q.pop())
    return out


def build(zip_path, pairs, seed, k_rows, as_of, class_rule="contains"):
    snap = load_snapshot(zip_path, as_of)
    per_id = Counter(i for i, _ in snap.docs)
    types = {}
    for _, _, rec in cs.iter_rows(zip_path):
        if rec["ID"] in snap.index_ids:
            types[rec["ID"]] = rec["Type"]
    cat_terms = [list(t) for t in dict.fromkeys(tuple(tokenize(p["catalog_term"])) for p in pairs)]
    idx = Index(snap, types, cat_terms, class_rule)

    rows, meta, report_pairs = [], [], []
    for p in pairs:
        cat, sel = tuple(tokenize(p["catalog_term"])), list(tokenize(p["seller_term"]))
        pid, dirn = pair_id(p["catalog_term"], p["seller_term"]), direction(p["note"])
        rng = random.Random(f"silver-v2b-{seed}-{pid}")
        class_docs = idx.by_cat.get(cat, [])
        by_head = defaultdict(list)
        for n in class_docs:
            if per_id[snap.docs[n][0]] == 1:
                by_head[idx.heads[n]].append(n)
        info = {"pair_id": pid, "catalog_term": p["catalog_term"], "seller_term": p["seller_term"],
                "direction": dirn, "class_rows": len(class_docs),
                "seller_head_rows": len(idx.starts_with(sel)), "distinct_heads": len(by_head),
                "sampled_rows": 0, "queries": dict.fromkeys(LEVELS, 0),
                "duplicates": dict.fromkeys(LEVELS, 0)}
        report_pairs.append(info)
        if not class_docs:
            continue
        seen = defaultdict(set)

        def emit(level, q_tokens, label, source_id):
            q = " ".join(q_tokens)
            if q in seen[level]:
                info["duplicates"][level] += 1
                return
            seen[level].add(q)
            if source_id and source_id not in label:
                raise AssertionError(f"source row not in its own label: {source_id} {level}")
            tier = "S" if len(label) == 1 else "C"
            rows.append([q, ";".join(sorted(label)), tier, f"silver-v2b|pair:{pid}|{level}|{dirn}|{seed}"])
            meta.append([len(rows), pid, level, dirn, source_id, tier, len(label)])
            info["queries"][level] += 1

        emit("L0", sel, idx.label(cat, None, sel, []), "")
        for n in pick_rows(by_head, k_rows, rng):
            id_, title = snap.docs[n]
            head, brand, attrs, _ = ms.derive(title, types[id_])
            h = list(idx.heads[n])
            sub = v2.substitute(h, list(cat), sel)
            for level, (q, extra) in ladder(sub, brand, attrs).items():
                emit(level, q, idx.label(cat, tuple(h), sub, extra), id_)
        info["sampled_rows"] = min(k_rows, sum(len(v) for v in by_head.values()))

    totals = {lv: sum(i["queries"][lv] for i in report_pairs) for lv in LEVELS}
    report = {
        "params": {"seed": seed, "k_rows": k_rows, "as_of": as_of, "class_rule": class_rule},
        "snapshot": snap.describe(),
        "index_ids": len(snap.index_ids),
        "levels": LEVEL_DESC,
        "pairs": len(pairs),
        "zero_coverage": [i["pair_id"] for i in report_pairs if i["class_rows"] == 0],
        "queries_per_level": totals,
        "rows": len(rows),
        "pair_details": report_pairs,
    }
    return rows, meta, report


def main(argv=None):
    p = argparse.ArgumentParser(description="Build SILVER-v2b (query-detail ladder, optimistic).")
    p.add_argument("--zip", type=Path, help="catalog zip (default: the one zip in data/catalog/)")
    p.add_argument("--pairs", type=Path, default=REPO / "eval" / "synonyms" / "head_synonyms.csv")
    p.add_argument("--out", type=Path, default=REPO / "eval" / "silver_v2b.csv")
    p.add_argument("--meta", type=Path, default=REPO / "eval" / "silver_v2b_meta.csv")
    p.add_argument("--report", type=Path, default=REPO / "eval" / "results" / "silver_v2b_build.json")
    p.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    p.add_argument("--k-rows", type=int, default=DEFAULTS["k_rows"])
    p.add_argument("--as-of", default=DEFAULTS["as_of"])
    p.add_argument("--class-rule", choices=CLASS_RULES, default=DEFAULTS["class_rule"],
                   help="catalog-term side of the head rule: anywhere in the head, or at its start")
    a = p.parse_args(argv)
    if "golden" in a.out.name:
        p.error("refusing to write a silver set to a golden file")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    zip_path = a.zip or cs.default_zip()
    rows, meta, report = build(zip_path, v2.read_pairs(a.pairs), a.seed, a.k_rows, a.as_of,
                               a.class_rule)
    report = {"catalog_zip": Path(zip_path).name, "catalog_sha256": ms.sha256(zip_path),
              "pairs_sha256": ms.sha256(a.pairs), **report}
    ms.write_csv(a.out, ms.GOLDEN_HEADER, rows)
    ms.write_csv(a.meta, META_HEADER, meta)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out} ({len(rows)} rows), {a.meta}, {a.report}")
    print("queries per level:", json.dumps(report["queries_per_level"]),
          " zero-coverage pairs:", len(report["zero_coverage"]))
    return report


if __name__ == "__main__":
    main()
