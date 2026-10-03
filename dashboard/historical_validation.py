"""Realized outcomes for the cumulative production forecast ledger."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .outcomes import outcome_for_signal
from .signal_engine import SignalEvent, production_events
from .signal_ledger import merge_signal_events
from .trading_calendar import normalize_sessions, target_session


HISTORICAL_VALIDATION_COLUMNS = [
    "signal_date",
    "signal_id",
    "economic_signal_id",
    "source",
    "direction",
    "horizon",
    "entry_date",
    "target_date",
    "actual_return",
    "maturity",
    "event_origin",
    "research_status",
    "research_commit",
]
HISTORICAL_VALIDATION_KEY = ("signal_date", "signal_id", "horizon", "target_date")
MATURITY_STATES = {"MATURED", "PENDING", "DATA_UNAVAILABLE"}


def empty_historical_validation() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORICAL_VALIDATION_COLUMNS)


def _normalized_prices(values: pd.Series, name: str) -> pd.Series:
    result = pd.to_numeric(pd.Series(values).copy(), errors="coerce")
    result.index = pd.DatetimeIndex(pd.to_datetime(result.index)).normalize()
    if result.index.has_duplicates:
        raise ValueError(f"{name} contains duplicate trading sessions")
    return result.sort_index()


def _date_text(value: object | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(pd.Timestamp(value).date())


def build_historical_validation(
    ledger_events: tuple[SignalEvent, ...],
    adjusted_open: pd.Series,
    adjusted_close: pd.Series,
    trading_sessions: pd.Index | list[object],
    *,
    as_of_date: object | None = None,
) -> pd.DataFrame:
    """Map cumulative formal forecasts to canonical adjusted O1→Cn outcomes.

    ``trading_sessions`` may include configured future sessions for target mapping.
    Maturity is bounded separately by ``as_of_date`` (or the latest observed price
    index), so future sessions can never create a realized return early.
    """
    opens = _normalized_prices(adjusted_open, "adjusted_open")
    closes = _normalized_prices(adjusted_close, "adjusted_close")
    observed = opens.index.union(closes.index).sort_values()
    if observed.empty:
        raise ValueError("adjusted price history is empty")
    sessions = normalize_sessions(trading_sessions)
    available_through = pd.Timestamp(as_of_date or observed[-1]).normalize()
    if available_through > observed[-1]:
        raise ValueError("as_of_date is later than the available adjusted price history")

    # Reindexing preserves the canonical session positions used by
    # outcome_for_signal; missing entry/exit prices remain NaN and are never filled.
    canonical_open = opens.reindex(sessions)
    canonical_close = closes.reindex(sessions)
    canonical_events = merge_signal_events((), tuple(ledger_events))
    ordered_events = tuple(sorted(
        canonical_events,
        key=lambda event: (
            event.signal_date,
            0 if event.event_origin == "PRODUCTION" else 1,
            event.signal_id,
            event.horizon,
        ),
    ))
    formal_events = sorted(
        production_events(ordered_events),
        key=lambda event: (event.signal_date, event.signal_id, event.horizon, event.target_date or ""),
    )

    rows: list[dict[str, object]] = []
    for event in formal_events:
        target_date = pd.Timestamp(event.target_date).normalize() if event.target_date else None
        try:
            entry_date = target_session(event.signal_date, 1, sessions)
            canonical_target = target_session(event.signal_date, event.horizon, sessions)
        except LookupError:
            entry_date = None
            canonical_target = None

        actual_return: float | None = None
        if target_date is not None and target_date > available_through:
            maturity = "PENDING"
        elif target_date is None or entry_date is None or canonical_target is None:
            maturity = "DATA_UNAVAILABLE"
        elif canonical_target != target_date:
            raise ValueError(
                "historical validation target does not match canonical trading-session mapping: "
                f"{event.signal_date}/{event.signal_id}/C{event.horizon}"
            )
        else:
            entry_price = canonical_open.get(entry_date, np.nan)
            exit_price = canonical_close.get(target_date, np.nan)
            if not (
                np.isfinite(entry_price)
                and float(entry_price) > 0
                and np.isfinite(exit_price)
            ):
                maturity = "DATA_UNAVAILABLE"
            else:
                actual_return = outcome_for_signal(
                    event.signal_date,
                    canonical_open,
                    canonical_close,
                    event.horizon,
                )
                if actual_return is None:
                    maturity = "DATA_UNAVAILABLE"
                else:
                    maturity = "MATURED"

        rows.append({
            "signal_date": event.signal_date,
            "signal_id": event.signal_id,
            "economic_signal_id": event.economic_signal_id,
            "source": event.source,
            "direction": event.direction,
            "horizon": event.horizon,
            "entry_date": _date_text(entry_date),
            "target_date": _date_text(target_date),
            "actual_return": actual_return,
            "maturity": maturity,
            "event_origin": event.event_origin,
            "research_status": event.research_status,
            "research_commit": event.research_commit,
        })
    result = pd.DataFrame(rows, columns=HISTORICAL_VALIDATION_COLUMNS)
    validate_historical_validation(result)
    return result


def validate_historical_validation(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the serialized historical-validation contract."""
    missing = set(HISTORICAL_VALIDATION_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("historical_validation schema missing: " + ", ".join(sorted(missing)))
    result = frame.loc[:, HISTORICAL_VALIDATION_COLUMNS].copy()
    if result.empty:
        return result

    required_text = [
        "signal_date", "signal_id", "economic_signal_id", "source", "direction",
        "entry_date", "target_date", "maturity", "event_origin", "research_status",
        "research_commit",
    ]
    for column in required_text:
        invalid = result[column].isna() | result[column].astype(str).str.strip().eq("")
        if invalid.any():
            raise ValueError(f"historical_validation required field is blank: {column}")

    horizons = pd.to_numeric(result["horizon"], errors="coerce")
    if horizons.isna().any() or not horizons.isin({1, 3, 5, 10, 20}).all():
        raise ValueError("historical_validation horizon is invalid")
    result["horizon"] = horizons.astype(int)

    unknown_states = set(result["maturity"].astype(str)) - MATURITY_STATES
    if unknown_states:
        raise ValueError("historical_validation maturity is invalid: " + ", ".join(sorted(unknown_states)))

    raw_returns = result["actual_return"]
    numeric_returns = pd.to_numeric(raw_returns, errors="coerce")
    invalid_numeric = raw_returns.notna() & raw_returns.astype(str).str.strip().ne("") & numeric_returns.isna()
    if invalid_numeric.any():
        raise ValueError("historical_validation actual_return contains non-numeric values")
    result["actual_return"] = numeric_returns
    matured = result["maturity"].eq("MATURED")
    if result.loc[matured, "actual_return"].isna().any():
        raise ValueError("MATURED historical validation requires actual_return")
    if result.loc[~matured, "actual_return"].notna().any():
        raise ValueError("non-matured historical validation must not contain actual_return")
    if result.duplicated(list(HISTORICAL_VALIDATION_KEY)).any():
        raise ValueError("historical_validation contains duplicate canonical forecasts")
    return result


def load_historical_validation(path: str | Path) -> pd.DataFrame:
    return validate_historical_validation(pd.read_csv(Path(path)))
