# Phase 0, step 1: access to the product/service ID catalog (شناسه کالا و خدمت)

Investigation date: 2026-09-22 (the site's header showed سه شنبه 31 شهریور 1405).
Method: Claude's built-in browser for stuffid.tax.gov.ir, plus WebSearch/WebFetch for mirrors.
No files were downloaded. No CAPTCHA was attempted. No project code was written.

**Fetch budget (limit 5):**

| # | What was fetched | How |
|---|---|---|
| 1 | `https://stuffid.tax.gov.ir/` | Browser. This is a single-page app, so clicking its four tabs made no new page loads. The page itself sent the XHR/GraphQL calls listed below. |
| 2 | `https://stuffid.tax.gov.ir/robots.txt` | Browser |
| 3 | `https://tadvinhesab.com/stuffid/` | WebFetch (a small model summarizes the page, so this is not a raw read) |
| 4 | `https://www.hesabdary.com/@HesabdaryCom/20389/` | WebFetch (same caveat) |
| – | One web search query (it returns a list of results, not a page fetch) | WebSearch |

The fifth fetch was not used.

The site **was reachable** from here. Every request returned HTTP 200, and there was no geo-block.

---

## 1. Bulk download on stuffid.tax.gov.ir

The site is titled «سامانه دریافت شناسه کالا و خدمت» and has four tabs:

1. **دریافت فایل‌های کل شناسه کالا و خدمت** (full-catalog file)
   - Help text on the page: «این تب تمامی شناسه‌های سامانه (شامل شناسه کالا و خدمت) را با تغییرات آن‌ها نمایش می‌دهد.»
   - File type choices: فایل کل شناسه خدمت / فایل کل شناسه کالا / فایل کل شناسه خدمت و شناسه کالا
   - Format choices: **CSV** or **XML**. There is no Excel/ZIP option in the UI.
   - "Output files" dropdown: filled from the API `FilesCount` (see §2). It returned only `انتخاب همه` (`all`) and `فایل یک` (guid `73fc4f9f-030e-4d9c-4ce7-08df163e811f`). So today the full catalog seems to be a single file.
   - Before the «دریافت فایل» button there is a **«من ربات نیستم» CAPTCHA**.
2. **دریافت فایل شناسه بر اساس فیلتر سطح** (filtered file)
   - Filters: کالا/خدمت, سطح یک / سطح دو / سطح سه, سرفصل, شرح شناسه, and a date range on «تاریخ اجرای شناسه».
   - CSV/XML output. Same CAPTCHA.
3. **دریافت شناسه بر اساس کد و شرح شناسه** (by code or description)
   - Inputs: کد شناسه or شرح شناسه (at least one), plus the same date range.
   - The **output is a file too**. The page shows no results table. Same CAPTCHA.
4. **راهنما** (help)
   - Title: «راهنمای فایل خروجی (بر اساس فایل پیوست Excel)».
   - Body: «اطلاعاتی برای نمایش وجود ندارد». The file-format guide was empty when checked.

**Exact URL:** I found no static download URL. The download is created on the fly after the CAPTCHA, through the GraphQL gateway (`/portal-gateway/StuffRate/gs/graphql`, inferred). I did not click «دریافت فایل», so I did not observe the actual download request or its URL.

Confidence:
- A bulk CSV/XML download exists behind a CAPTCHA: **verified** (UI observed).
- The full catalog is one file today: **partly verified** (from the `FilesCount` response only).
- Download URL and mechanism: **not verified**.

## 2. JSON API behind the page

Configuration served in `/env.js`: `VITE_APIURL: "/portal-gateway"`, `VITE_AUTH_GUEST: "auth/gs"`.
The page sends POST requests to GraphQL endpoints. Observed:

| Endpoint | Operation (from response root) | What it returned |
|---|---|---|
| `POST /portal-gateway/auth/gs/graphql` | `identity_create_session` | An opaque encrypted session token (guest session) |
| `POST /portal-gateway/auth/graphql` | `identity_user_info` | `statusCode: 401`, «کاربر گرامی لطفاً وارد حساب کاربری خود شوید» (no login, so guest) |
| `POST /portal-gateway/auth/gs/graphql` | `config_list` | `login.showCaptcha: 0`, upload limits |
| `POST /portal-gateway/StuffRate/gs/graphql` | `FilesCount` | `{fileTitle, fileGuid}` list (see §1) |
| `POST /portal-gateway/StuffRate/gs/graphql` | `GetProductClassList` | Level-1 category list |

A real response, `GetProductClassList` (abridged: the first two of 43 items):
```json
{"data":{"GetProductClassList":{"data":{"items":[
  {"code":"500000","parentCode":"000000","title":"مالکیتها و امتیازات","classLevel":1},
  {"code":"420000","parentCode":"000000","title":"قطعات جنرال مکانیکی، هیدرولیکی، پنوماتیکی، الکتریکی و الکترونیکی","classLevel":1}
 ],"bottomItems":null,"hasNext":false,"totalCount":null,"pageCount":null},
 "message":"Success","statusCode":200,"requestId":null,
 "subStatuses":[{"subject":null,"subStatusCode":2,"message":"عملیات با موفقیت انجام شد","subActionType":"SHOW_TOAST"}],
 "mode":"SYNC"}}}
```
The 43 level-1 codes run from `010000` (مواد غذایی) to `500000` (مالکیتها و امتیازات). Each has `parentCode` `000000`.

Gaps:
- **I could not see the request bodies.** The browser tool returns responses only, so the exact GraphQL query text and variables are unknown.
- **No per-item search endpoint was observed.** Every per-item lookup ends in a CAPTCHA-gated file download. So I can't show a request/response containing 13-digit IDs.
- The endpoint's name `StuffRate` suggests rates are involved, but that is only a name.

Confidence:
- GraphQL gateway, the operations above and the category-response fields: **verified**.
- A JSON API that returns item-level records without the CAPTCHA: **not verified** (none observed).

## 3. Fields per item

| Field wanted | Status |
|---|---|
| 13-digit ID | Not observed in any official response |
| Title | Only for categories (`title`) |
| Group/category | Category tree fields observed: `code` (6 digits), `parentCode`, `title`, `classLevel`. The UI has levels 1/2/3 plus «سرفصل». |
| General vs specific (عمومی/اختصاصی) | Not observed on the official site |
| VAT rate | Not observed on the official site |
| Exempt flag | Not observed on the official site |
| Validity dates | The UI filters on «تاریخ اجرای شناسه». The full-file tab says it includes «تغییرات آن‌ها», which suggests change history. The actual columns were not observed. |

The official file layout is unknown. Its help tab («بر اساس فایل پیوست Excel») was empty.

Confidence:
- Category fields: **verified**.
- That the files carry an execution date and change history: **partly verified** (UI text only).
- ID, general/specific, VAT rate, exempt flag and validity dates per item: **not verified**.

## 4. robots.txt and terms of use

`https://stuffid.tax.gov.ir/robots.txt`, quoted in full:
```
# https://www.robotstxt.org/robotstxt.html
User-agent: *
Disallow:
```

Terms of use:
- I saw no terms-of-use page or link on the site.
- The only legal text on the page is the footer: «© کلیه حقوق این سامانه متعلق به سازمان امور مالیاتی کشور می‌باشد.»
- The CAPTCHA («من ربات نیستم») sits in front of every download.

These are reported as found, without interpretation.

Confidence:
- robots.txt content: **verified**.
- "No terms page": **partly verified**. I only checked the single SPA page, with no site-wide search.

## 5. Third-party mirrors

From one web search. I opened two results via WebFetch (a summarizing model, not a raw read).

- **tadvinhesab.com/stuffid/** («جستجوی آنلاین شناسه کالا و خدمات سامانه مودیان + نرخ مالیات ارزش افزوده»)
  - Online search only; no download stated.
  - Columns: ردیف، شناسه کالا و خدمات، نام کالا و خدمات، نوع، ارزش افزوده.
  - Filters for general/specific and domestic/import, and for VAT rate values that the page itself lists (0, 5, 9, 13%). These are the site's claim; I have not checked them. TODO(legal)
  - **Source:** only a vague claim, «دسترسی مستقیم به کامل‌ترین بانک اطلاعاتی وزارت صمت و سازمان مالیاتی».
  - **Date:** not stated.
- **hesabdary.com/@HesabdaryCom/20389/** («فایل کامل شناسه کالا و خدمت»)
  - An .xlsx «تا مورخه ۳۱ اردیبهشت ۱۴۰۲», post dated 1402/03/02, «۵۵۰.۰۰۰ ردیف».
  - Columns: not described.
  - Source: not explicitly stated.
  - Download link: the summarizer gave it only in mangled form (`hesabdary.com/wp-content/uploads/2023/05/...1402.xlsx`), so I have **no exact URL**.
  - The file is about 3.3 years old.
- Listed in search results but not opened: kariyahesab.com/stuffid-tax-gov, liyam.cloud (an «ابزار رایگان» for ID lookup), irancodee.com, payeshhesab.ir, ezfactor.ir, keysuntsp.com, thdorsan.com.

Confidence:
- Mirrors exist: **verified**.
- Their fields, source and date as reported above: **partly verified** (summarized, not raw).
- Accuracy of any mirror's VAT data: **not verified**.

---

## Open questions for Navid

1. Can you (a human) pass the CAPTCHA once, download «فایل کل شناسه کالا و خدمت» as CSV, and put it outside the repo? That single file would answer §3: the columns, row count, and whether VAT rate, exempt flag and dates are in it. I did not and will not solve the CAPTCHA.
2. If the official file has **no VAT rate or exempt flag**, where should `vat_rate` / `is_exempt` come from? Options: a separate official source such as the Moadian portal (not the catalog), mirrors (source unclear), or mapping from law text by hand. The architecture assumes the catalog provides them.
3. Is it acceptable to rely on a human-downloaded snapshot of an official file, re-downloaded periodically, with no automated access? The site shows a CAPTCHA and a copyright line, and robots.txt disallows nothing. The call on whether automated access is allowed is yours, not mine.
4. Should third-party mirrors be used at all, e.g. to cross-check? None that I opened names an official source together with a date.
5. The full-catalog tab says it includes «تغییرات آن‌ها». If the file really has change history and «تاریخ اجرای شناسه», it may directly feed the SCD2 `valid_from`/`valid_to` in `goods_catalog`. That needs confirming from the actual file.
6. Do you know of an official document describing the file layout? The help tab («راهنمای فایل خروجی بر اساس فایل پیوست Excel») was empty when checked.
