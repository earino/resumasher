# Capstone Backtest Results

Walk-forward on weeks 101-113 of the cleaned FirmX dataset.

| Model | MAPE | MAE | Notes |
|-------|------|-----|-------|
| Baseline (FirmX Excel) | 34.0% | 142 units | Simple moving avg |
| Prophet solo | 28.2% | 118 units | Captures seasonality |
| XGBoost solo | 25.9% | 108 units | Handles promo calendar |
| Prophet + XGBoost residuals | **22.1%** | **92 units** | Our final pick |
| LightGBM (ablation) | 23.4% | 98 units | Simpler, slightly worse |

## Key findings

- ~40% of error reduction came from promo calendar encoding alone.
- Lag-52 feature mattered much more than lag-4 (annual seasonality dominates).
- 2 SKUs with near-zero demand dragged MAPE up; we carved them out of reporting.
