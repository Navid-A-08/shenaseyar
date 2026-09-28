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
| `tools/analyze_ambiguity.py` | yes | rebuilds the ambiguity table (§Finding) |
| `eval/results/ambiguity.json` | yes | its output: counts and per-row categories by source ID. No text. |
| `eval/synonyms/head_synonyms.csv` | yes | synonym pairs for silver-v2 (drafted by Claude, reviewed and edited by Navid) |
| `tools/make_silver_v2.py` | yes | builds silver-v2 (main + head-only) |
| `eval/silver_v2.csv`, `eval/silver_v2_head.csv`, `eval/silver_v2_meta.csv` | **no** (gitignored) | silver-v2 sets and per-row meta |
| `eval/results/silver_v2_*.json` | yes | build counts, BM25 results, per-pair table. No catalog text (terms come from the list). |
| `tools/make_silver_v2b.py`, `tools/silver_v2b_report.py` | yes | builds silver-v2b (detail ladder); report per level / pair / slice |
| `eval/silver_v2b*.csv`, `eval/silver_v2b*_meta.csv` | **no** (gitignored) | silver-v2b sets: prefix rule (default) and `_contains` |
| `eval/results/silver_v2b*_{build,bm25,bm25_report}.json` | yes | build counts, BM25 ranks, ladder report. No catalog text. |

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

## Finding: ambiguity (measured 2026-09-26)

**This is the most important result of the silver set so far.** It is also recorded in
`docs/architecture.md` §3.

**1. Most seller-style queries do not identify one ID.**

| Outcome for a rule-derived query (head + brand + at most 2 attributes) | Count |
|---|---:|
| matches exactly 1 in-force ID (tier S) | 116 of 300 |
| matches 2–20 in-force IDs (tier C) | 184 of 300 |
| matches more than 20: rejected, replaced by the next candidate | 182 further candidates |

**2. What would separate the ambiguous rows.** For each of the 184 tier C rows, the source title
was rebuilt with more information and the same token-subset test was re-run. Source:
`eval/results/ambiguity.json` (built by `tools/analyze_ambiguity.py`; per-row categories included):

| Query rebuilt with … | Rows that become unique |
|---|---:|
| all remaining descriptive attributes (still no manufacturer, no part number) | 103 of 184 (56%) |
| … plus the manufacturer, or plus the part number (not unique before) | 46 of 184 (25%): 14 by manufacturer only, 29 by part number only, 3 by either |
| unique under none of these | 35 of 184 (19%) |

The 182 rejected candidates were not analysed this way.

**3. What this means.**
- For this catalog, exact-ID prediction from seller-style text is **often impossible in
  principle**. The text does not carry enough information to pick one ID.
- **Short lines** (a head and 1–2 attributes): the missing information is often ordinary
  descriptive attributes, which a seller *could* write but often won't.
- **At least 81 of the 184** (44%): it takes the manufacturer or the part number, or nothing in
  the title separates the IDs at all.
- **UNVERIFIED assumption:** that invoice lines rarely carry the manufacturer or part number.
  There is no invoice data in this project yet (`TODO(data)`).

**4. OPEN for Phase 2 (flagged, not decided).** Retrieval may be best framed as *suggest plausible
alternative IDs*, while the well-posed task is **consistency of the DECLARED ID with the text (T1)**.
Phase 1 is unchanged.
- *Suggestion, not a decision:* since 56% of ambiguous rows resolve with ordinary descriptive
  attributes, a useful future metric is **recall as a function of query detail level** (head only /
  + attributes / + brand), rather than a single recall number.

Method for table 2 (`tools/analyze_ambiguity.py`; output `eval/results/ambiguity.json`):
- The same rules and normalization as the generator. Catalog in force as of 1405-07-01, 13-digit IDs.
- "All remaining attributes": head + brand + every attribute kept by `derive()`. Countries,
  packaging, placeholders and admin tails stay dropped.
- "Manufacturer": the `سازنده …` / `تولید کننده …` / `شرکت …` segments (or the trailing
  `/ شرکت …`). "Part number": the `شماره فنی …` segment.
- Of the 184 rows, 168 have a manufacturer segment and 57 have a part number.
- Rebuild: `python tools/analyze_ambiguity.py`. It reads the catalog zip (same SHA-256 as above)
  and `eval/silver_meta.csv` (gitignored; rebuilt by `tools/make_silver.py`). It records both
  SHA-256 hashes in the JSON. Same inputs give the same JSON.

## Metrics so far

All runs: snapshot = rules R1-R4, active rows in force on 1405-07-01 (943,383 rows, 943,380 IDs;
67 quarantined-only IDs). On the silver set: missing 0, quarantined_only 0 (measured), not_in_index 0.

| Retriever | File | Tier S R@1 | Tier S R@5 | Tier C R@1 | Tier C R@5 | Combined R@5 | MRR@20 (combined) |
|---|---|---:|---:|---:|---:|---:|---:|
| `random` (harness check, not a baseline) | `eval/results/silver_random_seed0.json` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `bm25` (k1 1.5, b 0.75, untuned) | `eval/results/silver_bm25.json` | 0.9569 | **1.0000** | 0.9457 | 0.9728 | 0.9833 | 0.9619 |

**Read the BM25 row as a sanity check, not as a result.** The silver set is built with the same
token-subset test that BM25 rewards: a tier S row is, by construction, the only in-force title
containing every query token. BM25 finding it is close to guaranteed, so this number says the
index and the normalizer work, and almost nothing about real accuracy. It is not leakage
(nothing is trained); it is circularity by design (see the top of this page).

- **Misses at 5: 4 of 300, all tier C.** 3 are `noise_typo` rows and 1 is a clean row.
- **Noise that `normalize()` removes is not a test for BM25.** `noise_arabic_yk` and
  `noise_fa_digits` rows become identical to their clean versions after normalization. By noise
  type (R@5): clean 0.9952 (n=210), arabic_yk 0.9706 (n=34), fa_digits 1.0000 (n=18), typo
  0.9211 (n=38). This breakdown was a one-off; the harness does not report it yet.
- Runtime (i7-14700K, CPU only): snapshot 10.1 s, BM25 index build 23.6 s, 5.5 ms per query,
  process peak memory 1,573 MB. Index: 501,371 terms, 22,757,220 postings, 179 MB of arrays.

> **⚠ CEILING WARNING: this set can no longer compare retrievers**
>
> - **Tier S is saturated.** Untuned BM25 already reaches **Recall@5 = 1.0000 on tier S**. No
>   later retriever can score higher. The set can still reveal a retriever that does *worse*
>   than BM25, but it can never show one that does better. Tier C (0.9728, 5 misses at 5) has
>   almost no headroom either. **An ablation table built on this set (BM25 → dense → hybrid →
>   reranker) would show no improvements and would mean nothing.**
> - **Root cause:** silver queries are derived from title tokens, so the set can only ever test
>   token overlap. **Vocabulary mismatch cannot occur in a set built this way**: a seller writing
>   گوشی where the catalog says تلفن همراه is never generated. So the silver set can never show
>   whether dense or hybrid retrieval beats lexical retrieval.
> - **Consequence:** comparing retrievers requires queries whose wording is independent of the
>   titles: the human golden set, or a variant that deliberately replaces title words (a
>   silver-v2 with hand-written synonyms is proposed, not built).

## Silver-v2: synonym head (measured 2026-09-27)

> **Provenance of the synonym list.** `eval/synonyms/head_synonyms.csv` (32 pairs) was **drafted by
> Claude and reviewed and edited by Navid**. It is **not independent human data**: the words reflect
> an LLM's idea of how sellers write, checked by one person. Per-pair results are evidence about
> these pairs, not about real sellers' vocabulary.

**What changes from v1.** Rows are sampled per pair (10 per pair, seed 20260927) from in-force rows
whose head segment contains the catalog term. The catalog term in the head is replaced by the
seller term. No noise is added. Two files, reported separately:
- **main** (`eval/silver_v2.csv`): v1 query rules with the substituted head.
  - Acceptable IDs = matches of the original query ∪ matches of the substituted query. The second
    part covers catalog rows that really use the seller's word.
  - 1 ID → S, 2–20 → C, more → rejected.
- **head-only slice** (`eval/silver_v2_head.csv`): one query per pair, the bare seller term.
  - Label = every in-force ID whose head is assigned to the catalog term, plus every ID whose head
    *starts with* the seller term. So a genuine `گوشی …` head counts, `قاب گوشی` does not.
  - Classes are large (61 to 13,306 IDs). This isolates the synonym from brand and attribute
    overlap, and it is the first data point for the query-detail-level idea.

Head assignment uses whole normalized tokens in the head segment only, and the longest listed
catalog term wins (`رایانه لوحی` beats `رایانه`). Build: `python tools/make_silver_v2.py`. It
records the SHA-256 of the zip and of the list.

### BM25 results (untuned; snapshot R1-R4, in force on 1405-07-01)

| File | Rows evaluated | Tier S R@5 | Tier C R@5 | Combined R@1 | Combined R@5 | MRR@20 |
|---|---:|---:|---:|---:|---:|---:|
| main (`silver_v2_bm25.json`) | 99 (46 S, 53 C) | **1.0000** | 0.9434 | 0.9495 | 0.9697 | 0.9596 |
| head-only (`silver_v2_head_bm25.json`) | 22 (all C) | n/a | **0.4091** | 0.3182 | 0.4091 | 0.3437 |

- **The main file is saturated too** (tier S R@5 = 1.0000). With the head swapped, the brand and
  the 1–2 attributes still overlap the title, so BM25 finds the row anyway. **The main v2 file
  cannot measure the synonym effect.**
- **The head-only slice can.** BM25 finds a class member in the top 5 for **9 of 22** pairs. Split
  by whether the catalog itself heads rows with the seller's word:

  | Head-only pairs | n | hits at 5 | R@5 |
  |---|---:|---:|---:|
  | seller term also heads catalog rows (lexical match possible) | 11 | 7 | 0.64 |
  | seller term heads **no** catalog row (pure vocabulary mismatch) | 11 | 2 | **0.18** |

  The 2 pure-mismatch hits: تبلت (rank 4) and پاوربانک (rank 1). The seller word appears
  elsewhere in those titles.
- **Small numbers.** 22 head-only queries: one query moves R@5 by 4.5 points.
- Runtime as for v1: index build about 40 s, 3.5–12 ms per query, peak about 1.6 GB.

### Per pair (BM25)
Source: `eval/results/silver_v2_bm25_per_pair.json` (`tools/silver_v2_per_pair.py`).
"Main rows 0" with coverage means every sampled row was rejected: more than 20 matching IDs, or a
duplicate query.

| # | catalog term → seller term | rows headed by catalog term | rows headed by seller term | main rows (S) | main R@5 | head-only rank (top 20) |
|---:|---|---:|---:|---|---:|---:|
| 0 | تلفن همراه → گوشی | 662 | 19 | 0 (0) | n/a | 1 |
| 1 | تلفن همراه → موبایل | 662 | 0 | 2 (1) | 1.00 | miss |
| 2 | رایانه قابل حمل → لپ تاپ | 0 | 1,419 | 0 (0) | n/a | — |
| 3 | رایانه → کامپیوتر | 532 | 1 | 10 (4) | 1.00 | miss |
| 4 | رایانه لوحی → تبلت | 63 | 0 | 6 (0) | 1.00 | 4 |
| 5 | چاپگر → پرینتر | 983 | 0 | 2 (2) | 1.00 | miss |
| 6 | حافظه فلش → فلش مموری | 0 | 581 | 0 (0) | n/a | — |
| 7 | دوربین عکاسی → دوربین | 34 | 4,988 | 9 (6) | 1.00 | 1 |
| 8 | هدفون → هندزفری | 0 | 747 | 0 (0) | n/a | — |
| 9 | شارژر همراه → پاوربانک | 612 | 0 | 10 (6) | 1.00 | 1 |
| 10 | تلویزیون → ال ای دی | 2,556 | 0 | 0 (0) | n/a | miss |
| 11 | کولر گازی → اسپلیت | 7,187 | 0 | 0 (0) | n/a | 9 |
| 12 | کولر آبی → کولر | 588 | 2,273 | 4 (0) | 1.00 | 5 |
| 13 | اجاق گاز → گاز | 2,012 | 124 | 1 (0) | 1.00 | miss |
| 14 | ماشین لباسشویی → لباسشویی | 1,576 | 0 | 0 (0) | n/a | miss |
| 15 | مایکروویو → ماکروفر | 0 | 0 | 0 (0) | n/a | — |
| 16 | یخچال فریزر → یخچال | 2,175 | 3,038 | 3 (0) | 1.00 | miss |
| 17 | روغن موتور → روغن ماشین | 2,208 | 0 | 8 (5) | 1.00 | miss |
| 18 | لاستیک → تایر | 3,281 | 6,905 | 7 (4) | 0.86 | 1 |
| 19 | باتری → باطری | 6,059 | 0 | 3 (2) | 1.00 | miss |
| 20 | خودرو → ماشین | 7,451 | 5,855 | 3 (1) | 1.00 | 1 |
| 21 | کفش ورزشی → کتونی | 0 | 0 | 0 (0) | n/a | — |
| 22 | شلوار جین → جین | 0 | 0 | 0 (0) | n/a | — |
| 23 | پوشاک → لباس | 12 | 49 | 8 (6) | 1.00 | miss |
| 24 | نوشابه گازدار → نوشابه | 0 | 456 | 0 (0) | n/a | — |
| 25 | آب آشامیدنی → آب معدنی | 111 | 68 | 10 (5) | 0.80 | 1 |
| 26 | دستمال کاغذی → دستمال | 165 | 356 | 6 (0) | 1.00 | 1 |
| 27 | لامپ ال ای دی → لامپ کم مصرف | 0 | 4 | 0 (0) | n/a | — |
| 28 | میلگرد → آرماتور | 1,589 | 0 | 6 (4) | 1.00 | miss |
| 29 | سیمان پرتلند → سیمان | 0 | 80 | 0 (0) | n/a | — |
| 30 | صندلی اداری → صندلی گردان | 0 | 0 | 0 (0) | n/a | — |
| 31 | ماشین حساب → حساب گر | 67 | 0 | 1 (0) | 1.00 | miss |

### Pairs with zero coverage (listed, not dropped): 10 of 32
No in-force head contains the catalog term, so no row can be sampled. For several of them, the
catalog uses the *seller* term as the head instead: the pair's direction does not fit this
catalog. That is an observation for the list's author, not a change made here.

| # | pair | rows headed by the seller term |
|---:|---|---:|
| 2 | رایانه قابل حمل → لپ تاپ | 1,419 |
| 6 | حافظه فلش → فلش مموری | 581 |
| 8 | هدفون → هندزفری | 747 |
| 15 | مایکروویو → ماکروفر | 0 |
| 21 | کفش ورزشی → کتونی | 0 |
| 22 | شلوار جین → جین | 0 |
| 24 | نوشابه گازدار → نوشابه | 456 |
| 27 | لامپ ال ای دی → لامپ کم مصرف | 4 |
| 29 | سیمان پرتلند → سیمان | 80 |
| 30 | صندلی اداری → صندلی گردان | 0 |

## Silver-v2b: query-detail ladder (measured 2026-09-28)

Same synonym list (drafted by Claude, reviewed and edited by Navid; not independent human data),
now with **4 reversed pairs added as new rows** (`direction=reversed` in the note; the originals
stay, with their zero coverage). **A reversed pair tests the mismatch in the unrealistic
direction**: no seller types رایانه قابل حمل. It is a valid test of synonym handling, not a
realistic seller query, so reversed pairs are **their own slice and never enter the headline**.

**Method** (`tools/make_silver_v2b.py`):
- **Sources:** per pair, up to K = 10 source rows, picked round-robin across distinct heads.
- **Queries:** from each source row, one query per level, with the catalog term in the head
  replaced by the seller term:

  | Level | Query |
  |---|---|
  | L0 | bare seller term (once per pair) |
  | L1 | substituted head |
  | L2 | L1 + 1 attribute |
  | L3 | L1 + 2 attributes |
  | L4 | L1 + brand + 2 attributes |

  Identical queries within a pair and level are kept once.
- **Label (no cap, every level):** the ID's head starts with the source head (catalog side) or
  with the substituted head (seller side), AND its title contains the level's brand and attribute
  tokens. "Starts with" on the catalog side is the `prefix` class rule, the default (below).
- **Re-runnable as the list grows:** pair IDs are hashes of the normalized terms and each pair has
  its own seed, so adding pairs never changes existing pairs' queries (tested).
- **Report** (`tools/silver_v2b_report.py`):
  - **macro** = mean over pairs (each pair counts once): the headline.
  - **micro** = mean over queries.
  - **random** = expected Recall@5 of a uniform random retriever given the label sizes (chance level).

### Label rule: *contains* vs *prefix* (a measured problem, decided 2026-09-28)
The planned catalog-side rule was "the head **contains** the catalog term". Measured on the
snapshot: **11,649 of 43,274 class rows (26.9%) do not start with the term**, and many of them are
not the product:
- لاستیک: 100% (3,089 `تولید …` heads and 172 `ورق …` rubber sheets; لاستیک is also "rubber").
- کولر گازی: 77% (5,522 `یونیت …` units).
- چاپگر: 35% (322 `کارتریج …` cartridges).
- خودرو: 24% (پخش خودرو, repair services, `عوارض` tolls).

Such rows enter the class and can even become source rows. `--class-rule prefix` applies
"**starts with**" on the catalog side too (the seller side already used it). Both were built and
run.
- **Prefix:** 468 queries, 11 zero-coverage pairs (پوشاک drops out: none of its 12 class rows
  starts with the term).
- **Contains:** 554 queries, 10 zero-coverage pairs.

**Decision (Navid, 2026-09-28): `prefix` is the default.** The contains rule was labelling
non-products as correct answers. `--class-rule contains` stays available, and its results are
kept in the `*_contains*` files.

> **Methodological note: the label rule alone moves the numbers.** The same retriever (untuned
> BM25) on the same pairs scores higher under the looser rule. Headline R@5 macro goes from
> **0.45 to 0.57 at L1** and from **0.64 to 0.79 at L2**, because non-products such as cartridges
> or car stereos count as hits. How a class is defined can change a reported recall by more than
> a retriever change would, so the rule is part of the result and must be stated with it.

**The tables below use prefix. The last column shows the contains number for comparison.**

### BM25 (untuned) by detail level: prefix rule

**Headline: original-direction pairs**

| Level | Pairs | Queries | R@5 macro | R@5 micro | R@1 macro | random R@5 | R@5 macro, *contains* rule |
|---|---:|---:|---:|---:|---:|---:|---:|
| L0 bare seller term | 21 | 21 | **0.38** | 0.38 | 0.29 | 0.01 | 0.41 |
| L1 substituted head | 21 | 50 | **0.45** | 0.42 | 0.32 | 0.01 | 0.57 |
| L2 head + 1 attribute | 21 | 86 | **0.64** | 0.74 | 0.58 | 0.00 | 0.79 |
| L3 head + 2 attributes | 21 | 144 | **0.90** | 0.90 | 0.85 | 0.00 | 0.95 |
| L4 head + brand + 2 attributes | 12 | 76 | **0.98** | 0.97 | 0.91 | 0.00 | 1.00 |

**Original pairs, pure vocabulary mismatch** (the catalog heads no row with the seller term)

| Level | Pairs | Queries | R@5 macro | R@5 micro | R@1 macro | random R@5 | R@5 macro, *contains* rule |
|---|---:|---:|---:|---:|---:|---:|---:|
| L0 bare seller term | 11 | 11 | **0.18** | 0.18 | 0.09 | 0.01 | 0.18 |
| L1 substituted head | 11 | 23 | **0.25** | 0.22 | 0.13 | 0.01 | 0.38 |
| L2 head + 1 attribute | 11 | 45 | **0.55** | 0.69 | 0.51 | 0.00 | 0.73 |
| L3 head + 2 attributes | 11 | 75 | **0.89** | 0.89 | 0.85 | 0.00 | 0.92 |
| L4 head + brand + 2 attributes | 7 | 40 | **0.96** | 0.95 | 0.86 | 0.00 | 1.00 |

**Original pairs, seller term also heads catalog rows**

| Level | Pairs | Queries | R@5 macro | R@5 micro | R@1 macro | random R@5 | R@5 macro, *contains* rule |
|---|---:|---:|---:|---:|---:|---:|---:|
| L0 bare seller term | 10 | 10 | **0.60** | 0.60 | 0.50 | 0.02 | 0.64 |
| L1 substituted head | 10 | 27 | **0.67** | 0.59 | 0.53 | 0.01 | 0.77 |
| L2 head + 1 attribute | 10 | 41 | **0.74** | 0.80 | 0.66 | 0.00 | 0.85 |
| L3 head + 2 attributes | 10 | 69 | **0.92** | 0.91 | 0.85 | 0.00 | 0.99 |
| L4 head + brand + 2 attributes | 5 | 36 | **1.00** | 1.00 | 0.98 | 0.00 | 1.00 |

**Reversed pairs** (unrealistic direction; own slice)

| Level | Pairs | Queries | R@5 macro | R@5 micro | R@1 macro | random R@5 | R@5 macro, *contains* rule |
|---|---:|---:|---:|---:|---:|---:|---:|
| L0 bare seller term | 4 | 4 | **0.50** | 0.50 | 0.25 | 0.00 | 0.50 |
| L1 substituted head | 4 | 4 | **0.50** | 0.50 | 0.25 | 0.00 | 0.38 |
| L2 head + 1 attribute | 4 | 24 | **0.38** | 0.42 | 0.38 | 0.00 | 0.45 |
| L3 head + 2 attributes | 4 | 30 | **0.69** | 0.80 | 0.54 | 0.00 | 0.72 |
| L4 head + brand + 2 attributes | 3 | 29 | **1.00** | 1.00 | 0.87 | 0.00 | 1.00 |

**What the ladder says:**
- **Detail is what carries lexical retrieval over the synonym gap.** In the pure-mismatch slice,
  R@5 rises from 0.18 (bare term) to 0.25 (head), 0.55 (+1 attribute), 0.89 (+2 attributes) and
  0.96 (+brand).
- **A seller who writes the head plus two attributes is mostly found by BM25 even through a
  synonym. A seller who writes one or two words mostly is not.**
- **Chance level is negligible** (random R@5 at most 0.02), so the large class labels are not what
  produces the hits.
- **Where a better retriever can show a difference:** L0–L2, and only there. L3–L4 are near
  ceiling.
- **Evidence limits:** 21 original pairs in the headline, 11 of them pure mismatch. Pairs are the
  unit: queries within a pair are not independent. When comparing retrievers, compare pair by
  pair. Only large differences will be detectable with this many pairs.

### Per pair (prefix rule): R@5 per level, number of queries in brackets
Source: `eval/results/silver_v2b_bm25_report.json`. Pair IDs are stable across list edits.
The contains-rule table is in `eval/results/silver_v2b_contains_bm25_report.json`.

| pair | dir | catalog term → seller term | seller-headed rows | L0 | L1 | L2 | L3 | L4 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| ca7d916e61 | ori | تلفن همراه → گوشی | 19 | 1.00 (1) | 1.00 (1) | 0.00 (1) | 0.60 (5) | n/a (0) |
| 6175c97b41 | ori | تلفن همراه → موبایل | 0 | 0.00 (1) | 0.00 (1) | 0.00 (1) | 1.00 (5) | n/a (0) |
| 9ba041ae85 | ori | رایانه قابل حمل → لپ تاپ | 1,419 | zero coverage |  |  |  |  |
| 75a17561ba | ori | رایانه → کامپیوتر | 1 | 0.00 (1) | 0.57 (7) | 0.88 (8) | 0.89 (9) | 1.00 (4) |
| e9647fa607 | ori | رایانه لوحی → تبلت | 0 | 1.00 (1) | 1.00 (1) | 1.00 (3) | 1.00 (6) | n/a (0) |
| d0c2f148e1 | ori | چاپگر → پرینتر | 0 | 0.00 (1) | 0.20 (5) | 0.67 (6) | 0.86 (7) | 1.00 (4) |
| 117211339f | ori | حافظه فلش → فلش مموری | 581 | zero coverage |  |  |  |  |
| 75c23d66c8 | ori | دوربین عکاسی → دوربین | 4,988 | 1.00 (1) | 1.00 (1) | 1.00 (4) | 1.00 (8) | 1.00 (8) |
| 3d8b74ada1 | ori | هدفون → هندزفری | 747 | zero coverage |  |  |  |  |
| a1a95ec002 | ori | شارژر همراه → پاوربانک | 0 | 1.00 (1) | 1.00 (1) | 1.00 (2) | 1.00 (10) | 1.00 (10) |
| 41b3e73ab2 | ori | تلویزیون → ال ای دی | 0 | 0.00 (1) | 0.00 (1) | 0.00 (1) | 0.17 (6) | 0.71 (7) |
| dcae132067 | ori | کولر گازی → اسپلیت | 0 | 0.00 (1) | 0.00 (1) | 0.50 (4) | 0.83 (6) | n/a (0) |
| 7ffb58a51b | ori | کولر آبی → کولر | 2,273 | 1.00 (1) | 1.00 (1) | 1.00 (2) | 1.00 (8) | n/a (0) |
| 1859cf187d | ori | اجاق گاز → گاز | 124 | 0.00 (1) | 0.00 (2) | 0.67 (3) | 1.00 (7) | n/a (0) |
| 24b6a8b86b | ori | ماشین لباسشویی → لباسشویی | 0 | 0.00 (1) | 0.00 (2) | 0.60 (5) | 0.89 (9) | 1.00 (4) |
| b79fb23e13 | ori | مایکروویو → ماکروفر | 0 | zero coverage |  |  |  |  |
| 7df7d49b68 | ori | یخچال فریزر → یخچال | 3,038 | 0.00 (1) | 0.00 (1) | 0.00 (4) | 1.00 (8) | n/a (0) |
| 3dfb52f92b | ori | روغن موتور → روغن ماشین | 0 | 0.00 (1) | 0.00 (1) | 0.88 (8) | 1.00 (8) | 1.00 (10) |
| c2da0529b5 | ori | لاستیک → تایر | 6,905 | 0.00 (1) | 0.50 (2) | 1.00 (3) | 1.00 (2) | n/a (0) |
| 4866f9e8e4 | ori | باتری → باطری | 0 | 0.00 (1) | 0.50 (4) | 0.86 (7) | 1.00 (7) | 1.00 (3) |
| a94b0a0c21 | ori | خودرو → ماشین | 5,855 | 1.00 (1) | 0.60 (10) | 0.89 (9) | 1.00 (9) | 1.00 (6) |
| bcec63c2c1 | ori | کفش ورزشی → کتونی | 0 | zero coverage |  |  |  |  |
| e8b64cedb8 | ori | شلوار جین → جین | 0 | zero coverage |  |  |  |  |
| b078c5fb3e | ori | پوشاک → لباس | 49 | zero coverage |  |  |  |  |
| ce1717abb0 | ori | نوشابه گازدار → نوشابه | 456 | zero coverage |  |  |  |  |
| 1cb85cec47 | ori | آب آشامیدنی → آب معدنی | 68 | 1.00 (1) | 1.00 (1) | 1.00 (3) | 0.67 (9) | 1.00 (9) |
| 5d69d20e0b | ori | دستمال کاغذی → دستمال | 356 | 1.00 (1) | 1.00 (1) | 1.00 (4) | 1.00 (4) | 1.00 (9) |
| 57457811ae | ori | لامپ ال ای دی → لامپ کم مصرف | 4 | zero coverage |  |  |  |  |
| 158bdd49d2 | ori | میلگرد → آرماتور | 0 | 0.00 (1) | 0.00 (5) | 0.57 (7) | 1.00 (9) | 1.00 (2) |
| f95b0aa66d | ori | سیمان پرتلند → سیمان | 80 | zero coverage |  |  |  |  |
| 35c46c6af3 | ori | صندلی اداری → صندلی گردان | 0 | zero coverage |  |  |  |  |
| 6fab2affcb | ori | ماشین حساب → حساب گر | 0 | 0.00 (1) | 0.00 (1) | 0.00 (1) | 1.00 (2) | n/a (0) |
| 5b3218fdd0 | rev | لپ تاپ → رایانه قابل حمل | 0 | 0.00 (1) | 0.00 (1) | 0.00 (7) | 0.43 (7) | 1.00 (9) |
| 48ec5184ef | rev | فلش مموری → حافظه فلش | 0 | 0.00 (1) | 0.00 (1) | 0.00 (6) | 1.00 (10) | 1.00 (10) |
| 41ffd1f1b9 | rev | هندزفری → هدفون | 0 | 1.00 (1) | 1.00 (1) | 0.50 (2) | 0.33 (3) | n/a (0) |
| c9e270251e | rev | نوشابه → نوشابه گازدار | 0 | 1.00 (1) | 1.00 (1) | 1.00 (9) | 1.00 (10) | 1.00 (10) |

## Decision rule: dense vs BM25 (pre-registered 2026-09-28, before any dense code)

Written before any dense retriever exists, so the result cannot shape the rule. No parameters of
either retriever are tuned on this set: it is the test set.

**Data.** `eval/silver_v2b.csv`, prefix class rule, as registered:

| | |
|---|---|
| Synonym list SHA-256 | `ab86e48bada29bb277b13d40888a52c62c094dbae092b7753f3aed5813f232f3` |
| Catalog zip SHA-256 | `b0703c8b5fea42a6e8590184a24090b13f5bdfd0d1b950f344615fad917957ba` |
| Build | seed 20260928, K = 10, as_of 1405-07-01, snapshot R1-R4 |

- **Same index for both retrievers.** Both run on the same snapshot (943,383 in-force rows). A
  dense run on a smaller index is **not** a valid comparison unless BM25 is re-run on exactly the
  same rows.
- **If the list grows before or after the dense run,** the result on the registered list (hash
  above) is always reported. Results on a newer list are reported next to it, with that list's
  hash. They never replace it.

**Primary comparison.** Recall@5 at **L1** (substituted head), **original-direction
pure-mismatch pairs only** (11 pairs, 23 queries at registration), averaged over pairs.
- For each pair *p*: *d_p* = dense R@5 − BM25 R@5 on that pair's L1 queries.
- Δ = mean of *d_p* over the 11 pairs. **BM25 baseline: 0.2455.**
- Bootstrap over pairs: 10,000 resamples of the 11 pairs with replacement (seed 20260928). The
  statistic is the mean *d_p*; the 95% interval is the 2.5th–97.5th percentile.

**Verdict, in this order:**

| Outcome | Condition |
|---|---|
| **Dense better** | Δ ≥ **+0.15** AND the 95% interval excludes 0 (lower bound > 0) |
| **BM25 better** | Δ ≤ −0.15 AND the 95% interval excludes 0 (upper bound < 0) |
| **No detectable difference** | anything else. This is not "equal": 11 pairs cannot show a small difference. |

Resolution: one pair is 1/11 ≈ 0.09 of Δ, so 0.15 means roughly two pairs going from miss to hit.

**Secondary: reported, never deciding.**
- Same slice at L0 and L2.
- All levels for the headline slice, the seller-headed slice and the reversed slice.
- Per-query (micro) averages.
- Index build time, memory, and per-query latency (median and 95th percentile), next to recall.
  A retriever that wins by 0.2 but needs 2 s per query is a different product decision, and the
  report says so rather than letting recall alone decide.

**Hybrid signal.** If dense is "better" at L1 but its headline R@5 (averaged over pairs) at **L3 or
L4** is below BM25's, the report states it plainly: *dense wins where the seller writes little
and loses where they write more, which points to a hybrid, not dense alone.* The L3/L4 gap is
shown with its own bootstrap interval.

**Caveats that apply to any verdict.**
- The synonym list was drafted by Claude and reviewed by Navid: this tests those 11 gaps, not
  sellers' real vocabulary.
- Silver sets are optimistic. A verdict here is a direction for the human golden set, not a
  replacement for it.

## Acceptance bar: int8 vs full precision (pre-registered 2026-09-28, before any variant is tested)

Written before any quantization variant is tested, so the results cannot shape the bar. It
decides which int8 model (if any) the dense retriever in the rule above may use.

**Data.** The same seeded sample of **2,000 in-force titles** (normalized, seed 20260928, as_of
1405-07-01), encoded by each int8 variant and by the full-precision (fp32) official ONNX model.
**No silver query is involved.**

**Metrics, both against fp32 on the same 2,000 titles:**
- **Mean cosine:** mean over the 2,000 titles of cos(int8 vector, fp32 vector).
- **Top-20 overlap:** each of the 2,000 titles queries the other 1,999 (itself excluded). For
  each title, overlap = |top-20 under int8 ∩ top-20 under fp32| / 20; the metric is the mean over
  the 2,000 titles.

**Bar.** An int8 variant is acceptable only if **mean cosine ≥ 0.99 AND top-20 overlap ≥ 0.95**.

- Judged **only on agreement with full precision**, never on silver (or golden) recall.
- **If no variant reaches the bar,** the closest one is used and its disagreement rate
  (1 − top-20 overlap) is stated next to the verdict. It is not called acceptable.
- Speed settings that leave the vectors unchanged (threads, graph optimization, batch size) are
  not judged by this bar, but any setting that changes the vectors is.

### Result (measured 2026-09-28): no int8 variant met the bar

Same 2,000 titles, batch 64, onnxruntime default threads, graph optimization ALL
(`eval/results/int8_agreement.json`, built by `tools/bench_int8_agreement.py`).

| Variant | Mean cosine | Top-20 overlap | Disagreement | Meets bar |
|---|---:|---:|---:|---|
| per-tensor (original) | 0.9756 | 0.8489 | 0.1511 | no |
| per-channel | 0.9839 | 0.8675 | 0.1325 | no |
| per-channel after `quant_pre_process` | 0.9839 | 0.8675 | 0.1325 | no |
| per-channel, MatMul only (embeddings float32) | 0.9840 | 0.8677 | 0.1323 | no |

- **No variant met the pre-registered bar** (mean cosine ≥ 0.99 AND top-20 overlap ≥ 0.95).
- MatMul-only is nominally closest, but its margin over per-channel (0.0002 in overlap) is within
  noise, so **no meaningful winner exists**.
- **The "use the closest variant" clause is not invoked.** The dense index uses **fp32**; int8
  is not used at all.
- `quant_pre_process` ran without its symbolic shape pass (it needs sympy, not a project
  dependency). The full preprocessing is **untested**; it is moot since int8 is not used.

**Batch dependence (the substantive reason int8 is dropped).** Dynamic int8 quantization scales
activations per batch, so an int8 vector depends on which other texts share its batch: the
same titles differ by up to 0.034 (max absolute difference) between batch 64 and batch 128. A
text therefore has no single well-defined int8 vector, and a query encoded alone can disagree
with the index encoding of the same text. At batch 1 the MatMul-only variant reaches cosine
0.9865 and overlap 0.8782, still below the bar. fp32 is batch-invariant (difference 0.0 between
batch 1 and batch 64).

**Earlier speed numbers are superseded.** The first benchmark (`eval/results/dense_bench.json`:
14.6 titles/s int8, 8.7 titles/s fp32, 18 h projected) is superseded by the runs recorded here.
On 2026-09-28 the same models, sample and default threads measured 87.3 titles/s (int8) and 35.7
titles/s (fp32), a roughly 4–6× difference that is **unexplained**.

**fp32 speed (re-timed 2026-09-28, two fresh runs).** Same 2,000 titles, graph optimization ALL
(`eval/results/fp32_speed_run1.json`, `fp32_speed_run2.json`). Fastest setting in both runs:
onnxruntime default threads, batch 64: **33.6 and 34.4 titles/s**, projecting **7.80 h and
7.61 h** for the 943,383-row index. Every other setting (8 / 16 / 28 threads, batch 128) was
slower (8.0–12.3 h). The vectors were identical across all settings.

**Full index build: postponed (decided 2026-09-28).** `eval/golden.csv` is empty, so the Phase 1
exit criterion cannot be measured with any retriever yet, and the silver comparison above never
gates. The build (`python tools/build_dense_index.py`, resumable, writes `data/index/dense-fp32/`)
runs once the golden set has at least 60 tier S rows, so one build serves both the silver
comparison and the golden evaluation.

## Known limitations
- **Optimistic by construction** (see the top). Seller wording, abbreviations, synonyms and
  missing words are not modelled; only 4 kinds of character noise are.
- **Imbalanced by design:** the floors give general IDs 18 of 300 rows, although they are 0.4%
  of the catalog. Combined metrics are not weighted to the catalog's distribution.
- **Catalog may be truncated** (data_dictionary §6), so all counts are provisional.
- The country list, the placeholder patterns and the segment prefixes are hand-written from a
  sample of titles. A title whose structure they miss keeps a manufacturer or country segment
  as an attribute. That makes the query more specific, not wrong.
