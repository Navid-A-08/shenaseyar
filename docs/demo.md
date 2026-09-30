# Demo v0.1: rules, score, templates (measured 2026-09-28, real T1 set and scoring redone 2026-09-30)

Scope and what is descoped: CLAUDE.md ("Demo scope (v0.1)", "Descoped").
Reproduce:
```
python tools/make_demo_invoices.py                      # fake profile (committed lines)
python tools/make_demo_invoices.py --profile real       # real profile (lines NOT committed)
python tools/eval_demo_rules.py                         # -> eval/results/demo_rules.json
python tools/eval_demo_rules.py --profile real          # -> eval/results/demo_rules_real.json
```

## Two catalog profiles: which rule runs on which catalog
One invoice line declares one `sstid`, so every rule on that line must read **one** catalog. The
split "T2 on the fake catalog, T1/T3/T4 and retrieval on the real one" is therefore made per
**run**, not per rule (`CatalogProfile` in `src/demo/pipeline.py`):

| Profile | Catalog | Lines (200, seed 1405) | Rules run | Not run |
|---|---|---|---|---|
| `fake` | `data/sample/fake_catalog.csv` (50 invented rows) | `data/sample/demo_invoices.csv` (committed) | T1, T2, T3, T4, T6, ID status | none |
| `real` | the manual download in `data/catalog/` (999,753 active rows, 966,089 IDs) | `data/interim/demo_invoices_real.csv` (**not committed**: real titles) | T1, T3, T4, T6, ID status | **T2** |

- **T2 is not run on the real profile** because what its `Vat` column means is `TODO(legal)`
  (`docs/data_dictionary.md` §8). The pipeline reports it as *not run* (`LineResult.skipped`, the
  app says so, and the eval lists it under `not_run`). It is never reported as "no finding".
  The app also shows no rate on the real profile.
- The T2/T6 bar is measured on the fake profile only.
- The real-profile lines contain no T2 and no T6 labels. T2 cannot be labeled there. T6 does not
  depend on the catalog, so it is measured on the fake profile; on the real one it still runs, and
  any firing counts as a false positive.
- On the real profile, `vra` in the synthetic lines is the version's raw `Vat` value. That is a
  placeholder number: nothing reads it as a rate there.
- Loading the real catalog takes ~45 s (rate table 17 s, BM25 26 s); one query ~0.16 s.

## Pre-registered bars
Set 2026-09-28 before any rule code: **T2 and T6 precision and recall both 1.00** on the fake
profile. T1, T3 and T4: no bar, reported only. The **T1 decision rule** was committed
(`d96472f`) before any T1 run:
fires iff `rate_at` is OK, the text matches some in-force ID, **`rank_declared > 5` and
`sim_declared < 0.5`** (docstring of `check_description_mismatch`, `src/detect/engine.py`).

## Result (2026-09-30; real profile = hard T1 set)

| Code | fake P | fake R | fake FP | fake FN | real P | real R | real FP | real FN | Bar (fake) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| T1 | 0.36 | 0.40 | 7 | 6 | **0.09** | **0.30** | 32 | 7 | none |
| T2 | 1.00 | 1.00 | 0 | 0 | not run | not run | | | **PASS** |
| T3 | 0.59 | 1.00 | 7 | 0 | 1.00 | 1.00 | 0 | 0 | none |
| T4 | 1.00 | 0.80 | 0 | 2 | 1.00 | 0.60 | 0 | 4 | none |
| T6 | 1.00 | 1.00 | 0 | 0 | (no labels) | | 0 | | **PASS** |
| NOT_IN_CATALOG | 1.00 | 1.00 | 0 | 0 | 1.00 | 1.00 | 0 | 0 | none |
| NOT_IN_FORCE | 1.00 | 1.00 | 0 | 0 | 1.00 | 1.00 | 0 | 0 | none |

Support is 10 for T1–T6 and 4 for each ID status. ID-status lines that also got T2: 0 on the fake
profile. The fake-profile lines and numbers are unchanged from 2026-09-28.

### The real-catalog T1 set (hard set)
The declared (swapped) ID is drawn at random from the description's **own top-20 in-force BM25
matches**, excluding the true ID, and must differ from it in `Taxable` (charges VAT or not).
A line with no such candidate is skipped and another is drawn
(`eval/results/demo_invoices_real_build.json`):

- **336 lines tried, 326 skipped (97%)**, all because no ID in the top 20 differed in `Taxable`.
  0 were skipped because the only candidates contained the whole text.
- Rank of the 10 chosen neighbors for their text: 2, 9, 16, 17, 18, 18, 20, 20, 20, 20.
- **T1 recall 0.30 (3 of 10).** With n = 10 the 95% Wilson interval is about 0.11–0.60.
- The 7 misses, against the pre-registered rule (`rank_declared > 5` and `sim_declared < 0.5`):
  **6 fail the similarity condition** (rank 9–20, but `sim_declared` 0.51–0.77: the swapped ID
  scores more than half of the best match) and **1 fails the rank condition** (rank 2).
  The 3 hits have `sim_declared` 0.33–0.42.
- **T1 precision 0.09 (3 of 35).** The 32 false positives are 12 vague low-amount lines (labeled
  clean), 10 T4 lines and all 10 T3 lines. There are 0 on the 150 non-vague clean lines.

**What the skip rate means.** A close neighbor with a different `Taxable` exists for only about 3%
of the drawn lines. The 10 T1 lines are therefore not a random sample of catalog IDs: they come
from the small part of the catalog where taxable and non-taxable titles sit next to each other.
Their neighbors also sit low in the top 20 (8 of 10 at rank 16 or lower).

### Earlier real-catalog T1 numbers (2026-09-28): an unrelated-ID swap, not the headline
The first real set (commit `a774e6e`) drew the swapped ID at random among all IDs sharing the
description's **first word**. In a catalog of about a million rows such an ID is almost never
close to the text: all 10 swapped IDs ranked outside the top 20 with `sim_declared` ≤ 0.15. That
set gave **T1 precision 0.24, recall 1.00**. It measured whether T1 notices an essentially
unrelated ID, which is the easy case, so it says nothing about misleading neighbors and is not the
headline. The picker was replaced; those lines are no longer generated.

### Fake profile
- T1 false positives: 4 on the vague low-amount lines, 3 on T4 lines. 0 on the 130 non-vague clean
  lines.
- T1 misses: 6 of 10. In all 6 the declared neighbor ranked 2–4 (inside the top 5 shown). In 4 of
  them T3 fired instead (the neighbor was a general ID).
- T3 false positives: 7, all T1 lines whose neighbor is a general ID. Swapping a specific ID for
  its general head is also a T3 situation, and the generator labels it T1 only.

### T1 false positives in general
Every T1 false positive is a vague-only text or a T3 line. The vague case was predicted in the
rule's docstring before the first run; the T3 overlap was not. On the real catalog a declared
general ID ranks outside the top 20 for a specific text, because thousands of specific titles
extend it.

### T4 misses
Fake: «اجناس فروش رفته» and «کالای متفرقه». Real: 4, all «اجناس فروش رفته». The words «رفته» and
«کالای» are not in the rule's vague list, a known gap already tested in `tests/test_engine.py`.
The list was not extended to fit the generator. T4 recall moves between runs (0.90 on
2026-09-28, 0.60 now) only because a new draw picked that phrase more often.

## What the T1 numbers are NOT evidence of
- **Not evidence that T1 catches real mislabeling.** The labels come from our generator, and the
  generator and the detector share the same notion of closeness (BM25 over normalized tokens).
  The hard set is built from the detector's own ranking. No real invoice line has been checked.
- **Recall 0.30 is not a recall on real errors.** It is 3 of 10 synthetic swaps, from the 3% of
  lines that have a close neighbor with a different `Taxable`. How real mislabeled lines are
  distributed (close or far, taxable or not) is unknown.
- **Not evidence of precision on real invoices.** 150 of the 162 real clean lines are catalog
  titles with light noise, so they match their own ID trivially. Seller text is usually terser
  (architecture.md §3, Finding: 184 of 300 seller-style queries match 2–20 IDs).
- **Not evidence about tax consequences.** T1 never reads a rate or a tax status. The `Taxable`
  difference only shapes which swaps the generator makes.
- The real catalog may be truncated (CLAUDE.md, OPEN), so every real-profile number is provisional.

## Hard flags (decided 2026-09-28)
`NOT_IN_CATALOG`, `NOT_IN_FORCE` and `QUARANTINED_ONLY` bypass the risk score (architecture.md §3).
A line with one gets **no score** and always goes to review. The app shows the flag above
everything else. The other rules still run and are listed. Both evals: 8 hard-flagged lines
(4 + 4), **0 with a score**. `QUARANTINED_ONLY` is a hard flag while its proper status stays OPEN;
the flag states the fact ("every row of this ID was quarantined in our copy") and nothing more.

## The score is a ranking, not a gate (decided 2026-09-30)
Any fired rule **floors** the line's score at the review threshold (0.5), so a line enters the
review queue because a rule fired (or a hard flag is set), never because of the weights
(architecture.md §6, `src/detect/score.py`). The weighted sum only orders the queue. **No weight
was changed**; T1 still weighs 0. `AMBIGUOUS` does not floor, because it must never raise a score.

Hard-flagged lines excluded. "In review" = score ≥ 0.5; "floored" = lifted to 0.5 by the floor;
"raw" = the weighted sum before the floor.

| Label | fake n | fake in review | fake floored | fake raw mean (min–max) | real n | real in review | real floored | real raw mean (min–max) |
|---|---:|---:|---:|---|---:|---:|---:|---|
| clean | 142 | 4 | 4 | 0.019 (0.000–0.261) | 162 | 12 | 12 | 0.021 (0.000–0.280) |
| T1 | 10 | 8 | 7 | 0.336 (0.020–0.548) | 10 | **3** | 3 | 0.145 (0.031–0.187) |
| T2 | 10 | 10 | 0 | 0.600 (0.600–0.600) | – | | | |
| T3 | 10 | 10 | 10 | 0.414 (0.393–0.442) | 10 | 10 | 0 | 0.537 (0.530–0.542) |
| T4 | 10 | 9 | 4 | 0.434 (0.250–0.550) | 10 | 10 | 4 | 0.440 (0.274–0.552) |
| T6 | 10 | 10 | 0 | 0.501 (0.500–0.505) | – | | | |

- **The floor does what it was meant to:** every line on which a rule fired is in the queue. On
  2026-09-28, before the floor, 1 of 10 T1 lines reached the threshold on either profile.
- **It cannot help where no rule fires.** On the hard real set only 3 of 10 T1 lines are in
  review, because T1 fired on 3. The 7 missed lines have raw scores of 0.03–0.17, the same range
  as clean lines, so the retrieval terms do not separate them either.
- **The queue now contains T1's false positives:** the "clean" lines in review (4 fake, 12 real)
  are the vague low-amount lines on which T1 fired.
- **Floored lines tie at exactly 0.5** (25 fake, 19 real). `LineResult.raw_score` orders them.
- The 4 real T4 lines that the T4 rule missed are in review anyway, through T1.
- The fake T1 lines in review without T1 firing got there through T3.

## Why all these numbers are weak evidence
- **Circular.** The generator knows what the rules look for. T2/T6 at 1.00 shows the arithmetic
  and the lookup are implemented as specified, not that they catch real errors. T1/T3/T4 measure
  agreement with the generator.
- **Fake profile: tiny invented catalog.** 50 rows, with BM25 statistics nothing like the real one.
- **Every citation is a placeholder.** `data/legal/units.json` has 2 `TODO(legal)` entries. No unit
  is attached to T1 yet (`TODO(legal)`), so T1 explanations say that no citation is on file.

## Design choices made here (reversible)
- **T6 tolerance: 1 Rial, inclusive, absolute.** Arithmetic is exact (`Decimal` from each number's
  text). The only legitimate difference is rounding `vam` to a whole Rial, which is under 1 Rial
  whether the rule is half-up, floor or ceiling. Which rounding the law requires is not assumed.
  Boundary tests: ±1.00 is accepted, ±1.01 fires, and so does ±(1 + 1e-7).
- **T6 base = am × fee.** The demo line has no discount field.
- **T3** fires only if the declared general ID matches the text at all (BM25 > 0), and a
  specific ID in force on the date scores strictly higher within the top 5.
- **T1 misleading neighbor** (generator). Both profiles: in force on the same date, not containing
  every word of the text. Fake: same head word as the text's source title and a different rate,
  drawn at random. Real: drawn at random from the text's own top-20 BM25 matches, with a different
  `charges_vat` (derived from `Taxable`: مشمول against معاف / غیر مشمول).
- **QUARANTINED_ONLY** is kept as its own `rate_at` status (hard flag, no T2). What status such an
  ID should get is OPEN in architecture.md §3; this avoids deciding it silently.
- **units.json `valid_to` is inclusive**, the same convention as the catalog's `ExpirationDate`.
  It is converted once to a half-open end by `jdatetime`.

## Open, for a human decision (not done: each would change a pre-registered rule after seeing results)
1. **T1's similarity boundary.** 6 of the 7 hard-set misses have `sim_declared` between 0.51 and
   0.77. Moving `max_sim` now would be choosing the boundary from results. It needs a new
   pre-registration and a fresh set (another seed) to measure it on.
2. **T1 vs T3.** On the real catalog T1 fires on every T3 line. One option is for T1 to yield when
   T3 fires.
3. **T1 on vague-only text.** It fires on all of them, and with the floor they now enter the
   review queue. One option is for T1 to abstain when every word is vague (T4's word list).
4. **More T1 lines.** n = 10 gives a wide interval. At a 97% skip rate, 100 T1 lines need about
   3,400 draws.
