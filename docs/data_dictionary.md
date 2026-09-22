# Data dictionary: product/service ID catalog (manual download)

Source file (not committed):
`data/catalog/product_all_2026-09-22T19-49-07_part_1_0df81806-5d13-4353-99a2-dbf3a308c5c9.zip`

- Downloaded by hand from stuffid.tax.gov.ir on 2026-09-22 (= 1405-06-31). See `docs/phase0_catalog.md`.
- The zip holds one entry, `product_all_..._part_1_....csv`, 346,191,395 bytes.
- The profile was built by streaming the CSV straight out of the zip with Python's standard library (`zipfile` + `csv`). Nothing was extracted, and the file was never fully loaded into memory.
- All numbers below are measured with full passes over the file, except where a line says "sample".

This document describes what the file **contains**. It makes no claims about what the values mean legally. Items marked `TODO(legal)` need checking against the law text.

## 1. File format

| Property | Value | How determined |
|---|---|---|
| Encoding | **UTF-8 with BOM** (`EF BB BF`) | BOM bytes checked. A strict UTF-8 decode of the whole file had 0 errors. Not windows-1256. |
| Delimiter | `,` | `csv.Sniffer` on the header. Every row parsed to exactly 10 fields (0 malformed rows). |
| Quoting | Standard CSV double quotes. Some fields contain commas and newlines (e.g. `PricingDescription`). | Parsed with `csv.reader` |
| Line count | Header + **1,000,000** data rows | Full pass |
| Date format | `YYYY-MM-DD` in the **Jalali (Solar Hijri)** calendar, e.g. `1405-07-01`. Every non-empty date value matched this pattern. | Full pass |
| Digits | Latin digits throughout. 0 descriptions contain Persian or Arabic-Indic digits. | Full pass |

Column names, exactly as written in the header. They are English, not Persian:

```
ID,DescriptionOfID,Vat,Taxable,RunDate,ExpirationDate,CreateDate,LastEditDate,Type,PricingDescription
```

## 2. Per-column profile

"Empty" means an empty string after trimming spaces. "Distinct" counts values that are not empty.

| Column | Type observed | Empty | Distinct | Example values |
|---|---|---|---|---|
| `ID` | 13-digit string (see §4). Keep it as a string. | 0 | 966,156 | `2330002104900`, `2710000129370`, `2720000129379` |
| `DescriptionOfID` | Persian free text, up to 572 characters. Specific IDs often end in `/ <company name>`. | 0 | 940,467 | `دستگاه جوجه کشی ماشین الات تکثیر، نظافت و نگهداری طیور` · `خدمات کنتور برق/ نصب انواع کنتورهای برق/…` · `خدمات ازمایشگاهی حوزه خاک/…` |
| `Vat` | Number: 999,999 integers and 1 decimal (`13.33`) | 0 | 19 | `10`, `0`, `65` |
| `Taxable` | Category text | 0 | 3 | `مشمول`, `معاف`, `غیر مشمول` |
| `RunDate` | Jalali date, from 1401-07-24 to 1405-07-01 | 0 | 635 | `1405-07-01`, `1405-06-31`, `1405-06-30` |
| `ExpirationDate` | Jalali date, from 1402-06-22 to 1405-06-31. **Mostly empty.** | 943,425 | 161 | `1402-12-29`, `1403-12-26`, `1404-09-09` |
| `CreateDate` | Jalali date, from 1403-11-17 to 1405-06-31 | 0 | 246 | `1405-06-31`, `1405-06-30`, `1405-06-29` |
| `LastEditDate` | Jalali date, from 1401-07-24 to 1405-06-31 | 0 | 598 | `1405-06-31`, `1405-06-30`, `1405-06-29` |
| `Type` | Category text | 0 | 6 | `شناسه اختصاصی وارداتی`, `شناسه عمومی تولید داخل`, `شناسه اختصاصی خدمت` |
| `PricingDescription` | Free text: a reason or reference for the rate decision. **Almost always empty.** | 999,984 | 7 | `به موجب تبصره بند (ب) ماده (9) قانون مالیات بر ارزش افزوده مصوب 1400` · `بموجب تاییدیه اداره کل امور مالیاتی غرب تهران طی تیکت …` · `با توجه به عدم ارائه مجوزهای مربوطه …` |

Value counts for the low-cardinality columns:

**`Type`**

| Value | Rows | ID prefix(es), first 3 digits |
|---|---|---|
| شناسه اختصاصی وارداتی | 501,459 | `280` (496,839), `200` (4,620) |
| شناسه اختصاصی تولید داخل | 458,860 | `290` (456,152), `291`, `222`, `280`, `200` |
| شناسه اختصاصی خدمت | 35,685 | `233` |
| شناسه عمومی وارداتی | 2,052 | `271` |
| شناسه عمومی تولید داخل | 1,936 | `272` |
| شناسه عمومی خدمت | 8 | `233` |

All 1,000,000 IDs start with `2`.

**`Taxable` crossed with `Vat`**

| Pair | Rows |
|---|---|
| 10 / مشمول | 885,197 |
| 0 / معاف | 55,683 |
| 9 / مشمول | 47,778 |
| 65 / مشمول | 4,097 |
| 50 / مشمول | 4,044 |
| 90 / مشمول | 1,473 |
| 16 / مشمول | 908 |
| 45 / مشمول | 200 |
| 40 / مشمول | 165 |
| 15 / مشمول | 115 |
| 35 / مشمول | 92 |
| 0 / غیر مشمول | 78 |
| 60 / مشمول | 75 |
| **0 / مشمول** | **32** |
| 30 / مشمول | 32 |
| 55 / مشمول | 16 |
| 1 / مشمول | 6 |
| 80 / مشمول | 5 |
| 85 / مشمول | 2 |
| 13.33 / مشمول | 1 |
| 75 / مشمول | 1 |

Observations (no legal interpretation):
- Every `معاف` and every `غیر مشمول` row has `Vat = 0`.
- 32 rows are `مشمول` with `Vat = 0`.
- `TODO(legal)`: `Vat` values such as 65, 50, 90, 45, 40 and 35 are unlikely to all be the general VAT rate. The column may include other duties, or a combined rate. What the `Vat` column means must be confirmed before it is used as `vat_rate`.
- `TODO(legal)`: what separates `معاف` from `غیر مشمول`.

## 3. Key questions

| Question | Answer | Confidence |
|---|---|---|
| VAT rate column? | **Yes**: `Vat`, filled on every row. What it means is `TODO(legal)` (see above). | Column existence **verified**. Semantics **not verified**. |
| Exempt flag? | **Yes**: `Taxable`, with 3 values `مشمول` / `معاف` / `غیر مشمول`, filled on every row | **verified** |
| Validity / execution dates? | **Yes**: `RunDate` (always set) and `ExpirationDate` (set on 56,575 rows, empty otherwise). Also `CreateDate` and `LastEditDate`. | **verified** (columns). Reading them as a validity period: **partly verified**, see below. |
| General vs specific? | **Yes**: `Type` (عمومی / اختصاصی), combined with origin (تولید داخل / وارداتی / خدمت) | **verified** |
| Change history (repeated IDs)? | **Yes**: 33,117 IDs appear more than once (33,844 extra rows, max 5 rows per ID). | **verified** |

Details on the repeated IDs (all measured):
- **Mostly change history.** In 29,192 of the 33,117 repeated IDs, the rows differ only in `Vat`, `RunDate`, `ExpirationDate` and `LastEditDate`. The rate changes over time while the description stays the same.
  - `Vat` differs across rows in 31,134 IDs.
  - `Taxable` differs in 1,431.
  - `DescriptionOfID` differs in 13.
  - `Type` never differs.
- 33,037 of the 33,117 IDs have periods that follow each other without overlapping (`ExpirationDate` ≤ the next `RunDate`). This fits `valid_from = RunDate`, `valid_to = ExpirationDate`.
- 32,994 repeated IDs have exactly one row with an empty `ExpirationDate`. 38 have two such rows, and 85 have none.
- **Data-quality issues:**
  - 100 exact duplicate rows.
  - 141 duplicate `(ID, RunDate)` keys. That is 100 exact duplicates plus 41 conflicting rows.
  - 135 rows where `RunDate` > `ExpirationDate`.

## 4. ID column

- Length: 999,999 values are 13 characters. **1 value is 14 characters** (row 850,431, `Type` = شناسه اختصاصی خدمت). The raw value is not reproduced here.
- Characters: all 1,000,000 values are ASCII digits only.
- Uniqueness: 966,156 distinct IDs in 1,000,000 rows. The repeats are the history rows in §3.
- Prefix structure: in this file, the first 3 digits are fully determined by `Type` (table in §2). This is an observation from this file only, not a documented rule.

Confidence: **verified**.

## 5. Mapping to `goods_catalog` (architecture.md §5)

| Schema field | Source column | Notes |
|---|---|---|
| `sstid CHAR(13)` | `ID` | Store as a string. 1 value has 14 digits and needs a decision. |
| `title` | `DescriptionOfID` | Normalize with `src/text/normalize.py` before indexing. Specific IDs carry a company name in the text. |
| `group_path TEXT[]` | **none** | See the list below. |
| `id_kind` | `Type` | Has two dimensions: general vs specific, and domestic / imported / service. The schema has one field. Split it, or keep the raw value. |
| `vat_rate` | `Vat` | Blocked on `TODO(legal)`: what the column means (values like 65 / 50 / 90). |
| `is_exempt` | `Taxable` | Three values do not fit a boolean. `غیر مشمول` ≠ `معاف` is not decided (`TODO(legal)`). |
| `legal_basis` | `PricingDescription` | Only 16 of 1,000,000 rows. Effectively empty. |
| `valid_from` | `RunDate` | Convert Jalali to Gregorian, or store Jalali as well. |
| `valid_to` | `ExpirationDate` | Empty = open-ended (inferred, **partly verified**). |
| `source_url` | **none in file** | Fill from download metadata (site + filename + download date). |
| `PK(sstid, valid_from)` | `ID` + `RunDate` | **Violated by 141 rows** (100 exact duplicates + 41 conflicting). Needs a dedup rule. |

**Schema fields this file cannot fill:**
1. `group_path`
   - The file has no category or level code.
   - The site's filter tabs and its `GetProductClassList` API have a 3-level class tree, but that tree is not in this export.
   - Whether the ID digits encode the class is **not verified**.
2. `legal_basis`
   - Only 16 rows have `PricingDescription`.
   - For practical purposes it must come from the legal-text layer.
3. `source_url`
   - Not in the file; comes from download metadata.

**File columns with no schema field:**
- `CreateDate`
- `LastEditDate`
- the origin part of `Type` (domestic / imported / service)

## 6. Is this the complete catalog?

**Probably not. This looks like part 1 of several. Not verified.**

Evidence from the file content alone:
- **Exactly 1,000,000 data rows.** That is a round number, typical of an export split at a fixed row limit. It matches the `part_1` in the filename.
- **Rows are sorted by `CreateDate`, newest first.** Only 39 of 999,999 adjacent pairs break the order. The first row was created 1405-06-31.
- **The file ends in the middle of a large batch.** All of the last 100,000 rows have `CreateDate` = 1404-06-05. That date looks like a bulk load, though this is inferred. The final row belongs to that batch too, so the batch probably continues past the 1M cut.
- `RunDate` goes back to 1401-07-24. So IDs in use since 1401 are present only if they were created or re-loaded on or after about 1403-11-17 (the earliest `CreateDate`). Older IDs that were never re-loaded would be missing.
- Goods IDs dominate (≈963k of 1M). The site offered one file («فایل یک»), yet this file's name says `part_1`. The site may split one logical file into several parts on download.

The only way to verify is on the site. Check whether the download produced, or offers, a `part_2`, and compare the total row count if the site shows one.

## 7. Fake catalog for tests

`data/sample/fake_catalog.csv`:
- **invented** data with the same 10 columns and the same formats (UTF-8 with BOM, Jalali dates, ID prefixes by `Type`)
- 50 rows, including a few history rows and edge cases

The IDs and titles are made up, and the rates are illustrative placeholders. They are **not** statements about real tax rates. No real rows were copied.
