# RetailCast India — Demand Forecasting Pipeline

## Overview

A stratified demand forecasting pipeline for 60 product-store series across 6 products and 10 stores in India (Maharashtra, Karnataka, Tamil Nadu). Produces a 28-day daily forecast.

## Architecture

Three model strategies based on series characteristics:

| Series Class | Products | Strategy |
|---|---|---|
| Regular (40 series) | ATTA, CHARGER, DETERGENT, PICKLE | LightGBM with recursive multi-step, blended 65/15/20 with seasonal naive and vendor signal |
| Regime-Changed (10) | AGARBATTI | LightGBM on truncated history (d_1314+), blended 55/20/25 with naive and vendor |
| Intermittent (10) | CABLE | Day-of-week stratified recent mean (last 90 days), blended 80/20 with vendor signal |

## Key Data Decisions

- **market_signal.csv**: EXCLUDED — confirmed data leakage (r=0.942 correlation with actual sales, perfect zero-concordance, only covers training period, ~10.5x scale factor)
- **vendor_signal.csv**: INCLUDED as 20% blend component — legitimate external baseline forecast (r=0.778 with sales, covers full horizon d_1914–d_1941, beats naive on holdout RMSSE 0.929 vs 1.088)
- **AGARBATTI**: Training truncated to post-regime period (d_1314+) — sales were near-zero before this
- **CABLE**: No ML model — too sparse (58-80% zeros); simple DOW-mean is more robust
- **ATTA trend**: Recent +49% surge captured via trend_ratio feature and lag structure

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run pipeline (data must be in ../starter_kit 4/data/ or specify --data)
python3 pipeline.py --data ../starter_kit\ 4/data --out submission.csv
```

## Data Layout

The pipeline expects data files in the specified data directory:
```
data/
├── sales_train.csv      # 60 series × 1913 days
├── calendar.csv         # Day index → date, events, SNAP flags
├── sell_prices.csv      # Weekly prices per item per store
├── market_signal.csv    # NOT USED (leakage)
└── vendor_signal.csv    # Used as 20% blend component
```

## Output

`submission.csv` — 60 rows, columns `id, F1, F2, ..., F28` (non-negative numeric forecasts).

## Validation

```bash
python3 validate_format.py --submission submission.csv
```

## Dependencies

- Python 3.11+
- pandas 2.1.4
- numpy 1.26.2
- lightgbm 4.2.0

## Submitting — the three artifacts

1. **Repo link** — this code, which reads the data and regenerates `submission.csv`
2. **Claude chat export (.md)** — investigation and planning conversation
3. **Approach summary** — technical decision log (max 1,500 words)
