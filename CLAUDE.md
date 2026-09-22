# Shenaseyar (شناسه‌یار)

Checks each line item of a Persian e-invoice (Iran's سامانه مودیان) for mismatches between
the free-text product description (`sstt`), the declared 13-digit product/service ID (`sstid`),
and the declared VAT rate (`vra`). High-risk lines get a short Persian explanation citing the
legal article or circular valid on the invoice date. Output is a **suggestion for human review,
never a verdict**. Full design: `docs/architecture.md`. Read it before any design decision.

## Current phase
**Phase 0: data feasibility.** Exit criterion: a usable product-ID catalog with VAT rate or
exempt flag per ID, or a documented decision to narrow scope to a few product groups.
(Update this section whenever the phase changes.)

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

## Working rules for Claude
- Propose a plan and wait for my approval before writing code for any non-trivial task.
- One small task per session. Do not start the next phase without being asked.
- Write or update tests with every change. A task is done when `pytest` passes, not before.
- Evaluation code comes before model code. Report metrics as numbers, never "looks good".
- Do NOT write or edit `eval/golden.csv`. It is hand-labeled by me and must stay independent.
- Do not make claims about Iranian tax law. If a legal fact is needed, leave a `TODO(legal)`
  and tell me.
- If a metric improves suspiciously, check for train/test leakage before reporting it.
- If you are unsure, say so. Don't guess.
- Scraping: never fetch more than a handful of pages without my explicit approval. Respect robots.txt.

## Stack
Python 3.11 · hazm · BGE-M3 (dense+sparse) · bge-reranker-v2-m3 · Qdrant · PostgreSQL ·
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
