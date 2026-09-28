# Demo v0.1: rules, score, templates (measured 2026-09-28)

Scope and what is descoped: CLAUDE.md ("Demo scope (v0.1)", "Descoped").
All data here is **invented**: `data/sample/fake_catalog.csv` (50 rows) and 200 synthetic lines
from `tools/make_demo_invoices.py` (seed 1405, committed as `data/sample/demo_invoices.csv`).
Reproduce: `python tools/eval_demo_rules.py` → `eval/results/demo_rules.json`.

## Pre-registered bar
Set 2026-09-28 before any rule code: **T2 and T6 precision and recall both 1.00** (arithmetic and
lookup, so anything less is a bug). T3 and T4: no bar, reported only.

## Result

| Code | Precision | Recall | Support | FP | FN | Bar |
|---|---:|---:|---:|---:|---:|---|
| T2 | 1.00 | 1.00 | 10 | 0 | 0 | **PASS** |
| T6 | 1.00 | 1.00 | 10 | 0 | 0 | **PASS** |
| T3 | 1.00 | 1.00 | 10 | 0 | 0 | none |
| T4 | 1.00 | 0.90 | 10 | 0 | 1 | none |
| NOT_IN_CATALOG | 1.00 | 1.00 | 4 | 0 | 0 | none |
| NOT_IN_FORCE | 1.00 | 1.00 | 4 | 0 | 0 | none |

ID-status lines that also got T2: **0**. The T3 precision covers 55 clean lines that declare a
general ID; none fired.

**T4 miss (L190):** «اجناس فروش رفته». «رفته» is not in the rule's vague-word list, so the rule
does not see the text as vague. The same gap applies to inflected forms such as «کالاهای» and
«کالای» (tested as a known gap in `tests/test_engine.py`). The list was not extended to match the
generator: that would fit the rule to its own test data.

## Why these numbers are weak evidence
- **Circular.** The generator knows what the rules look for. T2/T6 at 1.00 shows the arithmetic
  and the lookup are implemented as specified, not that they catch real errors. T3/T4 measure
  agreement with the generator.
- **Tiny, invented catalog.** 50 rows, with BM25 statistics nothing like the real 1M-row catalog.
- **T2 runs on the fake catalog only.** What the real catalog's `Vat` column means is still
  `TODO(legal)` (data_dictionary.md §8).
- **Every citation is a placeholder.** `data/legal/units.json` has 2 `TODO(legal)` entries until
  it is filled by hand.

## Risk score by true label (hand-set weights, `src/detect/score.py`)

| Label | n | mean | min | max | ≥ 0.5 (HIGH_RISK) |
|---|---:|---:|---:|---:|---:|
| clean | 152 | 0.018 | 0.000 | 0.260 | 0 |
| T2 | 10 | 0.600 | 0.600 | 0.600 | 10 |
| T6 | 10 | 0.500 | 0.500 | 0.500 | 10 |
| T4 | 10 | 0.520 | 0.250 | 0.550 | 9 |
| T3 | 10 | 0.410 | 0.398 | 0.447 | 0 |
| NOT_IN_CATALOG | 4 | 0.866 | 0.858 | 0.873 | 4 |
| NOT_IN_FORCE | 4 | 0.413 | 0.400 | 0.431 | 0 |

At the hand-set display threshold 0.5, the T3 and NOT_IN_FORCE lines are not marked "needs
review", although their rules fire and the UI lists them. The weights were left as set: tuning
them on these 200 lines would fit them to the generator. The threshold should instead come from
review capacity (architecture.md §6) once real lines exist.

## Design choices made here (reversible)
- **T6 tolerance: 1 Rial, inclusive, absolute.** Arithmetic is exact (`Decimal` from each number's
  text). The only legitimate difference is rounding `vam` to a whole Rial, which is under 1 Rial
  whether the rule is half-up, floor or ceiling. Which rounding the law requires is not assumed.
  Boundary tests: ±1.00 is accepted, ±1.01 fires, and so does ±(1 + 1e-7).
- **T6 base = am × fee.** The demo line has no discount field.
- **T3** fires only if the declared general ID matches the text at all (BM25 > 0), and a
  specific ID in force on the date scores strictly higher within the top 5.
- **QUARANTINED_ONLY** is kept as its own `rate_at` status (weight 0, no T2). What status such an
  ID should get is OPEN in architecture.md §3; this avoids deciding it silently.
- **units.json `valid_to` is inclusive**, the same convention as the catalog's `ExpirationDate`.
  It is converted once to a half-open end by `jdatetime`.
