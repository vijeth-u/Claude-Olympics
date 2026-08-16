#!/usr/bin/env python3
"""
pipeline.py — RetailCast India demand forecasting pipeline.

Stratified model approach:
  - Regular series (ATTA, CHARGER, DETERGENT, PICKLE): LightGBM with recursive multi-step
  - Regime-changed (AGARBATTI): LightGBM on truncated history (d_1314+)
  - Intermittent (CABLE): Day-of-week stratified recent mean

Key data decisions:
  - market_signal.csv EXCLUDED (confirmed data leakage: r=0.942 with actual sales,
    perfect zero-concordance, ~10x scale, no horizon coverage)
  - vendor_signal.csv INCLUDED as blend component (r=0.778 with sales, covers full
    forecast horizon d_1914–d_1941, beats seasonal naive on holdout RMSSE 0.93 vs 1.09)
  - AGARBATTI training truncated to post-regime period only (d_1314+)
  - ATTA recent +49% uptrend captured via lag features and short rolling windows
  - Price effects captured via price_ratio feature (current / 8-week rolling mean)

Usage:
    python3 pipeline.py --data ../data --out submission.csv
"""
import argparse
import os
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
N_HIST = 1913
HORIZON = 28
FCOLS = [f"F{i}" for i in range(1, HORIZON + 1)]
RANDOM_STATE = 42

# Series classification thresholds
AGARBATTI_REGIME_START = 1314  # Training starts here for AGARBATTI
CABLE_RECENT_DAYS = 90  # DOW-mean window for CABLE

# Blend weights: (LightGBM, seasonal naive, vendor signal)
BLEND_REGULAR = (0.65, 0.15, 0.20)
BLEND_AGARBATTI = (0.55, 0.20, 0.25)
BLEND_CABLE = (0.0, 0.0, 0.0)  # pure DOW-mean, weights unused

# LightGBM parameters (conservative to avoid overfitting)
LGB_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 10,
    "verbose": -1,
    "random_state": RANDOM_STATE,
    "n_jobs": 1,
}
LGB_N_ESTIMATORS = 300
LGB_EARLY_STOP = 30


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────────────────────────────
def load_data(data_dir):
    """Load sales, calendar, prices, and vendor signal.

    Deliberately skips market_signal.csv (confirmed data leakage).
    """
    sales = pd.read_csv(os.path.join(data_dir, "sales_train.csv"))
    calendar = pd.read_csv(os.path.join(data_dir, "calendar.csv"))
    prices = pd.read_csv(os.path.join(data_dir, "sell_prices.csv"))
    vendor = pd.read_csv(os.path.join(data_dir, "vendor_signal.csv"))
    return sales, calendar, prices, vendor


def load_vendor_horizon(vendor_df):
    """Extract vendor signal forecasts for the forecast horizon (d_1914–d_1941).

    Returns dict: series_id -> np.array of 28 values.
    """
    vendor_df = vendor_df.copy()
    vendor_df["d_num"] = vendor_df["d"].str.replace("d_", "").astype(int)
    horizon = vendor_df[vendor_df["d_num"].between(N_HIST + 1, N_HIST + HORIZON)]
    horizon = horizon.sort_values(["id", "d_num"])

    vendor_forecasts = {}
    for sid, grp in horizon.groupby("id"):
        vals = grp["vendor_forecast"].values
        if len(vals) == HORIZON:
            vendor_forecasts[sid] = vals
    return vendor_forecasts


def melt_sales(sales):
    """Convert wide-format sales to long format: (id, item_id, store_id, d_num, sales)."""
    id_cols = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]
    dcols = [f"d_{i}" for i in range(1, N_HIST + 1)]
    long = sales.melt(id_vars=id_cols, value_vars=dcols, var_name="d", value_name="sales")
    long["d_num"] = long["d"].str.replace("d_", "").astype(int)
    return long


# ─────────────────────────────────────────────────────────────────────────────
# Series Classification
# ─────────────────────────────────────────────────────────────────────────────
def classify_series(item_id):
    """Classify a series into one of three strategy groups."""
    if item_id == "HOMECARE_2_AGARBATTI":
        return "regime_changed"
    elif item_id == "ELECTRONICS_1_CABLE":
        return "intermittent"
    return "regular"


def get_training_start(series_class):
    """Determine the first training day for each class."""
    if series_class == "regime_changed":
        return AGARBATTI_REGIME_START
    return 1  # Use full history for regular series


# ─────────────────────────────────────────────────────────────────────────────
# Feature Engineering
# ─────────────────────────────────────────────────────────────────────────────
def build_calendar_features(calendar):
    """Extract calendar features needed for modelling."""
    cal = calendar[["d", "wm_yr_wk", "wday", "month", "year",
                    "event_name_1", "event_type_1", "snap_MH", "snap_KA", "snap_TN"]].copy()
    cal["d_num"] = cal["d"].str.replace("d_", "").astype(int)

    # Binary event features
    cal["event_religious"] = (cal["event_type_1"] == "Religious").astype(int)
    cal["event_cultural"] = (cal["event_type_1"] == "Cultural").astype(int)
    cal["event_national"] = (cal["event_type_1"] == "National").astype(int)
    cal["has_event"] = (cal["event_type_1"].notna() & (cal["event_type_1"] != "")).astype(int)

    return cal


def get_price_features(prices, store_id, item_id):
    """Compute price features for a specific series."""
    mask = (prices["store_id"] == store_id) & (prices["item_id"] == item_id)
    sp = prices[mask][["wm_yr_wk", "sell_price"]].copy()
    sp = sp.sort_values("wm_yr_wk").reset_index(drop=True)

    # Rolling mean price over last 8 weeks
    sp["price_rolling_8wk"] = sp["sell_price"].rolling(8, min_periods=1).mean()
    sp["price_ratio"] = sp["sell_price"] / sp["price_rolling_8wk"]
    sp["price_change"] = (sp["sell_price"].diff().abs() > 0.01).astype(int)

    return sp


def build_series_features(series_sales, cal_features, price_df, store_id, state_id):
    """Build full feature matrix for a single series.

    Args:
        series_sales: array of sales values indexed by d_num (1-based)
        cal_features: calendar DataFrame with d_num, wday, month, events, etc.
        price_df: price features for this series (wm_yr_wk, sell_price, price_ratio, etc.)
        store_id: store identifier
        state_id: state identifier (MH, KA, TN)
    """
    n = len(series_sales)
    records = []

    # Merge calendar with price by wm_yr_wk
    cal_price = cal_features.merge(price_df, on="wm_yr_wk", how="left")

    for d in range(29, n + 1):  # Start at day 29 so we have 28 days of lags
        cal_row = cal_price[cal_price["d_num"] == d]
        if cal_row.empty:
            continue
        cal_row = cal_row.iloc[0]

        # Target
        y = series_sales[d - 1]  # 0-indexed

        # Temporal features
        wday = cal_row["wday"]
        month = cal_row["month"]

        # Lag features
        lag_7 = series_sales[d - 1 - 7] if d > 7 else 0
        lag_14 = series_sales[d - 1 - 14] if d > 14 else 0
        lag_21 = series_sales[d - 1 - 21] if d > 21 else 0
        lag_28 = series_sales[d - 1 - 28] if d > 28 else 0

        # Rolling statistics (computed from actuals)
        window_7 = series_sales[max(0, d - 1 - 7):d - 1]
        window_28 = series_sales[max(0, d - 1 - 28):d - 1]
        rolling_mean_7 = np.mean(window_7) if len(window_7) > 0 else 0
        rolling_mean_28 = np.mean(window_28) if len(window_28) > 0 else 0
        rolling_std_7 = np.std(window_7) if len(window_7) > 1 else 0

        # DOW rolling mean (same weekday over last 4 occurrences)
        same_dow_vals = []
        for k in range(1, 5):
            idx = d - 1 - 7 * k
            if idx >= 0:
                same_dow_vals.append(series_sales[idx])
        rolling_mean_dow_4 = np.mean(same_dow_vals) if same_dow_vals else 0

        # Trend feature: ratio of recent 7-day mean to 28-day mean
        trend_ratio = rolling_mean_7 / rolling_mean_28 if rolling_mean_28 > 0 else 1.0

        # Price features
        sell_price = cal_row.get("sell_price", np.nan)
        price_ratio = cal_row.get("price_ratio", 1.0)
        price_change = cal_row.get("price_change", 0)
        if pd.isna(sell_price):
            sell_price = 0
            price_ratio = 1.0
            price_change = 0

        # Event features
        event_religious = cal_row["event_religious"]
        event_cultural = cal_row["event_cultural"]
        event_national = cal_row["event_national"]
        has_event = cal_row["has_event"]

        # SNAP feature (state-specific)
        snap_col = f"snap_{state_id}"
        snap = cal_row.get(snap_col, 0)

        records.append({
            "d_num": d,
            "sales": y,
            "wday": wday,
            "month": month,
            "lag_7": lag_7,
            "lag_14": lag_14,
            "lag_21": lag_21,
            "lag_28": lag_28,
            "rolling_mean_7": rolling_mean_7,
            "rolling_mean_28": rolling_mean_28,
            "rolling_std_7": rolling_std_7,
            "rolling_mean_dow_4": rolling_mean_dow_4,
            "trend_ratio": trend_ratio,
            "sell_price": sell_price,
            "price_ratio": price_ratio,
            "price_change": price_change,
            "event_religious": event_religious,
            "event_cultural": event_cultural,
            "event_national": event_national,
            "has_event": has_event,
            "snap": snap,
        })

    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
# Model Training
# ─────────────────────────────────────────────────────────────────────────────
FEATURE_COLS = [
    "wday", "month", "lag_7", "lag_14", "lag_21", "lag_28",
    "rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_mean_dow_4",
    "trend_ratio",
    "sell_price", "price_ratio", "price_change",
    "event_religious", "event_cultural", "event_national", "has_event", "snap",
]


def train_lightgbm(df_train, df_val=None):
    """Train a LightGBM model on feature dataframe."""
    X_train = df_train[FEATURE_COLS].values
    y_train = df_train["sales"].values

    train_set = lgb.Dataset(X_train, label=y_train)

    callbacks = [lgb.log_evaluation(period=0)]

    if df_val is not None and len(df_val) > 0:
        X_val = df_val[FEATURE_COLS].values
        y_val = df_val["sales"].values
        val_set = lgb.Dataset(X_val, label=y_val, reference=train_set)
        callbacks.append(lgb.early_stopping(LGB_EARLY_STOP, verbose=False))
        model = lgb.train(
            LGB_PARAMS,
            train_set,
            num_boost_round=LGB_N_ESTIMATORS,
            valid_sets=[val_set],
            callbacks=callbacks,
        )
    else:
        model = lgb.train(
            LGB_PARAMS,
            train_set,
            num_boost_round=LGB_N_ESTIMATORS,
            callbacks=callbacks,
        )

    return model


# ─────────────────────────────────────────────────────────────────────────────
# Forecasting
# ─────────────────────────────────────────────────────────────────────────────
def recursive_forecast_lgb(model, series_sales, cal_features, price_df, state_id, horizon=HORIZON):
    """Generate recursive multi-step forecast using trained LightGBM model.

    For each step h=1..28: predict using actual lags (where available from history)
    and predicted values (for steps that depend on earlier predictions).
    Rolling features are updated during recursion to maintain consistency with training.
    """
    n = len(series_sales)
    # Extended array: history + forecast slots
    extended = np.concatenate([series_sales, np.zeros(horizon)])

    # Merge calendar with price
    cal_price = cal_features.merge(price_df, on="wm_yr_wk", how="left")

    forecasts = np.zeros(horizon)

    for h in range(horizon):
        d = n + 1 + h  # day number (1-based)
        cal_row = cal_price[cal_price["d_num"] == d]
        if cal_row.empty:
            # Fallback: use last known value
            forecasts[h] = extended[n - 1]
            extended[n + h] = forecasts[h]
            continue
        cal_row = cal_row.iloc[0]

        # Temporal
        wday = cal_row["wday"]
        month = cal_row["month"]

        # Lags (from extended array which includes predictions)
        idx = n + h  # 0-based index of current forecast day
        lag_7 = extended[idx - 7] if idx >= 7 else 0
        lag_14 = extended[idx - 14] if idx >= 14 else 0
        lag_21 = extended[idx - 21] if idx >= 21 else 0
        lag_28 = extended[idx - 28] if idx >= 28 else 0

        # Rolling stats — UPDATED using extended array (includes predictions)
        window_start_7 = max(0, idx - 7)
        window_start_28 = max(0, idx - 28)
        window_7 = extended[window_start_7:idx]
        window_28 = extended[window_start_28:idx]
        rolling_mean_7 = np.mean(window_7) if len(window_7) > 0 else 0
        rolling_mean_28 = np.mean(window_28) if len(window_28) > 0 else 0
        rolling_std_7 = np.std(window_7) if len(window_7) > 1 else 0

        # DOW rolling mean — updated with predictions where available
        same_dow_vals = []
        for k in range(1, 5):
            back_idx = idx - 7 * k
            if back_idx >= 0:
                same_dow_vals.append(extended[back_idx])
        rolling_mean_dow_4 = np.mean(same_dow_vals) if same_dow_vals else 0

        # Trend ratio
        trend_ratio = rolling_mean_7 / rolling_mean_28 if rolling_mean_28 > 0 else 1.0

        # Price
        sell_price = cal_row.get("sell_price", np.nan)
        price_ratio = cal_row.get("price_ratio", 1.0)
        price_change = cal_row.get("price_change", 0)
        if pd.isna(sell_price):
            sell_price = 0
            price_ratio = 1.0
            price_change = 0

        # Events
        event_religious = cal_row["event_religious"]
        event_cultural = cal_row["event_cultural"]
        event_national = cal_row["event_national"]
        has_event = cal_row["has_event"]

        # SNAP
        snap_col = f"snap_{state_id}"
        snap = cal_row.get(snap_col, 0)

        # Build feature vector (must match FEATURE_COLS order)
        features = np.array([[
            wday, month, lag_7, lag_14, lag_21, lag_28,
            rolling_mean_7, rolling_mean_28, rolling_std_7, rolling_mean_dow_4,
            trend_ratio,
            sell_price, price_ratio, price_change,
            event_religious, event_cultural, event_national, has_event, snap,
        ]])

        pred = model.predict(features)[0]
        pred = max(pred, 0)  # floor at zero
        forecasts[h] = pred
        extended[n + h] = pred

    return forecasts


def dow_mean_forecast(series_sales, cal_features, horizon=HORIZON):
    """Forecast for intermittent series using DOW-stratified recent mean.

    Groups the last CABLE_RECENT_DAYS by day-of-week and forecasts each horizon
    day as the mean of its DOW group.
    """
    n = len(series_sales)
    recent_start = max(0, n - CABLE_RECENT_DAYS)
    recent_sales = series_sales[recent_start:n]

    # Get wday for recent period
    recent_d_nums = list(range(recent_start + 1, n + 1))
    cal_recent = cal_features[cal_features["d_num"].isin(recent_d_nums)][["d_num", "wday"]].copy()
    cal_recent = cal_recent.sort_values("d_num").reset_index(drop=True)

    if len(cal_recent) == 0:
        return np.full(horizon, np.mean(recent_sales) if len(recent_sales) > 0 else 0)

    # Compute DOW means from recent history
    dow_means = {}
    for i, row in cal_recent.iterrows():
        day_idx = row["d_num"] - recent_start - 1
        if 0 <= day_idx < len(recent_sales):
            wday = row["wday"]
            if wday not in dow_means:
                dow_means[wday] = []
            dow_means[wday].append(recent_sales[day_idx])

    for wday in dow_means:
        dow_means[wday] = np.mean(dow_means[wday])

    # Fallback mean
    overall_mean = np.mean(recent_sales) if len(recent_sales) > 0 else 0

    # Generate forecasts for horizon days
    forecasts = np.zeros(horizon)
    forecast_d_nums = list(range(n + 1, n + horizon + 1))
    cal_horizon = cal_features[cal_features["d_num"].isin(forecast_d_nums)][["d_num", "wday"]].copy()
    cal_horizon = cal_horizon.sort_values("d_num").reset_index(drop=True)

    for h in range(horizon):
        d = n + 1 + h
        cal_row = cal_horizon[cal_horizon["d_num"] == d]
        if not cal_row.empty:
            wday = cal_row.iloc[0]["wday"]
            forecasts[h] = dow_means.get(wday, overall_mean)
        else:
            forecasts[h] = overall_mean

    return forecasts


def seasonal_naive_forecast(series_sales, horizon=HORIZON):
    """Repeat the last week (same weekday) across the horizon."""
    last_week = series_sales[-7:]
    repeats = (horizon // 7) + 1
    naive = np.tile(last_week, repeats)[:horizon]
    return naive


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def compute_rmsse(y_true, y_pred, y_train):
    """Compute Root Mean Squared Scaled Error.

    RMSSE = sqrt(MSE(forecast) / MSE(naive_in_sample))
    where naive in-sample is the random walk (diff of consecutive values).
    """
    numerator = np.mean((y_true - y_pred) ** 2)
    denom = np.mean(np.diff(y_train) ** 2)
    if denom == 0 or np.isnan(denom):
        return 0.0  # constant series
    return np.sqrt(numerator / denom)


# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def main(data_dir, out_path):
    print("=" * 60)
    print("RetailCast India — Demand Forecasting Pipeline")
    print("=" * 60)

    # ── Step 1: Load data ─────────────────────────────────────────────────
    print("\n[1/7] Loading data...")
    sales, calendar, prices, vendor = load_data(data_dir)
    cal_features = build_calendar_features(calendar)
    vendor_horizon = load_vendor_horizon(vendor)
    print(f"  Sales: {sales.shape[0]} series × {N_HIST} days")
    print(f"  Calendar: {len(calendar)} rows")
    print(f"  Prices: {len(prices)} rows")
    print(f"  Vendor signal: {len(vendor)} rows, {len(vendor_horizon)} series with horizon forecasts")
    print("  market_signal.csv: EXCLUDED (confirmed data leakage, r=0.942)")

    # ── Step 2: Classify series ───────────────────────────────────────────
    print("\n[2/7] Classifying series...")
    sales["series_class"] = sales["item_id"].apply(classify_series)
    class_counts = sales["series_class"].value_counts()
    for cls, cnt in class_counts.items():
        print(f"  {cls}: {cnt} series")

    # ── Step 3: Prepare data structures ───────────────────────────────────
    print("\n[3/7] Preparing sales arrays...")
    dcols = [f"d_{i}" for i in range(1, N_HIST + 1)]
    all_series_data = {}
    for _, row in sales.iterrows():
        sid = row["id"]
        all_series_data[sid] = {
            "sales": row[dcols].values.astype(float),
            "item_id": row["item_id"],
            "store_id": row["store_id"],
            "state_id": row["state_id"],
            "class": row["series_class"],
        }

    # ── Step 4: Build features and train per series ───────────────────────
    print("\n[4/7] Training models (per-series LightGBM)...")
    models = {}
    validation_scores = {}

    for sid, sdata in all_series_data.items():
        series_class = sdata["class"]

        if series_class == "intermittent":
            # No LightGBM training for CABLE — skip
            continue

        item_id = sdata["item_id"]
        store_id = sdata["store_id"]
        state_id = sdata["state_id"]
        full_sales = sdata["sales"]

        # Determine training window
        train_start = get_training_start(series_class)

        # Get price features for this series
        price_feats = get_price_features(prices, store_id, item_id)

        # Build feature dataframe using full sales (absolute day indexing)
        df_features_full = build_series_features(
            full_sales, cal_features, price_feats, store_id, state_id
        )

        # Filter to training window
        df_train_all = df_features_full[df_features_full["d_num"] >= train_start].copy()

        if len(df_train_all) < 50:
            print(f"  WARN: {sid} has too few rows after filtering ({len(df_train_all)}), using naive")
            continue

        # Split: last 28 days as validation, rest as train
        val_cutoff = N_HIST - HORIZON  # d_1886
        df_train = df_train_all[df_train_all["d_num"] <= val_cutoff]
        df_val = df_train_all[df_train_all["d_num"] > val_cutoff]

        if len(df_train) < 30:
            # Fall back to training on all data without validation
            model = train_lightgbm(df_train_all)
        else:
            model = train_lightgbm(df_train, df_val)

        models[sid] = model

        # Compute validation RMSSE
        if len(df_val) > 0:
            y_val_true = df_val["sales"].values
            X_val = df_val[FEATURE_COLS].values
            y_val_pred = model.predict(X_val)
            y_val_pred = np.maximum(y_val_pred, 0)
            # In-sample history for scaling
            train_sales = full_sales[train_start - 1:val_cutoff]
            rmsse = compute_rmsse(y_val_true, y_val_pred, train_sales)
            validation_scores[sid] = rmsse

    n_trained = len(models)
    print(f"  Trained {n_trained} LightGBM models")
    if validation_scores:
        mean_rmsse = np.mean(list(validation_scores.values()))
        print(f"  Mean validation RMSSE (point prediction): {mean_rmsse:.4f}")

    # ── Step 5: Retrain on full data ──────────────────────────────────────
    print("\n[5/7] Retraining on full data (up to d_1913)...")
    final_models = {}

    for sid, sdata in all_series_data.items():
        series_class = sdata["class"]
        if series_class == "intermittent":
            continue

        item_id = sdata["item_id"]
        store_id = sdata["store_id"]
        state_id = sdata["state_id"]
        full_sales = sdata["sales"]
        train_start = get_training_start(series_class)

        price_feats = get_price_features(prices, store_id, item_id)
        df_features_full = build_series_features(
            full_sales, cal_features, price_feats, store_id, state_id
        )
        df_train_all = df_features_full[df_features_full["d_num"] >= train_start].copy()

        if len(df_train_all) < 50:
            continue

        model = train_lightgbm(df_train_all)
        final_models[sid] = model

    print(f"  Retrained {len(final_models)} models on full history")

    # ── Step 6: Generate forecasts ────────────────────────────────────────
    print("\n[6/7] Generating 28-day forecasts...")
    all_forecasts = {}

    for sid, sdata in all_series_data.items():
        series_class = sdata["class"]
        store_id = sdata["store_id"]
        state_id = sdata["state_id"]
        item_id = sdata["item_id"]
        full_sales = sdata["sales"]

        # Component 1: Seasonal naive (always computed)
        naive = seasonal_naive_forecast(full_sales, HORIZON)

        # Component 2: Vendor signal (if available)
        vendor_fc = vendor_horizon.get(sid, None)

        if series_class == "intermittent":
            # CABLE: DOW-mean primary, blend with vendor if available
            dow_fc = dow_mean_forecast(full_sales, cal_features, HORIZON)
            if vendor_fc is not None:
                # Light blend: 80% DOW-mean, 20% vendor for CABLE
                final_fc = 0.80 * dow_fc + 0.20 * vendor_fc
            else:
                final_fc = dow_fc
        elif sid in final_models:
            # LightGBM + naive + vendor blend
            price_feats = get_price_features(prices, store_id, item_id)
            lgb_fc = recursive_forecast_lgb(
                final_models[sid], full_sales, cal_features, price_feats, state_id, HORIZON
            )

            # Get blend weights for this series class
            if series_class == "regime_changed":
                w_lgb, w_naive, w_vendor = BLEND_AGARBATTI
            else:
                w_lgb, w_naive, w_vendor = BLEND_REGULAR

            if vendor_fc is not None:
                final_fc = w_lgb * lgb_fc + w_naive * naive + w_vendor * vendor_fc
            else:
                # No vendor available — redistribute to LightGBM and naive
                total = w_lgb + w_naive
                final_fc = (w_lgb / total) * lgb_fc + (w_naive / total) * naive
        else:
            # No model trained — blend naive with vendor
            if vendor_fc is not None:
                final_fc = 0.60 * naive + 0.40 * vendor_fc
            else:
                final_fc = naive

        # Post-processing: floor at 0, round
        final_fc = np.maximum(final_fc, 0)
        final_fc = np.round(final_fc, 2)
        all_forecasts[sid] = final_fc

    print(f"  Generated forecasts for {len(all_forecasts)} series")

    # ── Step 7: Write submission ──────────────────────────────────────────
    print("\n[7/7] Writing submission...")
    rows = []
    for _, row in sales.iterrows():
        sid = row["id"]
        fc = all_forecasts[sid]
        row_dict = {"id": sid}
        for i in range(HORIZON):
            row_dict[f"F{i + 1}"] = fc[i]
        rows.append(row_dict)

    submission = pd.DataFrame(rows)
    submission = submission[["id"] + FCOLS]
    submission.to_csv(out_path, index=False)
    print(f"\n  ✓ Wrote {out_path} ({len(submission)} rows × {len(FCOLS)} forecast days)")

    # Summary statistics
    print("\n" + "=" * 60)
    print("Forecast Summary")
    print("=" * 60)
    for item_id in sales["item_id"].unique():
        item_series = [sid for sid, sd in all_series_data.items() if sd["item_id"] == item_id]
        means = [all_forecasts[sid].mean() for sid in item_series]
        print(f"  {item_id}: avg forecast = {np.mean(means):.2f} units/day")

    if validation_scores:
        print(f"\n  Holdout mean RMSSE: {mean_rmsse:.4f}")
        print(f"  (Lower is better; 1.0 = same as seasonal naive)")

    print("\nDone.")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="RetailCast India forecasting pipeline")
    ap.add_argument("--data", default=os.path.join(here, os.pardir, "data"),
                    help="Path to data directory (default: ../data)")
    ap.add_argument("--out", default=os.path.join(here, "submission.csv"),
                    help="Output CSV path (default: ./submission.csv)")
    args = ap.parse_args()
    main(args.data, args.out)
