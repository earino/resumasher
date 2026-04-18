# Capstone: Inventory Optimization for FirmX

Client: FirmX, a Central European multi-category retailer (~200 stores).
Duration: 6 weeks, team of 3 CEU students.
Advisor: Prof. Harder.

## Goal

FirmX asked us to reduce their SKU-level forecast error for a subset of 2,400
fast-moving items across 4 distribution centers. Baseline (their in-house
Excel model): MAPE of 34%.

## Approach

1. Data pull: 24 months of weekly POS data + supplier catalog (PostgreSQL export).
2. Feature engineering: lag-4, lag-52, seasonality encoding, promo calendar.
3. Model: ensemble of Prophet + XGBoost residuals (inspired by M5 competition notes).
4. Evaluation: walk-forward backtest on the last 13 weeks.

## Results

- MAPE reduced from 34% to 22% (12pp improvement) on the hold-out weeks.
- Streamlit dashboard delivered to the FirmX analytics team.
- Final report (39 pages) delivered to FirmX leadership.

## Files

- `notebook.ipynb` — data prep + feature engineering + model training.
- `final-report.pdf` — 39-page consulting-style report.
- `dashboard.py` — Streamlit app shown in the client walkthrough.
- `backtest-results.md` — numeric results across the walk-forward.
