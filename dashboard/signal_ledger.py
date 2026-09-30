"""As-of signal reconstruction and cumulative canonical event ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from .research_registry import CANONICAL_SIGNALS, ResearchSignal
from .signal_engine import SignalEvent, evaluate_signals, events_frame, events_from_frame
from .trading_calendar import normalize_sessions


LEDGER_KEY = ("signal_date", "signal_id", "horizon")
ORIGIN_PRIORITY = {"BACKFILL": 1, "PRODUCTION": 2}


def required_history_observations(signals: tuple[ResearchSignal, ...] = CANONICAL_SIGNALS) -> int:
    """Minimum canonical-metric observations required by the longest registry rule."""
    return max((signal.rolling_window + (1 if signal.normalization.endswith("strict_prior") else 0)) for signal in signals)


def initial_backfill_dates(
    sessions: pd.Index | list[object],
    current_signal_date: object,
    max_horizon: int | None = None,
) -> pd.DatetimeIndex:
    """Return prior signal sessions needed to populate all horizons targeting today."""
    horizon = max_horizon or max(signal.horizon for signal in CANONICAL_SIGNALS)
    index = normalize_sessions(sessions)
    current = pd.Timestamp(current_signal_date).normalize()
    prior = index[index < current]
    return prior[-horizon:]


def build_historical_signal_events(
    metrics: Mapping[str, pd.Series],
    sessions: pd.Index | list[object],
    signal_dates: pd.Index | list[object],
    calculation_timestamp: object,
    signals: tuple[ResearchSignal, ...] = CANONICAL_SIGNALS,
) -> tuple[SignalEvent, ...]:
    """Reconstruct each d0 using only canonical metric observations available by d0."""
    all_events: list[SignalEvent] = []
    for signal_date in normalize_sessions(signal_dates):
        truncated: dict[str, pd.Series] = {}
        for name, values in metrics.items():
            series = pd.to_numeric(pd.Series(values), errors="coerce").sort_index()
            series.index = pd.to_datetime(series.index).normalize()
            truncated[name] = series.loc[series.index <= signal_date]
        all_events.extend(evaluate_signals(
            truncated,
            sessions,
            calculation_timestamp=calculation_timestamp,
            signals=signals,
            as_of_date=signal_date,
            event_origin="BACKFILL",
        ))
    return tuple(all_events)


def merge_signal_events(
    existing: tuple[SignalEvent, ...],
    incoming: tuple[SignalEvent, ...],
) -> tuple[SignalEvent, ...]:
    """Idempotent ledger upsert where a recorded PRODUCTION event always wins."""
    selected: dict[tuple[str, str, int], SignalEvent] = {
        (event.signal_date, event.signal_id, event.horizon): event for event in existing
    }
    for event in incoming:
        key = (event.signal_date, event.signal_id, event.horizon)
        previous = selected.get(key)
        if previous is None:
            selected[key] = event
            continue
        previous_priority = ORIGIN_PRIORITY.get(previous.event_origin, 0)
        incoming_priority = ORIGIN_PRIORITY.get(event.event_origin, 0)
        if incoming_priority > previous_priority:
            selected[key] = event
        elif incoming_priority == previous_priority and event.event_origin == "PRODUCTION":
            selected[key] = event
        # Same-key BACKFILL is deliberately preserved for deterministic reruns.
    return tuple(selected[key] for key in sorted(selected))


def build_signal_ledger(
    metrics: Mapping[str, pd.Series],
    sessions: pd.Index | list[object],
    current_events: tuple[SignalEvent, ...],
    calculation_timestamp: object,
    existing: tuple[SignalEvent, ...] = (),
) -> tuple[SignalEvent, ...]:
    if not current_events:
        return existing
    current_date = current_events[0].signal_date
    dates = initial_backfill_dates(sessions, current_date)
    backfill = build_historical_signal_events(metrics, sessions, dates, calculation_timestamp)
    return merge_signal_events(existing, merge_signal_events(backfill, current_events))


def load_signal_ledger(path: str | Path) -> tuple[SignalEvent, ...]:
    source = Path(path)
    if not source.is_file():
        return ()
    return events_from_frame(pd.read_csv(source))


def save_signal_ledger(path: str | Path, events: tuple[SignalEvent, ...]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    events_frame(events).to_csv(temporary, index=False)
    temporary.replace(target)
    return target
