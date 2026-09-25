# Shenaseyar: Architecture (condensed from the design PDF v1.0)

Author: Navid Abdollahzadeh. Source of truth for design decisions. If code and this file
disagree, stop and ask. Don't silently pick one.

## 1. Problem
Every line of a Moadian e-invoice has a free-text description (`sstt`), a 13-digit product/service
ID (`sstid`) and a VAT rate (`vra`). The rate is determined by the ID. Choosing a wrong ID,
by accident or on purpose, produces wrong tax. Much of the mismatch is hidden in the **text**, so
it is an NLP problem.

Unit of analysis: one invoice line. Output: risk score 0–1, error type(s), top-5 alternative IDs,
and for high-risk lines a Persian explanation with verified legal citations.

## 2. Design principle
"Deterministic things use rules, semantic things use retrieval, and only the explanation uses an LLM."
This keeps CPU cost low and every decision traceable.

## 3. Error types (targets)
| Code | Meaning | Detected by |
|---|---|---|
| T1 | Description doesn't match declared ID (e.g. a good that charges VAT filed under an ID with `charges_vat = false`) | retrieval + risk model (core NLP) |
| T2 | Declared rate ≠ reference rate of the ID on invoice date | rule |
| T3 | Generic/"other" ID used where a specific ID exists | retrieval + rule |
| T4 | Vague description ("کالا", "خدمات") with high amount | rule + feature |
| T5 | Unit price far outside the ID group's distribution | feature (robust z-score) |
| T6 | `vam` ≠ base × `vra` | rule |
| `NOT_IN_CATALOG` | Separate from T1–T6. **Severity: high.** The declared `sstid` does not exist in the catalog. Rationale: an invoice quoting a non-existent ID is the seller's error, and an error in itself, not a rate question. | rule (`rate_at` → `NOT_IN_CATALOG`) |
| `NOT_IN_FORCE` | Separate from T1–T6. **Severity: medium.** The `sstid` exists, but no version is in force on the invoice date (a gap between versions, or a date before the first version). Rationale: it could be a genuinely wrong ID, or just a lag around a version change. It warrants review, but is not a strong signal on its own. | rule (`rate_at` → `NOT_IN_FORCE`) |

| `AMBIGUOUS` | Separate from T1–T6. **Severity: none (informational). Do not upgrade.** Several versions are in force on the invoice date and tie on the latest `valid_from` (§5). Rationale: this is **our** data problem (two catalog rows in force for one ID), not the seller's. It must **never raise a line's risk score**. It is logged as a catalog-maintenance issue with its own counter in the run report. | rule (`rate_at` → `AMBIGUOUS`) |

ID-status findings (`NOT_IN_CATALOG`, `NOT_IN_FORCE`, `AMBIGUOUS`):
- **Never** fall back to a nearby version or to "no VAT due". A missing rate is never treated as a zero rate.
- T2 does not fire for any of them. The line gets its own finding, not a rate mismatch.
- The reviewer view shows the raw status text, never a guessed rate.
- Every detection run report records the count of each.
- Caveat: while the catalog-truncation question is open (CLAUDE.md), some `NOT_IN_CATALOG` findings
  may be caused by missing catalog rows, not by the invoice.
- OPEN (Navid): which status applies to an ID whose rows were **all** quarantined
  (`docs/data_quality.md`). Such an ID is not in the active index but does exist in the source.

Tax status: the catalog separates three statuses, `taxable` (مشمول), `exempt` (معاف) and
`out_of_scope` (غیر مشمول). **Detection** (rules, features such as `exempt_flip`, the risk model)
uses only the derived boolean `charges_vat`. The **explanation layer** must use `tax_status`, so it
never presents `exempt` and `out_of_scope` as the same thing.

## 4. Layers
1. **Data**: catalog (`stuffid.tax.gov.ir`, manual download only, see `docs/data_dictionary.md`), versioned rate table,
   VAT law 1400 (esp. art. 9 exemptions), Moadian law & bylaws, circulars, annual budget law rate
   (**sources conflict on the 1405 rate, so it's a table parameter, never a constant**), invoice spec,
   synthetic invoices.
2. **Ingest & index**: normalization (hazm, Parsivar for comparison), unit extraction, legal
   texts chunked by article/note/clause (ماده/تبصره/بند) with `valid_from`/`valid_to`/`superseded_by`,
   BGE-M3 dense+sparse vectors in Qdrant. Scrapy/PyMuPDF/Tesseract(fas) to collect legal docs.
3. **Detect** (runs on all lines, must be cheap): YAML rule engine → hybrid retrieval (dense+sparse,
   RRF, top-20) → bge-reranker-v2-m3 (top-5) → calibrated LightGBM + SHAP.
4. **Reason & cite** (only lines above threshold): LangGraph graph with **one** LLM-calling agent.
   match (det.) → temporal legal RAG (retrieval only, hard filter on invoice date) → writer
   (llama.cpp + GBNF-constrained JSON) → citation verifier (exists / temporally valid / verbatim quote
   ≥0.9 fuzzy / supported) → retry once → else **safe-fail**: evidence only, "needs human review",
   no generated text.
5. **Serve**: FastAPI (`/check-item`, `/batch`, `/explain/{id}`, `/feedback`), RQ worker,
   llama.cpp server, Qdrant, Postgres, Redis, Streamlit UI, Prometheus/Grafana, MLflow. Docker Compose.

Cross-cutting **governance**: local-only execution, HMAC-hashed seller IDs, PII masking, RBAC,
hash-chained audit log, human in the loop, model cards, alert-rate monitoring by sector.

## 5. Core schema
```sql
goods_catalog(sstid CHAR(13), title, group_path TEXT[], id_kind, vat_rate,
              tax_status ENUM('taxable','exempt','out_of_scope'),   -- مشمول / معاف / غیر مشمول
              charges_vat BOOLEAN GENERATED AS (tax_status = 'taxable'),
              legal_basis, valid_from, valid_to_excl, source_url, PK(sstid, valid_from))  -- SCD2, half-open
invoice_item(item_id, invoice_id, issue_date, seller_hash, sstid, sstt, am, mu, fee,
             vra, vam, label_types TEXT[])   -- label_types only in synthetic data
legal_unit(unit_id, doc_type, title, body, valid_from, valid_to, superseded_by, source_url)
```
Field names must be checked against the current Moadian spec in phase 0.

**Validity dates: source vs code convention.**
- **Source** (catalog export): `ExpirationDate` is **inclusive**, the last day the version is in force.
  Measured in `docs/data_dictionary.md` §9: 33,319 consecutive-version pairs follow the
  "+1 day" pattern and 0 follow the "same day" pattern.
- **Code**: half-open intervals `[valid_from, valid_to_excl)`. The loader converts once:
  - `valid_from = RunDate`
  - `valid_to_excl = ExpirationDate + 1 day` (Jalali arithmetic via `jdatetime`, pinned)
  - empty `ExpirationDate` → `valid_to_excl = NULL` (open-ended)
- After loading, no code may add or subtract a day on these fields.
- `issue_date` and the validity fields must be compared in the same calendar. Where the conversion
  happens is decided in the loader plan.

**`rate_at(sstid, issue_date)`** uses the rows of `sstid` in force on `issue_date`, i.e.
`valid_from ≤ issue_date < valid_to_excl`, with `valid_to_excl = NULL` meaning no end:
- one row: return its rate and status.
- several rows: take the one with the latest `valid_from`.
- still tied: return an explicit **`AMBIGUOUS`** result, not a rate. With `AMBIGUOUS`, the T2 rule
  does not fire and the line is marked for human review.
- no row in force: return an explicit **`NOT_IN_FORCE`** result, not a rate. This happens when the
  date falls in a gap between versions (341 gaps measured) or before the first version.
- `sstid` not in the catalog at all: return an explicit **`NOT_IN_CATALOG`** result, not a rate.
- Never pick arbitrarily, never silently take the first row, and never fall back to the nearest version.
- How `NOT_IN_FORCE`, `NOT_IN_CATALOG` and `AMBIGUOUS` are treated (no fallback, T2 does not fire,
  own finding, raw status shown to the reviewer, counted in the run report): see §3.

The tie rule applies to the 3 IDs that have more than one row in force (`docs/data_quality.md`).
Catalog source columns and their mapping: `docs/data_dictionary.md` §5. Rows that fail the
data-quality rules are not in `goods_catalog`. They go to a quarantine list with a reason code
(`docs/data_quality.md`).

## 6. Risk-model features
`sim_declared`, `rank_declared` (21 if absent), `margin_top1`, `exempt_flip`, `is_general_id`,
`specific_exists`, `desc_specificity`, `price_z` (median-based), `injection_flag`, `rule_hits`,
`id_status` (categorical: `ok` | `not_in_force` | `not_in_catalog` | `ambiguous`, from `rate_at`;
`ambiguous` is the §5 tie case).
**DECIDED (Navid, 2026-09-25): the model never sees `ambiguous`.** At the feature boundary it is mapped
to `ok`, and it is recorded only in the catalog-maintenance counter. So the model's `id_status`
values are `ok` | `not_in_force` | `not_in_catalog`. Rationale: `ambiguous` is a defect in our data
(3 known IDs), and it carries no signal about the seller. A monotone constraint would add complexity
to guard against a value the model should never receive in the first place.
Threshold is set by **review capacity** (e.g. top 2% per period); report Precision@k at that point.

## 7. Synthetic data
Seeded, reproducible generator: stratified ID sampling (weight groups mixing exempt/taxable),
realistic descriptions (templates + offline LLM paraphrase + noise: typos, Arabic ي/ك, missing
half-space, abbreviations, mixed script), log-normal prices, ~15% injected errors T1–T6. T1 is built
from "misleading neighbors" (lexically close, different rate). Random **and** group-based splits.
**Golden set: 300 lines written and labeled by hand, independent of the generator** (two tiers, S and C; §10). It's the final
reference for accuracy. Known weakness: evaluating on self-generated data is circular. Real
anonymized lines, even 100, would be the biggest improvement.

## 8. Security (CIA triad)
- **Integrity (highest priority)**: prompt injection via `sstt` → LLM excluded from scoring, text
  quoted as data, GBNF output, verifier, `injection_flag` raises risk. Adversarial evasion via the
  pre-submission tool → show suggested ID only, never the score. Rate-limit and retrain. *Managed,
  not solved.* Reference-data tampering → signed, reviewed, versioned changes. Hash-chained audit
  log. Model supply chain → GGUF/safetensors only, checksums, pinned deps.
- **Confidentiality**: LLM context = one line + legal texts only. Retrieval filtered by
  `seller_hash`. PII masked before logging. Dashboard groups < 10 sellers suppressed.
- **Availability** (low priority): upload size/row caps, rate limit, degrade gracefully (scores
  without explanations if the LLM is down).
- Red-team set: 30 injection descriptions. Targets: score change = 0, manipulated explanations
  passing the verifier = 0.

## 9. Roadmap (exit criterion gates the next phase)
| Phase | Weeks | Deliverable | Exit criterion |
|---|---|---|---|
| 0 | 1 | Data-source report, initial catalog subset | Usable catalog with rate/exempt, or narrowed scope |
| 1 | 2–4 | Normalizer, BM25/dense/hybrid/rerank, golden set, eval notebook | Recall@5 at least 0.85 (target 0.90) |
| 2 | 5–6 | Synthetic generator, rules, LightGBM | PR-AUC at least 0.80 on group split. T2/T6 rules 100% |
| 3 | 7–9 | Legal RAG, graph, GBNF, verifier, model comparison | Citation accuracy at least 95%. Safe-fail rate at most 15%. All 20 deliberately wrong citations rejected |
| 4 | 10–12 | API, queue, UI, governance, Docker | 10,000 lines end-to-end on target machine |
| 5 | 13–14 | Report, video, bilingual README, cards | All numbers in docs replaced by measured ones |

The 14-week estimate is probably optimistic for one person. Expect 20–28.

## 10. Evaluation
Retrieval: Recall@1/@5, MRR (golden set). The harness (`eval/run_eval.py`) reports golden rows that
are missing from the catalog, quarantined-only, or invalid as separate buckets, never as misses.
Until the loader exists, the quarantined-only check reads NOT CHECKED (raw IDs; TODO(loader)).
Golden rows have a tier: S = exactly one correct ID, C = a class query with several acceptable IDs
(`;`-separated; a hit is any of them in the top k). Metrics are reported for S, C and combined. C is a
lower bound (the acceptable set is hand-made, never complete). The Phase 1 exit criterion gates on
tier S only, and only with at least 60 evaluated tier S rows. Why tiers: data_dictionary.md §10.
Risk: PR-AUC, Precision@2%, recall per T-type (group split).
Rules: 100% on injected T2/T6. Citations: existence, validity, support. Explanations: 1–5 by two
raters. Efficiency: p50/p95 latency, lines/hour.
Ablations: BM25 → dense → hybrid → +reranker. ± normalization/synonyms. Rules only vs full model.
Random vs group split. RAG ± temporal filter ± verifier. Dorna2-8B vs smaller model.

## 11. Open questions (unverified. Don't assume answers)
- Is the catalog downloadable in bulk? Terms of use?
- Exact current Moadian field names.
- Actual CPU latency/memory (all current numbers are estimates).
- Whether Dorna2 beats newer multilingual small models on this task. Decide by measurement.
- Which specific goods are exempt: read from law text, never from memory.
