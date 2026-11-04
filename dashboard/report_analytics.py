"""Presentation-only daily-report calculations over canonical persisted events."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .signal_presentation import historical_validation_results
from .signal_engine import SignalEvent
from .trading_calendar import normalize_sessions, target_session

ROOT = Path(__file__).resolve().parents[1]
MECHANISM_CONFIG = ROOT / "config" / "mechanism_groups.yaml"
TRACK_RECORD_COLUMNS = [
    "signal_id", "economic_signal_id", "episode_start", "horizon", "direction",
    "realized_return", "hit", "status", "mark_to_market_return", "calculation_status",
]


def load_mechanism_groups(path: str | Path = MECHANISM_CONFIG) -> dict[str, dict[str, str]]:
    """Read JSON-compatible YAML so runtime needs no extra YAML dependency."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    mapping = raw.get("group_by_economic_signal_id", {})
    return {str(key): dict(value) for key, value in mapping.items()}


def _event_date(event: SignalEvent) -> pd.Timestamp:
    return pd.Timestamp(event.signal_date).normalize()


def build_episode_index(
    events: Iterable[SignalEvent],
    trading_sessions: pd.Index | list[object],
) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    """Merge matched dates per signal variant when session gap <= horizon.

    Returns one row per variant episode and a lookup from (signal_id, signal_date)
    to its episode start. This only counts episodes represented in the supplied
    ledger; it does not infer pre-ledger history.
    """
    sessions = normalize_sessions(trading_sessions)
    positions = {date: pos for pos, date in enumerate(sessions)}
    matched = [event for event in events if event.matched and event.signal_date]
    groups: dict[str, list[SignalEvent]] = defaultdict(list)
    for event in matched:
        groups[event.signal_id].append(event)
    rows: list[dict[str, object]] = []
    lookup: dict[tuple[str, str], str] = {}
    for signal_id, items in sorted(groups.items()):
        ordered = sorted(items, key=lambda event: (_event_date(event), event.horizon))
        episode_start: pd.Timestamp | None = None
        previous_position: int | None = None
        episode_events: list[SignalEvent] = []
        def finish() -> None:
            if not episode_events or episode_start is None:
                return
            first = episode_events[0]
            start_text = str(episode_start.date())
            rows.append({
                "signal_id": first.signal_id,
                "economic_signal_id": first.economic_signal_id,
                "episode_start": start_text,
                "horizon": first.horizon,
                "direction": first.direction,
            })
            for event in episode_events:
                lookup[(event.signal_id, event.signal_date)] = start_text
        for event in ordered:
            date = _event_date(event)
            position = positions.get(date)
            if position is None:
                # Do not bridge or silently approximate an event whose trading
                # session is absent from the supplied official calendar.
                finish()
                episode_events = []
                episode_start = None
                previous_position = None
                continue
            if (
                previous_position is None
                or position - previous_position > event.horizon
            ):
                finish()
                episode_events = []
                episode_start = date
            episode_events.append(event)
            previous_position = position
        finish()
    frame = pd.DataFrame(rows, columns=[
        "signal_id", "economic_signal_id", "episode_start", "horizon", "direction",
    ])
    return frame, lookup


def build_live_track_record(
    events: Iterable[SignalEvent],
    adjusted_open: pd.Series | None,
    adjusted_close: pd.Series | None,
    trading_sessions: pd.Index | list[object],
    as_of_date: object,
) -> tuple[pd.DataFrame, dict[tuple[str, str], dict[str, object]]]:
    """Calculate one O1→Cn live record per matched signal-variant episode."""
    episode_rows, event_episode = build_episode_index(events, trading_sessions)
    rows: list[dict[str, object]] = []
    lookup: dict[tuple[str, str], dict[str, object]] = {}
    if episode_rows.empty:
        return pd.DataFrame(columns=TRACK_RECORD_COLUMNS), lookup
    if adjusted_open is None or adjusted_close is None:
        for episode in episode_rows.to_dict("records"):
            try:
                target_date = target_session(
                    episode["episode_start"], int(episode["horizon"]), trading_sessions
                )
                status = "DUE" if target_date <= pd.Timestamp(as_of_date).normalize() else "OPEN"
            except LookupError:
                status = "OPEN"
            row = {
                **episode, "realized_return": None, "hit": None,
                "status": status, "mark_to_market_return": None,
                "calculation_status": "DATA_UNAVAILABLE",
            }
            rows.append(row)
            lookup[(str(row["signal_id"]), str(row["episode_start"]))] = row
        return pd.DataFrame(rows, columns=TRACK_RECORD_COLUMNS), lookup

    opens = pd.to_numeric(pd.Series(adjusted_open).copy(), errors="coerce")
    closes = pd.to_numeric(pd.Series(adjusted_close).copy(), errors="coerce")
    opens.index = pd.DatetimeIndex(pd.to_datetime(opens.index)).normalize()
    closes.index = pd.DatetimeIndex(pd.to_datetime(closes.index)).normalize()
    if opens.index.has_duplicates or closes.index.has_duplicates:
        raise ValueError("0050 adjusted price series contains duplicate sessions")
    opens, closes = opens.sort_index(), closes.sort_index()
    sessions = normalize_sessions(trading_sessions)
    as_of = pd.Timestamp(as_of_date).normalize()
    available_close_dates = closes.index[closes.index <= as_of]
    for episode in episode_rows.to_dict("records"):
        signal_date = pd.Timestamp(episode["episode_start"]).normalize()
        horizon = int(episode["horizon"])
        try:
            entry_date = target_session(signal_date, 1, sessions)
            target_date = target_session(signal_date, horizon, sessions)
        except LookupError:
            entry_date = target_date = None
        entry = opens.get(entry_date, np.nan) if entry_date is not None else np.nan
        realized: float | None = None
        mark: float | None = None
        hit: bool | None = None
        status = "OPEN"
        calculation_status = "DATA_UNAVAILABLE"
        if target_date is not None and target_date <= as_of:
            status = "DUE"
        elif target_date is not None:
            status = "OPEN"
        if (
            np.isfinite(entry) and float(entry) > 0
            and entry_date is not None and target_date is not None
        ):
            if status == "DUE":
                exit_price = closes.get(target_date, np.nan)
                if np.isfinite(exit_price):
                    realized = float(exit_price / entry - 1.0)
                    hit = (realized > 0 if episode["direction"] == "bullish"
                           else realized < 0 if episode["direction"] == "bearish" else False)
                    calculation_status = "READY"
            else:
                current_dates = available_close_dates[available_close_dates >= entry_date]
                if len(current_dates):
                    mark_price = closes.get(current_dates[-1], np.nan)
                    if np.isfinite(mark_price):
                        mark = float(mark_price / entry - 1.0)
                        calculation_status = "READY"
        row = {**episode, "realized_return": realized, "hit": hit,
               "status": status, "mark_to_market_return": mark,
               "calculation_status": calculation_status}
        rows.append(row)
        lookup[(str(row["signal_id"]), str(row["episode_start"]))] = row
    return pd.DataFrame(rows, columns=TRACK_RECORD_COLUMNS), lookup


def formal_hit_records(historical_validation: pd.DataFrame, data_date: object) -> pd.DataFrame:
    """Deduplicate robustness variants exactly at the economic-vintage vote unit."""
    if historical_validation is None or historical_validation.empty:
        return pd.DataFrame()
    frame = historical_validation_results(historical_validation)
    if "target_date" not in frame:
        return pd.DataFrame()
    frame = frame.loc[
        frame["target_date"].astype(str).le(str(pd.Timestamp(data_date).date()))
        & frame["maturity"].eq("MATURED")
        & frame["actual_return"].notna()
    ].copy()
    if frame.empty:
        return frame
    frame["hit"] = frame["outcome"].eq("HIT")
    keys = ["signal_date", "economic_signal_id", "direction", "target_date"]
    return frame.sort_values(keys + ["signal_id"]).drop_duplicates(keys, keep="first")


def market_position(adjusted_close: pd.Series | None) -> dict[str, object]:
    if adjusted_close is None:
        return {"distance_from_60d_high": None, "vs_ma60": None, "vs_ma120": None}
    prices = pd.to_numeric(pd.Series(adjusted_close).copy(), errors="coerce").dropna().sort_index()
    if prices.empty:
        return {"distance_from_60d_high": None, "vs_ma60": None, "vs_ma120": None}
    current = float(prices.iloc[-1])
    recent = prices.tail(60)
    ma60 = prices.tail(60).mean() if len(prices) >= 60 else np.nan
    ma120 = prices.tail(120).mean() if len(prices) >= 120 else np.nan
    high = recent.max() if len(recent) >= 60 else np.nan
    return {
        "distance_from_60d_high": current / float(high) - 1 if np.isfinite(high) and high else None,
        "vs_ma60": "上" if np.isfinite(ma60) and current >= ma60 else "下" if np.isfinite(ma60) else None,
        "vs_ma120": "上" if np.isfinite(ma120) and current >= ma120 else "下" if np.isfinite(ma120) else None,
    }


def status_changes(previous_events: Iterable[SignalEvent], current_events: Iterable[SignalEvent]) -> list[str]:
    old: dict[str, str] = {}
    for event in previous_events:
        old[event.signal_id] = event.research_status
    changes: list[str] = []
    for event in current_events:
        previous = old.get(event.signal_id)
        if previous and previous != event.research_status:
            message = (
                f"研究狀態：{event.economic_signal_id} "
                f"{previous}→{event.research_status}"
            )
            if message not in changes:
                changes.append(message)
    return changes
