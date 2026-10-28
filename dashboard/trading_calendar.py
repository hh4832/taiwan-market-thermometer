"""Taiwan trading-session mapping helpers.

The configured exchange closure file is the calendar source of truth.  Market
data indexes describe data availability; they must not define whether the
exchange was open because a missing observation is not evidence of a closure.
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
    if horizon not in {1, 2, 3, 5, 10, 20}:
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


def official_sessions(start: object, end: object, holidays: pd.Index | list[object] | None = None) -> pd.DatetimeIndex:
    """Return configured TWSE day sessions for an explicitly covered interval."""
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    if first > last:
        raise ValueError("calendar start is after end")
    closed = configured_holidays() if holidays is None else pd.DatetimeIndex(pd.to_datetime(holidays)).normalize()
    covered_years = set(closed.year)
    requested_years = set(range(first.year, last.year + 1))
    missing = sorted(requested_years - covered_years)
    if missing:
        raise LookupError(f"official TWSE calendar is not configured for years: {missing}")
    weekdays = pd.date_range(first, last, freq="B")
    return weekdays[~weekdays.isin(closed)]


def extend_future_sessions(
    historical_sessions: pd.Index | list[object],
    periods: int = 40,
    holidays: pd.Index | list[object] | None = None,
) -> pd.DatetimeIndex:
    """Build sessions from the official calendar wherever annual coverage exists.

    Observed dates are retained for years not yet represented by the configured
    calendar.  Within a covered year, the official calendar replaces the market
    data index so a missing observation cannot silently become a market closure.
    """
    historical = normalize_sessions(historical_sessions)
    closed = configured_holidays() if holidays is None else pd.DatetimeIndex(pd.to_datetime(holidays)).normalize()
    covered_years = sorted(set(closed.year))
    search_end = historical[-1] + pd.Timedelta(days=max(periods * 3, 60))
    if historical[-1].year in covered_years:
        search_end = min(search_end, pd.Timestamp(year=historical[-1].year, month=12, day=31))
    candidate = official_sessions(historical[-1] + pd.Timedelta(days=1), search_end, closed)
    future = candidate[:periods]
    if len(future) < periods:
        raise LookupError(f"official calendar has only {len(future)} future sessions; need {periods}")
    result = historical
    for year in covered_years:
        year_start = max(historical[0], pd.Timestamp(year=year, month=1, day=1))
        year_end = min(historical[-1], pd.Timestamp(year=year, month=12, day=31))
        if year_start > year_end:
            continue
        result = result[result.year != year].union(official_sessions(year_start, year_end, closed))
    return result.union(future).sort_values()


def expected_latest_trading_date(now: object, sessions: pd.Index | list[object], publish_hour: int = 19) -> pd.Timestamp:
    timestamp = pd.Timestamp(now)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Taipei").tz_localize(None)
    cutoff = timestamp.normalize() if timestamp.hour >= publish_hour else timestamp.normalize() - pd.Timedelta(days=1)
    eligible = official_sessions(cutoff - pd.Timedelta(days=40), cutoff)
    if eligible.empty:
        raise LookupError("no expected trading date in calendar")
    return eligible[-1]
