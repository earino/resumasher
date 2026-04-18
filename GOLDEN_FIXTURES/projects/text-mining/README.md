# German Customer Email Sentiment Analysis

Course: Text Mining (Central Graduate School, Spring 2025).
Team: 2 students.

## Goal

Classify sentiment on ~50,000 German-language customer service emails from an
Austrian telco dataset (anonymized, released under CC-BY for coursework).

## Approach

- Baseline: TF-IDF + logistic regression. F1 macro = 0.71.
- Transformer: fine-tuned `bert-base-german-cased`. F1 macro = 0.84.

## Why it matters

Austrian / Swiss telco customer-service teams triage thousands of emails per
day. A reliable sentiment classifier lets them prioritize angry customers for
immediate response and file "neutral" messages for batch processing.

## Files

- `baseline.py` — TF-IDF + sklearn pipeline.
- `finetune.py` — HuggingFace fine-tuning script.
- `report.md` — write-up for the course submission.
