"""Canonical adapter for FinLab Taiwan option put/call-ratio data.

This module reconstructs the frozen research predictors only.  It does not
select candidates, estimate effects, or decide production eligibility.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DATASET = "tw_option_put_call_ratio"
DATE_COLUMN = "date"
SCHEMA = {
    "volume_put": "賣權成交量",
    "volume_call": "買權成交量",
    "volume_provider_pcr_percent": "買賣權成交量比率%",
    "open_interest_put": "賣權未平倉量",
    "open_interest_call": "買權未平倉量",
    "open_interest_provider_pcr_percent": "買賣權未平倉量比率%",
}


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.replace(0, np.nan)
    return result.where(np.isfinite(result))


def _daily_options_frame(source: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(source).copy()
    missing = set(SCHEMA.values()) - set(frame.columns)
    if DATE_COLUMN not in frame.columns and not isinstance(frame.index, pd.DatetimeIndex):
        missing.add(DATE_COLUMN)
    if missing:
        raise RuntimeError("FinLab options schema missing: " + ", ".join(sorted(missing)))
    if frame.empty:
        raise RuntimeError(f"FinLab dataset is empty: {DATASET}")
    if DATE_COLUMN in frame.columns:
        parsed = pd.DatetimeIndex(pd.to_datetime(frame[DATE_COLUMN], errors="coerce"), name=DATE_COLUMN)
    else:
        parsed = pd.DatetimeIndex(frame.index, name=DATE_COLUMN)
    if parsed.isna().any():
        raise RuntimeError(f"FinLab dataset has malformed dates: {DATASET}")
    if parsed.duplicated().any():
        raise RuntimeError(f"FinLab dataset has duplicate dates: {DATASET}")
    if not parsed.is_monotonic_increasing:
        raise RuntimeError(f"FinLab dataset is not date-sorted: {DATASET}")
    selected = frame.loc[:, list(SCHEMA.values())].apply(pd.to_numeric, errors="coerce")
    selected.index = parsed
    values = selected.to_numpy(dtype=float)
    if np.isinf(values).any():
        raise RuntimeError(f"FinLab dataset contains infinite values: {DATASET}")
    if selected.isna().all().any():
        raise RuntimeError(f"FinLab dataset contains an all-missing required field: {DATASET}")
    if (selected.drop(columns=[SCHEMA["volume_provider_pcr_percent"], SCHEMA["open_interest_provider_pcr_percent"]]) < 0).any().any():
        raise RuntimeError(f"FinLab dataset contains negative option contracts: {DATASET}")
    return selected


def build_options_metrics(source: pd.DataFrame) -> dict[str, pd.Series]:
    """Return exact canonical levels and absolute 1/3/5-session changes."""
    frame = _daily_options_frame(source)
    metrics: dict[str, pd.Series] = {}
    for scope in ("volume", "open_interest"):
        put = frame[SCHEMA[f"{scope}_put"]]
        call = frame[SCHEMA[f"{scope}_call"]]
        pcr = _safe_ratio(put, call)
        call_share = _safe_ratio(call, put + call)
        provider = frame[SCHEMA[f"{scope}_provider_pcr_percent"]] / 100.0
        common = pcr.notna() & provider.notna()
        if common.any() and (pcr[common] - provider[common]).abs().max() > 5.1e-5:
            raise RuntimeError(f"{scope} PCR does not match the FinLab provider ratio")
        bases = {"put": put, "call": call, "pcr": pcr, "callshare": call_share}
        for name, values in bases.items():
            key = f"options_{scope}_{name}_level_0d"
            metrics[key] = values.rename(key)
            for days in (1, 3, 5):
                change_key = f"options_{scope}_{name}_change_{days}d"
                metrics[change_key] = values.sub(values.shift(days)).rename(change_key)
    return metrics


def load_live_options() -> pd.DataFrame:
    from finlab import data

    return _daily_options_frame(pd.DataFrame(data.get(DATASET)))
