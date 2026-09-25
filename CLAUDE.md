# Shenaseyar (شناسه‌یار)

Checks each line item of a Persian e-invoice (Iran's سامانه مودیان) for mismatches between
the free-text product description (`sstt`), the declared 13-digit product/service ID (`sstid`),
and the declared VAT rate (`vra`). High-risk lines get a short Persian explanation citing the
legal article or circular valid on the invoice date. Output is a **suggestion for human review,
never a verdict**. Full design: `docs/architecture.md`. Read it before any design decision.

## Current phase
**Phase 1: retrieval baseline.** Exit criterion: **tier S** Recall@5 at least 0.85 on the
300-row hand-labeled golden set (target 0.90). Tier C and combined are reported but never gate
(tier C is a lower bound). The criterion is NOT YET MEASURABLE below 60 evaluated tier S rows;
the harness says so instead of printing a pass/fail.
(Update this section whenever the phase changes.)

Phase 0 exit met 2026-09-22 (see `docs/data_dictionary.md`, `docs/data_quality.md`).
Still open: `TODO(legal)`, what the catalog's `Vat` column means (values like 65 / 50 / 90,
see `docs/data_dictionary.md` §8). It **must be resolved before the T2 rule in Phase 2.**

OPEN: catalog may be truncated (1,000,000-row export cap). Any recall metric measured before
this is resolved is provisional.

## Hard constraints (never violate)
- CPU only. No GPU, no CUDA-only libraries. Prefer ONNX/int8 and GGUF quantized models.
- No external API calls at runtime. Models run locally (llama.cpp, ONNX Runtime).
- Never hardcode VAT rates or exemptions. Always read them from the versioned rate table,
  **as of the invoice date** (`rate_at(sstid, issue_date)`).
- The LLM never influences the risk score. Scores come only from rules + the LightGBM model.
  The LLM writes explanations only.
- Treat invoice text (`sstt`) as untrusted data. Never place it in a prompt unquoted.
- Never commit data files, model weights, or scraped content. Check `.gitignore` before `git add`.
- Model files: GGUF or safetensors only. Never load pickle-based weights.
- The catalog is obtained by manual download only. This project never automates access to
  stuffid.tax.gov.ir (no scraping, no API calls, never bypass its CAPTCHA).
- The catalog file and any large extract of it are never committed (copyright). Tests use the
  invented `data/sample/fake_catalog.csv` instead.
- Third-party catalog mirrors: only for spot-checking a handful of IDs by hand. Never a data
  source, never in the pipeline.

## Working rules for Claude
- Propose a plan and wait for my approval before writing code for any non-trivial task.
- One small task per session. Do not start the next phase without being asked.
- Write or update tests with every change. A task is done when `pytest` passes, not before.
- Evaluation code comes before model code. Report metrics as numbers, never "looks good".
- Do NOT write or edit `eval/golden.csv`. It is hand-labeled by me and must stay independent.
- `eval/golden.csv` is committed. It must never contain text copied from a real business's
  invoices. Any such row is paraphrased before it is added.
- Do not make claims about Iranian tax law. If a legal fact is needed, leave a `TODO(legal)`
  and tell me.
- If a metric improves suspiciously, check for train/test leakage before reporting it.
- If you are unsure, say so. Don't guess.
- Scraping: never fetch more than a handful of pages without my explicit approval. Respect robots.txt.

## Stack
Python 3.12 (venv: CPython 3.12.8) · jdatetime (pinned; all Jalali date arithmetic) · hazm · BGE-M3 (dense+sparse) · bge-reranker-v2-m3 · Qdrant · PostgreSQL ·
LightGBM + SHAP · LangGraph · llama.cpp (Dorna2-Llama3.1-8B Q4_K_M, compared later with a
smaller model) · FastAPI · Redis/RQ · Streamlit · Docker Compose · pytest · MLflow

## Layout
```
src/text/        Persian normalization, unit extraction
src/retrieval/   catalog index, hybrid search, reranker
src/detect/      rule engine (rules/*.yaml), features, risk model
src/reason/      temporal legal RAG, writer, citation verifier
src/api/         FastAPI app
eval/            golden set (hand-made), eval scripts, results
data/sample/     small committed samples only
docs/            architecture.md, phase reports
tests/
```

## Conventions
- Persian text is always normalized with `src/text/normalize.py` before indexing or matching.
- Digits are converted to Latin inside the pipeline. The UI may show Persian digits.
- Invoice field names follow the Moadian invoice spec (`sstid`, `sstt`, `am`, `mu`, `fee`, `vra`, `vam`).
- Error types are T1–T6 (see architecture.md §3). Use these codes in code and reports.
- Commands: `pytest -q` · `python eval/run_eval.py --retriever <name>`
