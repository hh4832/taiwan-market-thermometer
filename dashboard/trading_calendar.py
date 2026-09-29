"""Taiwan trading-session mapping helpers.

Production callers should pass the actual FinLab session index.  Weekday-only
fallback is for UI preview/tests and is labelled as such by callers.
"""
from __future__ import annotations

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLIDAY_FILE = ROOT / "config" / "twse_closed_dates.csv"


def normalize_sessions(sessions: pd.Index | list[object]) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().drop_duplicates().sort_values()
    if index.empty:
        raise ValueError("trading sessions are empty")
    return index


def target_session(signal_date: object, horizon: int, sessions: pd.Index | list[object]) -> pd.Timestamp:
    if horizon not in {1, 3, 5, 10, 20}:
        raise ValueError(f"unsupported horizon: {horizon}")
    index = normalize_sessions(sessions)
    signal = pd.Timestamp(signal_date).normalize()
    future = index[index > signal]
    if len(future) < horizon:
        raise LookupError(f"target C{horizon} is not available after {signal.date()}")
    return future[horizon - 1]


def next_session(signal_date: object, sessions: pd.Index | list[object]) -> pd.Timestamp:
    return target_session(signal_date, 1, sessions)


def configured_holidays(path: str | Path = HOLIDAY_FILE) -> pd.DatetimeIndex:
    source = Path(path)
    if not source.is_file():
        return pd.DatetimeIndex([])
    frame = pd.read_csv(source, comment="#")
    if "date" not in frame:
        raise ValueError(f"holiday file missing date column: {source}")
    return pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise")).normalize()


def extend_future_sessions(
    historical_sessions: pd.Index | list[object],
    periods: int = 40,
    holidays: pd.Index | list[object] | None = None,
) -> pd.DatetimeIndex:
    """Extend an observed session index with configured TWSE weekdays."""
    historical = normalize_sessions(historical_sessions)
    closed = configured_holidays() if holidays is None else pd.DatetimeIndex(pd.to_datetime(holidays)).normalize()
    candidate = pd.bdate_range(historical[-1] + pd.Timedelta(days=1), periods=periods + len(closed) + 10)
    future = candidate[~candidate.isin(closed)][:periods]
    return historical.union(future).sort_values()


def expected_latest_trading_date(now: object, sessions: pd.Index | list[object], publish_hour: int = 19) -> pd.Timestamp:
    timestamp = pd.Timestamp(now)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Taipei").tz_localize(None)
    cutoff = timestamp.normalize() if timestamp.hour >= publish_hour else timestamp.normalize() - pd.Timedelta(days=1)
    closed = configured_holidays()
    eligible = pd.bdate_range(cutoff - pd.Timedelta(days=40), cutoff)
    eligible = eligible[~eligible.isin(closed)]
    if eligible.empty:
        raise LookupError("no expected trading date in calendar")
    return eligible[-1]
