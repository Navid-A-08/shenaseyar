"""Synthetic demo invoice lines with ground-truth labels. Seeded and reproducible.

    python tools/make_demo_invoices.py [--seed 1405] [--n 200] [--catalog data/sample/fake_catalog.csv]
                                       [--out data/sample/demo_invoices.csv]

Mix (for n = 200): 10 each of injected T2, T3, T4, T6 (20%), 4 NOT_IN_CATALOG, 4 NOT_IN_FORCE,
and the rest clean, of which 12 are vague descriptions with a LOW amount (hard negatives for T4).
Every line has exactly one label or none. Issue dates fall in [1402-01-01, 1405-12-29].

How each kind is built (rates always come from rate_at on the line's date, never from literals):
  clean           an ID in force on the date, seller-style text from its title, vra = its rate,
                  vam = base x vra / 100 rounded to a whole Rial (half-up, floor or ceiling at random)
  T2              as clean, but vra = another rate that exists in the table; vam consistent with it
  T6              as clean, but vam moved by at least 2 Rial (small absolute or 1-50% relative)
  T3              a general ID G declared, text written from a specific ID whose title starts with
                  G's title and which is in force on the same date; vra/vam consistent with G
  T4              a vague phrase, base >= 1.2 x the T4 threshold; vra/vam consistent
  NOT_IN_CATALOG  a 13-digit ID absent from the catalog
  NOT_IN_FORCE    an ID of the catalog on a date where none of its versions is in force

Seller text: the title, company part dropped half the time, then light noise the normalizer is
meant to undo (Arabic ي/ك, Persian digits, a ZWNJ for a space). Circularity warning: the labels
come from this generator, which knows what the rules look for; T3/T4 numbers measure agreement
with it, not real-world accuracy.

The T4 threshold is read from its rule file so the label means "vague and above the threshold".
The vague phrases are NOT read from the rule: some are deliberately worded the way sellers write.
"""
import argparse
import csv
import random
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

import jdatetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.detect.engine import load_rules  # noqa: E402
from src.detect.rates import RateTable, Status, parse_jalali  # noqa: E402
from src.retrieval.bm25 import tokenize  # noqa: E402

DATE_MIN, DATE_MAX = "1402-01-01", "1405-12-29"
COLUMNS = ["line_id", "issue_date", "sstid", "sstt", "am", "mu", "fee", "vra", "vam", "labels"]
VAGUE_PHRASES = ["کالا", "خدمات", "اقلام متفرقه", "کالای متفرقه", "بابت خدمات", "سایر کالاها",
                 "خدمات متفرقه", "اجناس فروش رفته"]
ZWNJ = "‌"
ROUNDINGS = [ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING]


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


class Gen:
    def __init__(self, table, seed):
        self.t, self.rng = table, random.Random(seed)
        self.all_days = days()
        ids = sorted(table.ids())
        self.ok_days = {i: [d for d in self.all_days if table.rate_at(i, d).status is Status.OK]
                        for i in ids}
        self.ok_ids = [i for i in ids if self.ok_days[i]]
        self.gap_days = {i: [d for d in self.all_days
                             if table.rate_at(i, d).status is Status.NOT_IN_FORCE] for i in ids}
        self.t4_min = t4_threshold()

    # --- pieces ---------------------------------------------------------------------------
    def pick_ok(self, ids=None):
        sid = self.rng.choice(ids or self.ok_ids)
        d = self.rng.choice(self.ok_days[sid])
        return sid, d, self.t.rate_at(sid, d).version

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

    def t2(self):
        sid, d, v = self.pick_ok()
        am, fee = self.amounts()
        wrong = self.rng.choice([x for x in self.t.distinct_rates() if x != v.rate])
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

    def t3_pairs(self):
        """(general, specific) pairs: the specific title extends the general one, and both are in
        force on at least one common date."""
        pairs = []
        for g in self.ok_ids:
            vg = self.t.versions(g)[0]
            if not vg.is_general:
                continue
            tg = tokenize(vg.title)
            for s in self.ok_ids:
                vs = self.t.versions(s)[0]
                ts = tokenize(vs.title)
                if vs.is_general or len(ts) <= len(tg) or ts[:len(tg)] != tg:
                    continue
                if set(self.ok_days[g]) & set(self.ok_days[s]):
                    pairs.append((g, s))
        return pairs

    def t3(self, pairs):
        g, s = self.rng.choice(pairs)
        both = sorted(set(self.ok_days[g]) & set(self.ok_days[s]))
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
                         r.choice(self.t.distinct_rates()), label="NOT_IN_CATALOG")

    def not_in_force(self):
        ids = sorted(i for i, ds in self.gap_days.items() if ds)
        sid = self.rng.choice(ids)
        d = self.rng.choice(self.gap_days[sid])
        vs = self.t.versions(sid)
        am, fee = self.amounts()
        return self.line(d, sid, self.seller_text(vs[0].title), am, fee,
                         self.rng.choice([v.rate for v in vs]), label="NOT_IN_FORCE")


def generate(table, n=200, seed=1405):
    g = Gen(table, seed)
    each, status, vague_neg = round(n * 0.05), round(n * 0.02), round(n * 0.06)
    pairs = g.t3_pairs()
    if not pairs:
        raise SystemExit("catalog has no general/specific pair for T3")
    rows = ([g.t2() for _ in range(each)] + [g.t3(pairs) for _ in range(each)]
            + [g.vague(True) for _ in range(each)] + [g.t6() for _ in range(each)]
            + [g.not_in_catalog() for _ in range(status)]
            + [g.not_in_force() for _ in range(status)]
            + [g.vague(False) for _ in range(vague_neg)])
    rows += [g.clean() for _ in range(n - len(rows))]
    g.rng.shuffle(rows)
    for i, row in enumerate(rows, 1):
        row["line_id"] = f"L{i:03d}"
    return rows


def write(rows, out):
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=1405)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--catalog", default=str(ROOT / "data" / "sample" / "fake_catalog.csv"))
    ap.add_argument("--out", default=str(ROOT / "data" / "sample" / "demo_invoices.csv"))
    a = ap.parse_args(argv)
    rows = generate(RateTable.from_catalog(a.catalog), a.n, a.seed)
    write(rows, a.out)
    labels = {}
    for r in rows:
        labels[r["labels"] or "clean"] = labels.get(r["labels"] or "clean", 0) + 1
    print(f"wrote {len(rows)} lines to {a.out}: {dict(sorted(labels.items()))}")


if __name__ == "__main__":
    main()
