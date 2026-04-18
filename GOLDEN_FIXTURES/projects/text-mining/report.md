# Sentiment Analysis on German Customer Emails — Final Report

## Dataset

- 52,341 anonymized customer service emails from an Austrian telco.
- Labels: positive, neutral, negative (3-way).
- Class balance: 22% positive, 48% neutral, 30% negative.

## Baseline

TF-IDF (1-3 grams) + logistic regression with class weighting.
- F1 macro: 0.71
- Mostly confused positive vs. neutral.

## Transformer fine-tune

`bert-base-german-cased` fine-tuned for 3 epochs on an 80/10/10 split.
- F1 macro: 0.84
- Largest gain came on the negative class (F1: 0.68 → 0.89).

## Lessons

- German compound words (e.g., "Kundendienstmitarbeiter") hurt TF-IDF because the
  unigram vocabulary explodes; transformers don't care.
- Sarcasm remained hard for both models; human QA flagged it as the dominant
  residual error source.
