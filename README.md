# Shenaseyar (شناسه‌یار)

Checks each line item of a Persian e-invoice (سامانه مودیان) for mismatches between the product
description, the declared 13-digit product/service ID and the declared VAT rate.
Output is a suggestion for human review, never a verdict.

Status: Phase 0 (data feasibility). Design: [docs/architecture.md](docs/architecture.md).
Full bilingual README comes in phase 5.

## Data policy
- The product/service ID catalog is obtained by **manual download only** from
  stuffid.tax.gov.ir. This project never automates access to it.
- The catalog file and any large extract of it are **never committed** (copyright).
  Put it in `data/catalog/` (gitignored). Tests use the invented
  [data/sample/fake_catalog.csv](data/sample/fake_catalog.csv).
- File layout: [docs/data_dictionary.md](docs/data_dictionary.md).

## Tests
```
pip install -r requirements-dev.txt
pytest -q
```
