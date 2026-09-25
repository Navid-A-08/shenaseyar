# Silver set (machine-generated)

> **⚠ READ THIS FIRST**
>
> - **The queries are derived from catalog titles.** The set is therefore **BIASED IN FAVOUR OF
>   LEXICAL RETRIEVAL**, and scores on it are **OPTIMISTIC**, closer to a ceiling than to real
>   accuracy. **They are not an accuracy estimate.** Real seller text is not a trimmed copy of the
>   catalog title.
> - **It cannot satisfy the Phase 1 exit criterion.** That criterion is tied to the human golden
>   set (`eval/golden.csv`, tier S only, at least 60 rows). `eval/run_eval.py` prints
>   `NOT APPLICABLE` for the silver set and never prints MET or NOT MET for it.
> - "Optimistic" is a direction, not a proven bound. It is not guaranteed that every retriever
>   scores lower on the golden set.

`eval/silver.csv` does **not** replace `eval/golden.csv`. The golden set stays human-labeled.
The silver set exists to exercise the retrieval stack at volume until the golden set is filled.

## Files

| File | Committed? | What |
|---|---|---|
| `tools/make_silver.py` | yes | the generator (deterministic, seeded) |
| `eval/silver.csv` | **no** (gitignored) | the set: `query_text,expected_sstid,tier,notes`, same format as golden |
| `eval/silver_meta.csv` | **no** (gitignored) | per row: source ID, Type, Vat, length bucket, number of matches, tier (for later analysis) |
| `eval/results/silver_build.json` | yes | build parameters, catalog SHA-256, allocation table, counts. No text. |
| `eval/results/silver_*.json` | yes | `run_eval.py --out` results on the silver set. No query text. |

The two CSVs are an extract of the catalog, so they are never committed (the no-extracts rule in
`CLAUDE.md`).

## Rebuilding

Rebuilding needs **the same catalog zip**, which is a **manual download** (this project never
automates access to the portal):

| | |
|---|---|
| File | `product_all_2026-09-22T19-49-07_part_1_0df81806-5d13-4353-99a2-dbf3a308c5c9.zip` |
| SHA-256 | `b0703c8b5fea42a6e8590184a24090b13f5bdfd0d1b950f344615fad917957ba` |

```
python tools/make_silver.py            # defaults below; writes the 3 files above
python eval/run_eval.py --retriever <name> --catalog <zip> --golden eval/silver.csv --out eval/results/silver_<name>.json
```

With the same zip and parameters the output is byte-identical. This was checked by rebuilding on
the real zip and comparing bytes. The generator prints the zip's SHA-256; if it differs from the
table above, the set is different.

## Parameters (defaults)

| Parameter | Value | Meaning |
|---|---|---|
| `--seed` | 20260925 | seeds every random choice (sampling, noise) |
| `--n` | 300 | rows to emit |
| `--as-of` | 1405-07-01 | "in force" date. Fixed, not today, so the build is reproducible. |
| `--floor` | 5 | minimum quota per non-empty cell (or all its rows, if fewer) |
| `--max-matches` | 20 | a non-unique query with more matching IDs than this is rejected |
| `--noise-frac` | 0.30 | share of rows that get exactly one noise transform |
| `--pool-factor` | 10 | candidates drawn per cell = 10 × quota |

## Method

### 1. Which rows can be sampled
- The row is in force on `--as-of`: `RunDate <= as_of <= ExpirationDate` (inclusive, data_dictionary §9).
- The ID is 13 digits. One 14-digit ID is excluded.
- The ID has exactly one row in force. The 38 IDs with two rows in force (`AMBIGUOUS`) are excluded.

### 2. Stratification: Type × title length
- **Vat is not a sampling dimension.** Retrieval matches text against titles and never sees Vat.
  Vat is recorded per row in `silver_meta.csv` for later analysis.
- Cells are Type × {short, long}. A title is short when it has fewer characters than the median
  of all eligible titles (136).
- Quotas are proportional to cell size, with a floor of 5 rows per non-empty cell (or all its rows
  if fewer). Rounding uses the largest remainder.
- In each cell, a seeded random pool of candidates is drawn and tried in random order until the
  quota is met.

### 3. Query derivation (rules, not invention)
Every rule that fires is listed in `notes`. Goods titles are split on `،`. Service titles are
split on `/`.

| Rule | Effect |
|---|---|
| `head` | keep the first segment (head noun, or the service category) |
| `brand` | keep the value of a `برند X` / `نام تجارتی X` segment, without the keyword |
| `brand_latin` | no brand segment: keep the first all-Latin segment among the first three |
| `attr1`, `attr2` | keep the next remaining segment(s), in catalog order |
| `trim_attr` | an attribute longer than 4 words is cut to its first 4 words |
| `drop_mfr` | drop `سازنده …`, `تولید کننده …`, `شرکت …` segments and a trailing `/ شرکت …` |
| `drop_country` | drop country names (fixed list), `کشور …`, `ساخت …` |
| `drop_pack` | drop segments containing `بسته بندی` |
| `drop_paperweight` | drop segments with `g\m^2` / `گرم بر متر مربع` |
| `drop_partno` | drop `شماره فنی …`. A part number would make lexical matching trivial. |
| `drop_placeholder` | drop catalog placeholders like `برند فاقد نام تجارتی` or `مدل فاقد مدل` |
| `drop_admin` | drop the general-service tail from `سازمان امور مالیاتی` on |
| `drop_commas` | the catalog's `،` structure is replaced by spaces |

Query = head + brand + first 1 attribute. If that is not unique, head + brand + first 2 attributes
(step 4).

### 4. Uniqueness → tier
A query **matches** an in-force ID when that ID's title contains every token of the query.
- **Normalization:** ي/ك and digits are folded, text is casefolded, and ZWNJ is removed; the text
  is then split on whitespace and punctuation.
- **Nature of the test:** a plain token-subset test. It has no ranking and no fuzziness.

| Matching IDs | Result | `notes` |
|---|---|---|
| exactly 1 (the source row) | tier **S**, `expected_sstid` = that ID | `silver\|rule-derived\|<rules>\|<seed>` |
| 2 to 20 | tier **C**, `expected_sstid` = all matching IDs, `;`-separated | `silver\|rule-derived\|non-unique\|<rules>\|<seed>` |
| more than 20 | rejected and counted; the next candidate is tried | |

Tier C sets are exact under this test but still incomplete as "acceptable answers": a title that
says the same thing in other words does not match. The harness reports tier C as a lower bound.

The uniqueness filter makes the sample favour distinctive titles, which is part of why the set is
optimistic.

### 5. Noise
- Exactly `round(0.30 × rows)` rows get one noise transform. The rows and the transform are chosen
  with the seed, from the transforms that apply to that query.
- Noise is applied after the tier decision, so the expected IDs are unchanged.

| Rule | Effect |
|---|---|
| `noise_arabic_yk` | ی → ي, ک → ك |
| `noise_no_zwnj` | remove ZWNJ |
| `noise_fa_digits` | Latin digits → Persian digits |
| `noise_typo` | in one word of at least 4 letters: swap two adjacent letters, or drop one |

## Result of the default build

- **300 rows: 116 tier S, 184 tier C.** No shortfall.
- **182 candidates rejected** for more than 20 matches. 0 rejected as duplicate queries.
- **Tier C set sizes:** median 4 (min 2, max 20). 111 sets have 2–5 IDs, 39 have 6–10, and 34
  have 11–20.
- **Query length:** median 6 words (min 3, max 16).
- **Noise:** 90 rows: 34 `noise_arabic_yk`, 18 `noise_fa_digits`, 38 `noise_typo`,
  **0 `noise_no_zwnj`**. No derived query contains a ZWNJ, because the catalog titles sampled
  rarely use one. **The silver set does not test robustness to missing half-spaces.**
- **Vat of source rows** (recorded, not sampled on): 271 at 10, 27 at 0, 1 at 50, 1 at 90.

Allocation table:

| Type | Length | Eligible rows | Quota | Tried | S | C | Rejected >20 | Shortfall |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| شناسه اختصاصی تولید داخل | long | 248,695 | 73 | 175 | 19 | 54 | 102 | 0 |
| شناسه اختصاصی تولید داخل | short | 174,993 | 52 | 76 | 24 | 28 | 24 | 0 |
| شناسه اختصاصی خدمت | long | 2,860 | 5 | 5 | 1 | 4 | 0 | 0 |
| شناسه اختصاصی خدمت | short | 23,782 | 7 | 8 | 3 | 4 | 1 | 0 |
| شناسه اختصاصی وارداتی | long | 225,170 | 67 | 104 | 26 | 41 | 37 | 0 |
| شناسه اختصاصی وارداتی | short | 265,267 | 78 | 96 | 37 | 41 | 18 | 0 |
| شناسه عمومی تولید داخل | short | 1,222 | 5 | 5 | 0 | 5 | 0 | 0 |
| شناسه عمومی خدمت | long | 5 | 5 | 5 | 2 | 3 | 0 | 0 |
| شناسه عمومی خدمت | short | 3 | 3 | 3 | 3 | 0 | 0 | 0 |
| شناسه عمومی وارداتی | short | 1,351 | 5 | 5 | 1 | 4 | 0 | 0 |

General goods have no long cell: every general goods title is shorter than the median.

**What the tier split says:** most rule-derived queries (head + brand + at most 2 attributes)
do not identify one ID. Even among specific IDs, several often share the same head, brand and
first attributes. They differ in the manufacturer, part number or later attributes, which the
rules drop.

## Metrics so far

| Retriever | File | Tier S R@5 | Tier C R@5 | Combined R@5 |
|---|---|---:|---:|---:|
| `random` (harness check, not a baseline) | `eval/results/silver_random_seed0.json` | 0.0000 | 0.0000 | 0.0000 |

## Known limitations
- **Optimistic by construction** (see the top). Seller wording, abbreviations, synonyms and
  missing words are not modelled; only 4 kinds of character noise are.
- **Imbalanced by design:** the floors give general IDs 18 of 300 rows, although they are 0.4%
  of the catalog. Combined metrics are not weighted to the catalog's distribution.
- **Catalog may be truncated** (data_dictionary §6), so all counts are provisional.
- The country list, the placeholder patterns and the segment prefixes are hand-written from a
  sample of titles. A title whose structure they miss keeps a manufacturer or country segment
  as an attribute. That makes the query more specific, not wrong.
