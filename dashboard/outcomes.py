"""Tradable O1→Cn outcomes using actual trading sessions."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .trading_calendar import normalize_sessions, target_session

HORIZONS = (1, 3, 5, 10, 20)


def outcome_for_signal(signal_date: object, adjusted_open: pd.Series, adjusted_close: pd.Series, horizon: int) -> float | None:
    opens = pd.to_numeric(adjusted_open, errors="coerce").sort_index()
    closes = pd.to_numeric(adjusted_close, errors="coerce").sort_index()
    sessions = normalize_sessions(opens.index.intersection(closes.index))
    try:
        entry_date = target_session(signal_date, 1, sessions)
        exit_date = target_session(signal_date, horizon, sessions)
    except LookupError:
        return None
    entry, exit_price = opens.get(entry_date, np.nan), closes.get(exit_date, np.nan)
    if not (np.isfinite(entry) and np.isfinite(exit_price) and entry > 0):
        return None
    return float(exit_price / entry - 1)


def all_outcomes(signal_date: object, adjusted_open: pd.Series, adjusted_close: pd.Series) -> dict[str, float | None]:
    return {f"o1_c{h}_return": outcome_for_signal(signal_date, adjusted_open, adjusted_close, h) for h in HORIZONS}
