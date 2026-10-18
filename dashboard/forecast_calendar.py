"""Forecast calendar built only from canonical signal events."""
from __future__ import annotations

import pandas as pd

from .signal_engine import (
    SignalEvent,
    is_production_vote,
    production_events,
    visible_events,
)


TARGET_CALENDAR_COLUMNS = [
    "target_date", "bullish_count", "bearish_count", "net_vote",
    "contributing_event_count", "active_signals",
]
EVIDENCE_CALENDAR_COLUMNS = [
    "target_date", "visible_bullish_count", "visible_bearish_count",
    "production_bullish_count", "production_bearish_count",
    "experimental_bullish_count", "experimental_bearish_count",
    "experimental_relative_stronger_count", "experimental_relative_weaker_count",
    "experimental_absolute_bullish_count", "experimental_absolute_bearish_count",
    "net_vote", "visible_signals",
]


def contributing_events(events: tuple[SignalEvent, ...], target_date: object) -> tuple[SignalEvent, ...]:
    """Production-eligible forecasts for one target, deduplicated within each vintage."""
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
        if event.target_date != target or not is_production_vote(event):
            continue
        # A vintage is one signal_date. Robustness variants within the same
        # economic hypothesis and vintage get one vote; different vintages remain.
        key = (event.signal_date, event.economic_signal_id, event.direction, target)
        selected.setdefault(key, event)
    return tuple(selected[key] for key in sorted(selected))


def visible_contributing_events(
    events: tuple[SignalEvent, ...], target_date: object
) -> tuple[SignalEvent, ...]:
    target = str(pd.Timestamp(target_date).date())
    return tuple(event for event in visible_events(events) if event.target_date == target)


def build_evidence_calendar(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    """Descriptive calendar with experimental evidence kept out of net_vote."""
    visible = visible_events(events)
    targets = sorted({event.target_date for event in visible if event.target_date})
    rows: list[dict[str, object]] = []
    for target in targets:
        items = tuple(event for event in visible if event.target_date == target)
        production = tuple(event for event in items if is_production_vote(event))
        experimental = tuple(event for event in items if not is_production_vote(event))
        relative = tuple(event for event in experimental if event.evidence_scope == "relative")
        absolute = tuple(event for event in experimental if event.evidence_scope != "relative")
        prod_bull = sum(event.direction == "bullish" for event in production)
        prod_bear = sum(event.direction == "bearish" for event in production)
        rows.append({
            "target_date": target,
            "visible_bullish_count": sum(event.direction == "bullish" for event in items),
            "visible_bearish_count": sum(event.direction == "bearish" for event in items),
            "production_bullish_count": prod_bull,
            "production_bearish_count": prod_bear,
            "experimental_bullish_count": sum(event.direction == "bullish" for event in experimental),
            "experimental_bearish_count": sum(event.direction == "bearish" for event in experimental),
            "experimental_relative_stronger_count": sum(event.direction == "bullish" for event in relative),
            "experimental_relative_weaker_count": sum(event.direction == "bearish" for event in relative),
            "experimental_absolute_bullish_count": sum(event.direction == "bullish" for event in absolute),
            "experimental_absolute_bearish_count": sum(event.direction == "bearish" for event in absolute),
            "net_vote": prod_bull - prod_bear,
            "visible_signals": ", ".join(event.signal_id for event in items),
        })
    return pd.DataFrame(rows, columns=EVIDENCE_CALENDAR_COLUMNS)


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
