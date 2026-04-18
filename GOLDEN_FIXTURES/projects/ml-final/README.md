# ML Final Project: Anomaly Detection on Manufacturing Sensors

Course: Machine Learning (CEU Vienna, Fall 2024).
Team: solo.

## Goal

Compare isolation forest vs. autoencoder for detecting anomalies in a public
manufacturing sensor dataset (NASA turbofan engine degradation dataset, 4
different failure modes).

## Results

- Isolation forest: ROC-AUC = 0.87.
- Autoencoder (dense, bottleneck=8): ROC-AUC = 0.91.
- Autoencoder with LSTM encoder: ROC-AUC = 0.94.

## Files

- `anomaly.py` — both baselines.
- `autoencoder.py` — LSTM-encoder variant.
- `evaluation.md` — confusion matrices and PR curves.
