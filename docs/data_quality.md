# Data-quality rules: product/service ID catalog

- Source: the manually downloaded catalog export (see `docs/data_dictionary.md`), 1,000,000 data rows.
- Rules decided by Navid, 2026-09-22.
- **Principle: flag, never silently drop.** A row that is not in the active index goes to a **quarantine list** with a reason code. Nothing is deleted. The only rows removed are exact copies, and their count is recorded.
- All counts below were measured with full streaming passes over the file (the `data/catalog/` zip, not committed).
- "Data row N" means the N-th row after the header, counting from 1.

**Loader requirement:** the catalog loader must print every count in the summary table on every run. It is not written yet.

## Rules, in order of application

| # | Rule | Condition | Action | Reason code |
|---|---|---|---|---|
| R1 | Exact duplicate | Two or more rows identical in all 10 columns | Keep one copy and record `occurrences` | *(dedup, not quarantine)* |
| R2 | Conflicting version | After R1: more than one row with the same `(ID, RunDate)` | **All** rows of that key go out of the active index | `conflicting_version` |
| R3 | Invalid date range | `ExpirationDate` not empty and `RunDate` > `ExpirationDate` | Out of the active index | `invalid_date_range` |
| R4 | Invalid ID length | `ID` is not exactly 13 characters | Out of the active index. **Never truncated or "fixed".** | `invalid_id_length` |

- R1 runs first, on the whole file.
- R2–R4 are then checked independently on the deduplicated rows. A row can carry more than one reason, and the quarantine list stores **all** of them.
- The date comparison in R3 is a string comparison on Jalali `YYYY-MM-DD`. That is valid because the format is fixed-width.

## Summary (the loader must print these)

| Count | Value |
|---|---:|
| Input rows | 1,000,000 |
| R1: exact-duplicate groups | 100 (all pairs) |
| R1: extra copies removed | 100 |
| Rows after R1 | 999,900 |
| R2 `conflicting_version`: keys / rows | 41 / 82 |
| R3 `invalid_date_range`: rows (after R1) | 71 |
| R4 `invalid_id_length`: rows | 1 |
| Rows with two reasons (R2 + R3) | 7 |
| **Quarantined rows (distinct)** | **147** |
| **Active index rows** | **999,753** |
| **Active index distinct IDs** | **966,089** |

Checks:
- 999,900 − 147 = 999,753.
- 82 + 71 + 1 − 7 = 147.

## How the rules overlap

- **R1 × R3.** Before dedup, R3 matches 135 rows. 128 of them are copies within 64 exact-duplicate pairs. So after R1, R3 matches 71 distinct rows. In the quarantine list, each of those 64 rows has `occurrences = 2`.
- **R2 × R3.** 7 rows are in a conflicting group *and* have an invalid date range.
- **R2 and R1.** No conflicting group contains exact copies. All 41 groups are pairs of 2 different rows.
- **R4** overlaps nothing.

## Examples

### R1: exact duplicates (100 pairs)

| Data rows | ID | RunDate | ExpirationDate | Vat | Taxable | Title (cut) |
|---|---|---|---|---|---|---|
| 56999, 57001 | 2900412400826 | 1405-05-20 | 1405-05-19 | 0 | معاف | رزماری، اسیاب شده، نام تجارتی بی رقیب، … |
| 57000, 57002 | 2900412400819 | 1405-05-20 | 1405-05-19 | 0 | معاف | رزماری، اسیاب شده، نام تجارتی بی رقیب، … |
| 57006, 57007 | 2900412400840 | 1405-05-20 | 1405-05-19 | 0 | معاف | میخک، اسیاب شده، نام تجارتی بی رقیب، … |

These three pairs are also R3 cases: `ExpirationDate` is the day before `RunDate`.

### R2: `conflicting_version` (41 keys, 82 rows)

Which columns differ inside a group:

| Differing columns | Groups |
|---|---:|
| `ExpirationDate`, `LastEditDate` | 18 |
| `ExpirationDate` | 17 |
| `Vat`, `Taxable`, `ExpirationDate`, `CreateDate` | 3 |
| `Vat`, `Taxable`, `ExpirationDate`, `LastEditDate` | 3 |

So 35 groups differ only in dates, and 6 also differ in rate and tax status. Example:

| Data row | ID | RunDate | ExpirationDate | Vat | Taxable | Title (cut) |
|---|---|---|---|---|---|---|
| 39188 | 2330001048106 | 1405-05-21 | *(empty)* | 10 | مشمول | اعتبارات اسنادی ریالی/کارمزد قبول تعهد اعتبار اسنادی داخلی/ … |
| 76696 | 2330001048106 | 1405-05-21 | 1405-05-20 | 0 | معاف | *(same title)* |
| 39189 | 2330001048090 | 1405-05-21 | *(empty)* | 10 | مشمول | اعتبارات اسنادی ریالی/کارمزد قبول تعهد اعتبار اسنادی مدت دار/ … |
| 76694 | 2330001048090 | 1405-05-21 | 1405-05-20 | 0 | معاف | *(same title)* |

### R3: `invalid_date_range` (135 raw rows, 71 after R1)

In **all 135** raw rows, `ExpirationDate` is exactly one day before `RunDate` (checked with `jdatetime` 6.1.0). Under the inclusive convention (`docs/data_dictionary.md` §9), these are zero-length periods. The 8 most common date pairs:

| RunDate | ExpirationDate | Raw rows |
|---|---|---:|
| 1404-11-30 | 1404-11-29 | 38 |
| 1404-08-12 | 1404-08-11 | 38 |
| 1405-03-12 | 1405-03-11 | 26 |
| 1405-05-07 | 1405-05-06 | 10 |
| 1405-06-03 | 1405-06-02 | 8 |
| 1405-05-20 | 1405-05-19 | 6 |
| 1405-05-21 | 1405-05-20 | 3 |
| 1404-08-26 | 1404-08-25 | 2 |

The remaining 4 rows fall in 4 other date pairs, also exactly one day apart.

Raw rows by `Vat`/`Taxable`: 0/معاف 93, 10/مشمول 41, 30/مشمول 1.
By `Type`: شناسه اختصاصی تولید داخل 124, شناسه اختصاصی خدمت 7, شناسه عمومی وارداتی 2, شناسه عمومی تولید داخل 2.

### R4: `invalid_id_length` (1 row)

| Data row | ID length | RunDate | Type | Title (cut) |
|---|---:|---|---|---|
| 850431 | 14 | 1403-09-13 | شناسه اختصاصی خدمت | اماده سازی قبر/ … |

## Observations with no rule yet (reported, not acted on)

- **32 rows are `مشمول` with `Vat = 0`.** They stay in the active index. Now part of the `Vat` `TODO(legal)` (`docs/data_dictionary.md` §2).
- **Validity periods in the active index:**
  - 22,709 IDs have **no** open row: every row has an `ExpirationDate`.
  - 3 IDs have **more than one** open row. `rate_at` handles them by taking the latest `valid_from`, else `AMBIGUOUS` (`docs/architecture.md` §5). Before R1–R4 there were 38 such IDs; R1–R4 resolved the other 35. I did not break down which rule resolved which.
- These affect `rate_at(sstid, issue_date)` once lookups are built. There is no rule for them yet. It's Navid's call whether they need one.
