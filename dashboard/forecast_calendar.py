"""Forecast calendar built only from canonical signal events."""
from __future__ import annotations

import pandas as pd

from .signal_engine import SignalEvent, production_events


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
