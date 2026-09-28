# Demo v0.1: rules, score, templates (measured 2026-09-28)

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

## Result

| Code | fake P | fake R | fake FP | fake FN | real P | real R | real FP | real FN | Bar (fake) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| T1 | 0.36 | 0.40 | 7 | 6 | 0.24 | **1.00** | 32 | 0 | none |
| T2 | 1.00 | 1.00 | 0 | 0 | not run | not run | | | **PASS** |
| T3 | 0.59 | 1.00 | 7 | 0 | 0.91 | 1.00 | 1 | 0 | none |
| T4 | 1.00 | 0.80 | 0 | 2 | 1.00 | 0.90 | 0 | 1 | none |
| T6 | 1.00 | 1.00 | 0 | 0 | (no labels) | | 0 | | **PASS** |
| NOT_IN_CATALOG | 1.00 | 1.00 | 0 | 0 | 1.00 | 1.00 | 0 | 0 | none |
| NOT_IN_FORCE | 1.00 | 1.00 | 0 | 0 | 1.00 | 1.00 | 0 | 0 | none |

Support is 10 for T1–T6 and 4 for each ID status. ID-status lines that also got T2: 0 on the fake
profile.

**Where the T1 false positives are (by true label):**
- fake: 4 on the vague low-amount lines (labeled clean as T4 hard negatives), 3 on T4 lines.
- real: 12 on the vague low-amount lines, 10 on T4 lines, **10 on every T3 line**.
- **0 on the non-vague clean lines** in both (130 fake, 150 real).
Every T1 false positive is either a vague-only text or a T3 line. The vague case was predicted in
the rule's docstring before the run; the T3 overlap was not. On the real catalog a declared
general ID ranks outside the top 20 for a specific text, because thousands of specific titles
extend it.

**Where the T1 misses are:** fake only, 6 of 10. In all 6 the declared neighbor ranked 2–4 (inside
the top 5 shown), the close-neighbor blind spot stated in the docstring. In 4 of them T3 fired
instead (the neighbor was a general ID).

**The T3 false positives** are T1 lines whose neighbor is a general ID (7 fake, 1 real).
Swapping a specific ID for its general head is also a T3 situation, and the generator labels
it T1 only.

**T4 misses:** «اجناس فروش رفته» and «کالای متفرقه» (fake), «کالای متفرقه» (real). The words «رفته» and «کالای» are not in the rule's vague
list, a known gap already tested in `tests/test_engine.py`. The list was not extended to fit the
generator.

## What the T1 numbers are NOT evidence of
- **Not evidence that T1 catches real mislabeling.** The labels come from our generator, and the
  generator and the detector share the same notion of closeness (normalized tokens). No real
  invoice line has been checked.
- **Real recall 1.00 is not robustness.** The generator draws the neighbor at random among the IDs
  that share the text's head word. In a 1M-row catalog those are mostly far from the text: all
  10 declared IDs ranked outside the top 20 with `sim_declared` ≤ 0.15. The real set contains no
  near-misses; the fake set does, and there recall is 0.40. With n = 10, 10/10 has a 95% Wilson
  interval of about 0.72–1.00.
- **Not evidence of precision on real invoices.** 150 of the 162 real clean lines are catalog
  titles with light noise, so they match their own ID trivially. Seller text is usually terser
  (architecture.md §3, Finding: 184 of 300 seller-style queries match 2–20 IDs).
- **Not evidence about tax consequences.** T1 never reads a rate or a tax status. "Different rate /
  different charges_vat" only shapes which swaps the generator makes.
- The real catalog may be truncated (CLAUDE.md, OPEN), so every real-profile number is provisional.

## Hard flags (decided 2026-09-28)
`NOT_IN_CATALOG`, `NOT_IN_FORCE` and `QUARANTINED_ONLY` bypass the risk score (architecture.md §3).
A line with one gets **no score** and always goes to review. The app shows the flag above
everything else. The other rules still run and are listed. Both evals: 8 hard-flagged lines
(4 + 4), **0 with a score**. `QUARANTINED_ONLY` is a hard flag while its proper status stays OPEN;
the flag states the fact ("every row of this ID was quarantined in our copy") and nothing more.

## Risk score by true label (hard-flagged lines excluded; hand-set weights, `src/detect/score.py`)
The weights were **not changed**. The only edits were removing the three hard-flag codes and adding
**`T1` with weight 0**. T1's evidence already enters through the three BM25 terms, and giving it
its own weight is an open decision.

| Label | fake n | fake mean | fake min–max | fake ≥ 0.5 | real n | real mean | real min–max | real ≥ 0.5 |
|---|---:|---:|---|---:|---:|---:|---|---:|
| clean | 142 | 0.019 | 0.000–0.261 | 0 | 162 | 0.023 | 0.000–0.255 | 0 |
| T1 | 10 | 0.336 | 0.020–0.548 | **1** | 10 | 0.278 | 0.229–0.543 | **1** |
| T2 | 10 | 0.600 | 0.600–0.600 | 10 | – | | | |
| T3 | 10 | 0.414 | 0.393–0.442 | 0 | 10 | 0.530 | 0.513–0.539 | 10 |
| T4 | 10 | 0.434 | 0.250–0.550 | 5 | 10 | 0.521 | 0.250–0.552 | 9 |
| T6 | 10 | 0.501 | 0.500–0.505 | 10 | – | | | |

**Only 1 of 10 T1 lines reaches the "needs review" threshold (0.5) on either profile.** The rule
fires on all 10 real T1 lines, but with weight 0 the score does not follow it. Whether T1 gets a
weight is a decision for a human; nothing was tuned here. T3 lines score higher on the real profile
because the BM25 terms (declared general ID ranked outside the top 20) add to the T3 weight.

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
- **T1 misleading neighbor** (generator): same head word as the text's source title, in force on
  the same date, not containing every word of the text, and a different rate (fake) or a different
  `charges_vat` (real). Drawn at random among such neighbors, not the closest.
- **QUARANTINED_ONLY** is kept as its own `rate_at` status (hard flag, no T2). What status such an
  ID should get is OPEN in architecture.md §3; this avoids deciding it silently.
- **units.json `valid_to` is inclusive**, the same convention as the catalog's `ExpirationDate`.
  It is converted once to a half-open end by `jdatetime`.

## Open, for a human decision (not done, because each would change a rule or a weight after seeing results)
1. **T1 weight.** At 0, T1 hits rarely reach review (1 of 10).
2. **T1 vs T3.** On the real catalog T1 fires on every T3 line. One option is for T1 to yield when
   T3 fires. That changes the pre-registered rule, so it needs a new pre-registration.
3. **T1 on vague-only text.** It fires on all of them. One option is for T1 to abstain when every
   word is vague (T4's word list). Same caveat.
4. **A harder T1 set.** Draw the neighbor from the declared text's own top-k rather than at random,
   to measure the near-miss blind spot on the real catalog.
