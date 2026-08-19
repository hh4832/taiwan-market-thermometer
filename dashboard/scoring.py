from __future__ import annotations

import numpy as np
import pandas as pd


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator.astype(float).div(denominator.astype(float).replace(0, np.nan))
    return result.replace([np.inf, -np.inf], np.nan)


def expanding_percentile(series: pd.Series, min_periods: int = 126) -> pd.Series:
    """Percentile of x[t] versus x[:t], excluding x[t] itself."""
    values = pd.to_numeric(series, errors="coerce")

    def rank_at(i: int) -> float:
        current = values.iloc[i]
        history = values.iloc[:i].dropna()
        if pd.isna(current) or len(history) < min_periods:
            return np.nan
        return float(history.le(current).mean() * 100)

    return pd.Series((rank_at(i) for i in range(len(values))), index=values.index, dtype=float)


def rolling_percentile(series: pd.Series, window: int = 252, min_periods: int = 126) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")

    def rank_at(i: int) -> float:
        current = values.iloc[i]
        history = values.iloc[max(0, i - window):i].dropna()
        if pd.isna(current) or len(history) < min_periods:
            return np.nan
        return float(history.le(current).mean() * 100)

    return pd.Series((rank_at(i) for i in range(len(values))), index=values.index, dtype=float)


def score_band(score: float | None, low_label: str, high_label: str) -> str:
    if score is None or pd.isna(score):
        return "資料不足"
    if score < 20:
        return low_label
    if score < 40:
        return "偏低"
    if score <= 60:
        return "中性"
    if score <= 80:
        return "偏高"
    return high_label
