"""Forecast calendar built only from canonical signal events."""
from __future__ import annotations

import pandas as pd

from .signal_engine import SignalEvent, production_events


TARGET_CALENDAR_COLUMNS = [
    "target_date", "bullish_count", "bearish_count", "net_vote",
    "contributing_event_count", "active_signals",
]


def contributing_events(events: tuple[SignalEvent, ...], target_date: object) -> tuple[SignalEvent, ...]:
    """Retained matched forecasts for one target, deduplicated within each vintage."""
    target = str(pd.Timestamp(target_date).date())
    selected: dict[tuple[str, str, str, str], SignalEvent] = {}
    ordered = sorted(
        events,
        key=lambda event: (
            event.signal_date, event.economic_signal_id, event.direction,
            0 if event.event_origin == "PRODUCTION" else 1, event.signal_id,
        ),
    )
    for event in ordered:
        if not (
            event.target_date == target and event.matched
            and event.research_status == "RETAINED"
        ):
            continue
        # A vintage is one signal_date. Robustness variants within the same
        # economic hypothesis and vintage get one vote; different vintages remain.
        key = (event.signal_date, event.economic_signal_id, event.direction, target)
        selected.setdefault(key, event)
    return tuple(selected[key] for key in sorted(selected))


def aggregate_events_by_target_date(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    targets = sorted({event.target_date for event in events if event.target_date})
    rows: list[dict[str, object]] = []
    for target in targets:
        active = contributing_events(events, target)
        if not active:
            continue
        bullish = sum(event.direction == "bullish" for event in active)
        bearish = sum(event.direction == "bearish" for event in active)
        rows.append({
            "target_date": target,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "net_vote": bullish - bearish,
            "contributing_event_count": len(active),
            "active_signals": ", ".join(f"{event.signal_date}:{event.signal_id}" for event in active),
        })
    return pd.DataFrame(rows, columns=TARGET_CALENDAR_COLUMNS)


def build_forecast_calendar(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    active = production_events(events)
    columns = ["target_date", "bullish_count", "bearish_count", "net_vote", "active_signals"]
    if not active:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for target, group in pd.DataFrame([e.as_dict() for e in active]).groupby("target_date", dropna=False):
        bullish = int((group["direction"] == "bullish").sum())
        bearish = int((group["direction"] == "bearish").sum())
        rows.append({"target_date": target, "bullish_count": bullish, "bearish_count": bearish,
                     "net_vote": bullish - bearish, "active_signals": ", ".join(group["signal_id"])})
    return pd.DataFrame(rows, columns=columns).sort_values("target_date", na_position="last").reset_index(drop=True)


def calendar_matrix(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    active = production_events(events)
    if not active:
        return pd.DataFrame()
    frame = pd.DataFrame([e.as_dict() for e in active])
    frame["vote"] = frame["direction"].map({"bullish": "多", "bearish": "空"})
    return frame.pivot_table(index="economic_signal_id", columns="target_date", values="vote", aggfunc="first", fill_value="X")
