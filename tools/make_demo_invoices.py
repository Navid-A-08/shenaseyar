"""Synthetic demo invoice lines with ground-truth labels. Seeded and reproducible.

    python tools/make_demo_invoices.py [--profile fake|real] [--seed 1405] [--n 200]
                                       [--catalog PATH] [--out PATH]

Two catalog profiles (src/demo/pipeline.py):
  fake  data/sample/fake_catalog.csv -> data/sample/demo_invoices.csv (committed).
        Mix for n = 200: 10 each of T1, T2, T3, T4, T6, 4 NOT_IN_CATALOG, 4 NOT_IN_FORCE,
        12 vague lines with a LOW amount (hard negatives for T4, labeled clean), the rest clean
        (130; 142 lines labeled clean in all).
  real  the manually downloaded catalog -> data/interim/demo_invoices_real.csv (NOT committed:
        the lines carry real catalog titles). Same mix WITHOUT T2 and T6 (150 clean; 162 labeled
        clean in all).
        T2 cannot be labeled: what the Vat column means is TODO(legal). T6 does not depend on
        the catalog and is measured on the fake profile.
Every line has exactly one label or none. Issue dates fall in [1402-01-01, 1405-12-29].

How each kind is built (rates always come from rate_at on the line's date, never from literals):
  clean           an ID in force on the date, seller-style text from its title, vra = its rate,
                  vam = base x vra / 100 rounded to a whole Rial (half-up, floor or ceiling at random)
  T1              "misleading neighbor" (architecture.md §7), never a random ID. Text written from
                  an ID A in force on the date; the DECLARED ID is a neighbor B that
                    - is in force on the same date,
                    - does not contain every word of the text (else the text supports B as well),
                    - differs from A in its tax consequence: a different rate on the fake profile;
                      a different charges_vat (Taxable: مشمول vs معاف / غیر مشمول) on the real
                      profile, because the real Vat column is unresolved (TODO(legal)),
                    - is lexically close to the text:
                      fake  B shares A's head word (first token of the normalized title), drawn
                            at random among those;
                      real  HARD SET (2026-09-30): B is drawn at random from the text's own
                            top-20 in-force BM25 matches. If none of them qualifies, the line is
                            skipped and another A is drawn; the counts go to
                            eval/results/demo_invoices_real_build.json. (The head-word picker
                            was dropped for the real catalog: among ~1M rows a random same-head ID
                            is almost never close, so that set only measured unrelated-ID swaps.)
                  vra / vam are consistent with B, so the line is otherwise clean (no T2, no T6).
  T2              as clean, but vra = another rate that exists in the table; vam consistent with it
  T6              as clean, but vam moved by at least 2 Rial (small absolute or 1-50% relative)
  T3              a general ID G declared, text written from a specific ID whose title starts with
                  G's title and which is in force on the same date; vra/vam consistent with G
  T4              a vague phrase, base >= 1.2 x the T4 threshold; vra/vam consistent
  NOT_IN_CATALOG  a 13-digit ID absent from the catalog
  NOT_IN_FORCE    an ID of the catalog on a date where none of its versions is in force

On the real profile, vra is the version's raw Vat value: a placeholder number that no rule reads
as a rate there (T2 does not run; T6 only compares vam with the line's own vra).

Seller text: the title, company part dropped half the time, then light noise the normalizer is
meant to undo (Arabic ي/ك, Persian digits, a ZWNJ for a space). Circularity warning: the labels
come from this generator, which knows what the rules look for; T1/T3/T4 numbers measure agreement
with it, not real-world accuracy.

The T4 threshold is read from its rule file so the label means "vague and above the threshold".
The vague phrases are NOT read from the rule: some are deliberately worded the way sellers write.
"""
import argparse
import csv
import json
import random
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

import jdatetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.detect.engine import load_rules  # noqa: E402
from src.detect.features import CatalogSearch  # noqa: E402
from src.detect.rates import RateTable, Status, parse_jalali  # noqa: E402
from src.retrieval.bm25 import tokenize  # noqa: E402

DATE_MIN, DATE_MAX = "1402-01-01", "1405-12-29"
COLUMNS = ["line_id", "issue_date", "sstid", "sstt", "am", "mu", "fee", "vra", "vam", "labels"]
VAGUE_PHRASES = ["کالا", "خدمات", "اقلام متفرقه", "کالای متفرقه", "بابت خدمات", "سایر کالاها",
                 "خدمات متفرقه", "اجناس فروش رفته"]
ZWNJ = "‌"
ROUNDINGS = [ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING]
MAX_TRIES = 100_000
NEIGHBOR_SAMPLE = 200        # neighbors examined per T1 attempt (random subset of the head group)
BUILD_REPORT = ROOT / "eval" / "results" / "demo_invoices_real_build.json"
DEFAULTS = {"fake": (ROOT / "data" / "sample" / "fake_catalog.csv",
                     ROOT / "data" / "sample" / "demo_invoices.csv"),
            "real": (None, ROOT / "data" / "interim" / "demo_invoices_real.csv")}


def days(lo=DATE_MIN, hi=DATE_MAX):
    d, end, out = parse_jalali(lo), parse_jalali(hi), []
    while d <= end:
        out.append(d.strftime("%Y-%m-%d"))
        d += jdatetime.timedelta(days=1)
    return out


def t4_threshold():
    rule = next(r for r in load_rules() if r.code == "T4")
    return Decimal(str(rule.params["min_amount_rial"]))


def fmt(d):
    d = Decimal(d)
    return str(int(d)) if d == d.to_integral_value() else f"{d.normalize():f}"


def charges_vat(version):
    """Derived boolean (architecture.md §3): only مشمول charges VAT."""
    return version.tax_status == "taxable"


class Gen:
    def __init__(self, table, seed, rates_trusted=True):
        self.t, self.rng = table, random.Random(seed)
        self.rates_trusted = rates_trusted
        self.all_days = days()
        self.ids = sorted(table.ids())
        self._ok, self._gap, self._rates = {}, {}, None
        self._heads = self._pairs = None
        self.search = None               # CatalogSearch, built only for the top-20 T1 picker
        self.t1_stats = {"attempts": 0, "skipped": 0, "skipped_no_taxable_difference": 0,
                         "skipped_candidates_contain_whole_text": 0, "neighbor_ranks": []}
        self.t4_min = t4_threshold()

    # --- lazy per-ID lookups (the real catalog has ~1M IDs) ---------------------------------
    def ok_days(self, sid):
        if sid not in self._ok:
            self._ok[sid] = [d for d in self.all_days
                             if self.t.rate_at(sid, d).status is Status.OK]
        return self._ok[sid]

    def gap_days(self, sid):
        if sid not in self._gap:
            self._gap[sid] = [d for d in self.all_days
                              if self.t.rate_at(sid, d).status is Status.NOT_IN_FORCE]
        return self._gap[sid]

    def rates(self):
        if self._rates is None:
            self._rates = self.t.distinct_rates()
        return self._rates

    def differs(self, va, vb):
        """Different tax consequence: rate if rates are trusted, else charges_vat."""
        if self.rates_trusted:
            return va.rate != vb.rate
        return charges_vat(va) != charges_vat(vb)

    def _index(self):
        """One pass over the titles: head word -> IDs (any version), and T3 (general, specific)
        pairs where the specific's first-version title extends the general's."""
        general = {}
        for g in self.ids:
            vg = self.t.versions(g)[0]
            if vg.is_general:
                general.setdefault(tuple(tokenize(vg.title)), []).append(g)
        lengths = sorted({len(k) for k in general})
        heads, pairs = {}, []
        for sid in self.ids:
            vs = self.t.versions(sid)
            for v in vs:
                toks = tokenize(v.title)
                if toks:
                    group = heads.setdefault(toks[0], [])
                    if not group or group[-1] != sid:
                        group.append(sid)
            if vs[0].is_general:
                continue
            ts = tuple(tokenize(vs[0].title))
            for n in lengths:
                if n >= len(ts):
                    break
                pairs.extend((g, sid) for g in general.get(ts[:n], ()))
        self._heads, self._pairs = heads, pairs

    def heads(self):
        if self._heads is None:
            self._index()
        return self._heads

    def t3_pairs(self):
        if self._pairs is None:
            self._index()
        return self._pairs

    # --- pieces ---------------------------------------------------------------------------
    def pick_ok(self, ids=None):
        pool = ids or self.ids
        for _ in range(MAX_TRIES):
            sid = self.rng.choice(pool)
            ok = self.ok_days(sid)
            if ok:
                d = self.rng.choice(ok)
                return sid, d, self.t.rate_at(sid, d).version
        raise SystemExit("no ID in force on any date in range")

    def seller_text(self, title, drop_company=True):
        r = self.rng
        t = title
        if drop_company and "/" in t and r.random() < 0.5:
            t = t.split("/")[0]
        t = " ".join(t.replace("/", " ").split())
        if r.random() < 0.3:
            t = t.replace("ی", "ي").replace("ک", "ك")
        if r.random() < 0.3:
            t = t.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        if r.random() < 0.3 and " " in t:
            parts = t.split(" ")
            k = r.randrange(len(parts) - 1)
            t = " ".join(parts[:k + 1]) + ZWNJ + " ".join(parts[k + 1:])
        return t

    def amounts(self):
        r = self.rng
        am = Decimal(r.randint(1, 20)) if r.random() < 0.8 else Decimal(r.randint(1, 40)) / 2
        fee = Decimal(max(1, round(r.lognormvariate(14.5, 1.2) / 1000))) * 1000
        return am, fee

    def vam(self, am, fee, vra):
        exact = am * fee * vra / 100
        return exact.quantize(Decimal(1), rounding=self.rng.choice(ROUNDINGS))

    def line(self, d, sid, text, am, fee, vra, vam=None, label=""):
        vam = self.vam(am, fee, vra) if vam is None else vam
        return {"issue_date": d, "sstid": sid, "sstt": text, "am": fmt(am), "mu": "",
                "fee": fmt(fee), "vra": fmt(vra), "vam": fmt(vam), "labels": label}

    # --- kinds ----------------------------------------------------------------------------
    def clean(self):
        sid, d, v = self.pick_ok()
        am, fee = self.amounts()
        return self.line(d, sid, self.seller_text(v.title), am, fee, v.rate)

    def t1(self):
        return self.t1_head_word() if self.rates_trusted else self.t1_top20()

    def t1_top20(self):
        """Real profile (hard set): the declared ID is drawn from the text's OWN top-20 in-force
        BM25 matches, so the swap is always close. Lines with no eligible candidate are skipped
        and counted in self.t1_stats."""
        if self.search is None:
            self.search = CatalogSearch(self.t)
        st = self.t1_stats
        for _ in range(MAX_TRIES):
            a, d, va = self.pick_ok()
            text = self.seller_text(va.title)
            st["attempts"] += 1
            ranked = self.search.features(text, a, d).ranked
            differing = [(i, c) for i, c in enumerate(ranked, 1)
                         if c.sstid != a and self.differs(va, c.version)]
            words = set(tokenize(text))
            cands = [(i, c) for i, c in differing if not words <= set(tokenize(c.version.title))]
            if not cands:
                st["skipped"] += 1
                st["skipped_no_taxable_difference" if not differing
                   else "skipped_candidates_contain_whole_text"] += 1
                continue
            rank, c = self.rng.choice(cands)
            st["neighbor_ranks"].append(rank)
            am, fee = self.amounts()
            return self.line(d, c.sstid, text, am, fee, c.version.rate, label="T1")
        raise SystemExit("no T1 neighbor in any top-20")

    def t1_head_word(self):
        heads = self.heads()
        for _ in range(MAX_TRIES):
            a, d, va = self.pick_ok()
            toks = tokenize(va.title)
            group = [b for b in heads.get(toks[0], ()) if b != a] if toks else []
            if not group:
                continue
            text = self.seller_text(va.title)
            words = set(tokenize(text))
            for b in self.rng.sample(group, min(len(group), NEIGHBOR_SAMPLE)):
                res = self.t.rate_at(b, d)
                if res.status is not Status.OK or not self.differs(va, res.version):
                    continue
                if words <= set(tokenize(res.version.title)):
                    continue
                am, fee = self.amounts()
                return self.line(d, b, text, am, fee, res.version.rate, label="T1")
        raise SystemExit("catalog has no misleading neighbor for T1")

    def t2(self):
        sid, d, v = self.pick_ok()
        am, fee = self.amounts()
        wrong = self.rng.choice([x for x in self.rates() if x != v.rate])
        return self.line(d, sid, self.seller_text(v.title), am, fee, wrong, label="T2")

    def t6(self):
        sid, d, v = self.pick_ok()
        am, fee = self.amounts()
        good = self.vam(am, fee, v.rate)
        r = self.rng
        if good == 0 or r.random() < 0.5:
            delta = Decimal(r.randint(2, 500))
        else:
            delta = max(Decimal(2), (good * Decimal(r.randint(1, 50)) / 100).quantize(Decimal(1)))
        if good - delta >= 0 and r.random() < 0.5:
            delta = -delta
        return self.line(d, sid, self.seller_text(v.title), am, fee, v.rate, good + delta, "T6")

    def t3(self):
        pairs = self.t3_pairs()
        if not pairs:
            raise SystemExit("catalog has no general/specific pair for T3")
        for _ in range(MAX_TRIES):
            g, s = self.rng.choice(pairs)
            both = sorted(set(self.ok_days(g)) & set(self.ok_days(s)))
            if both:
                break
        else:
            raise SystemExit("no general/specific pair in force on a common date")
        d = self.rng.choice(both)
        vg, vs = self.t.rate_at(g, d).version, self.t.rate_at(s, d).version
        am, fee = self.amounts()
        text = self.seller_text(vs.title, drop_company=False)   # keep what makes it specific
        return self.line(d, g, text, am, fee, vg.rate, label="T3")

    def vague(self, high):
        sid, d, v = self.pick_ok()
        r = self.rng
        am = Decimal(r.randint(1, 5))
        lo, hi = (self.t4_min * Decimal("1.2"), self.t4_min * 20) if high else \
                 (Decimal(1_000_000), self.t4_min * Decimal("0.8"))
        base = Decimal(r.randint(int(lo), int(hi)))
        fee = (base / am / 1000).quantize(Decimal(1)) * 1000
        if high and am * fee < lo:
            fee += 1000
        if not high and am * fee > hi:
            fee -= 1000
        return self.line(d, sid, r.choice(VAGUE_PHRASES), am, fee, v.rate,
                         label="T4" if high else "")

    def not_in_catalog(self):
        r = self.rng
        while True:
            sid = r.choice(["290", "280", "233"]) + "".join(str(r.randint(0, 9)) for _ in range(10))
            if self.t.rate_at(sid, DATE_MIN).status is Status.NOT_IN_CATALOG:
                break
        _, _, v = self.pick_ok()
        am, fee = self.amounts()
        return self.line(r.choice(self.all_days), sid, self.seller_text(v.title), am, fee,
                         r.choice(self.rates()), label="NOT_IN_CATALOG")

    def not_in_force(self):
        for _ in range(MAX_TRIES):
            sid = self.rng.choice(self.ids)
            gaps = self.gap_days(sid)
            if gaps:
                break
        else:
            raise SystemExit("no ID has a date out of force in range")
        d = self.rng.choice(gaps)
        vs = self.t.versions(sid)
        am, fee = self.amounts()
        return self.line(d, sid, self.seller_text(vs[0].title), am, fee,
                         self.rng.choice([v.rate for v in vs]), label="NOT_IN_FORCE")


def generate(table, n=200, seed=1405, rates_trusted=True, stats=None):
    """rates_trusted=False is the real profile: no T2 or T6 lines, T1 neighbors from the text's
    top-20 BM25 matches, differing in charges_vat. `stats` (a dict) receives the T1 skip counts."""
    g = Gen(table, seed, rates_trusted)
    each, status, vague_neg = round(n * 0.05), round(n * 0.02), round(n * 0.06)
    rows = [g.t1() for _ in range(each)]
    if rates_trusted:
        rows += [g.t2() for _ in range(each)]
    rows += [g.t3() for _ in range(each)] + [g.vague(True) for _ in range(each)]
    if rates_trusted:
        rows += [g.t6() for _ in range(each)]
    rows += ([g.not_in_catalog() for _ in range(status)]
             + [g.not_in_force() for _ in range(status)]
             + [g.vague(False) for _ in range(vague_neg)])
    rows += [g.clean() for _ in range(n - len(rows))]
    if stats is not None:
        stats.update(g.t1_stats)
    g.rng.shuffle(rows)
    for i, row in enumerate(rows, 1):
        row["line_id"] = f"L{i:03d}"
    return rows


def write(rows, out):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", choices=sorted(DEFAULTS), default="fake")
    ap.add_argument("--seed", type=int, default=1405)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--catalog")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.profile == "real":
        from src.demo.pipeline import real_profile
        catalog = a.catalog or real_profile().path
    else:
        catalog = a.catalog or DEFAULTS["fake"][0]
    out = a.out or DEFAULTS[a.profile][1]
    stats = {}
    rows = generate(RateTable.from_catalog(catalog), a.n, a.seed, a.profile == "fake", stats)
    write(rows, out)
    if a.profile == "real":
        BUILD_REPORT.write_text(json.dumps({"seed": a.seed, "n": a.n, "t1_picker": "top20",
                                            "t1": stats}, indent=2) + "\n", encoding="utf-8")
        print(f"T1 picker: {stats['attempts']} lines tried, {stats['skipped']} skipped "
              f"(no Taxable difference in the top 20: {stats['skipped_no_taxable_difference']}; "
              f"only candidates containing the whole text: "
              f"{stats['skipped_candidates_contain_whole_text']}); "
              f"ranks of the chosen neighbors: {sorted(stats['neighbor_ranks'])}")
    labels = {}
    for r in rows:
        labels[r["labels"] or "clean"] = labels.get(r["labels"] or "clean", 0) + 1
    print(f"wrote {len(rows)} lines ({a.profile} profile) to {out}: {dict(sorted(labels.items()))}")


if __name__ == "__main__":
    main()
