"""Forecast-centric projections shared by email and publication consumers.

This module never evaluates a research condition and never changes vote
eligibility.  It projects already-built canonical ``SignalEvent`` records and
the canonical historical-validation table into the three date views used by
human-facing products.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import pandas as pd

from .forecast_calendar import contributing_events, visible_contributing_events
from .historical_validation import empty_historical_validation
from .research_registry import CANONICAL_SIGNALS
from .signal_engine import SignalEvent, is_production_vote, visible_events
from .signal_presentation import event_audit_record, historical_validation_results


def _date_text(value: object | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(pd.Timestamp(value).date())


def _entry_date_map(events: Iterable[SignalEvent]) -> dict[str, str]:
    result: dict[str, str] = {}
    for event in events:
        if event.horizon != 1 or not event.target_date:
            continue
        previous = result.get(event.signal_date)
        if previous is not None and previous != event.target_date:
            raise ValueError(
                "conflicting canonical O1 dates for signal_date "
                f"{event.signal_date}: {previous} != {event.target_date}"
            )
        result[event.signal_date] = event.target_date
    return result


def _record(event: SignalEvent, entry_dates: dict[str, str]) -> dict[str, Any]:
    return event_audit_record(event, entry_date=entry_dates.get(event.signal_date))


def _counts(events: Iterable[SignalEvent]) -> dict[str, int]:
    items = tuple(events)
    production = tuple(event for event in items if is_production_vote(event))
    research = tuple(event for event in items if not is_production_vote(event))
    return {
        "production_bullish_count": sum(event.direction == "bullish" for event in production),
        "production_bearish_count": sum(event.direction == "bearish" for event in production),
        "research_bullish_count": sum(event.direction == "bullish" for event in research),
        "research_bearish_count": sum(event.direction == "bearish" for event in research),
        "unconfirmed_direction_count": sum(
            event.direction not in {"bullish", "bearish"} for event in items
        ),
        "signal_count": len(items),
    }


def _group_events(
    events: Iterable[SignalEvent],
    entry_dates: dict[str, str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[SignalEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.signal_date, event.economic_signal_id)].append(event)
    rows: list[dict[str, Any]] = []
    for (signal_date, economic_signal_id), items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda event: (event.horizon, event.signal_id))
        records = [_record(event, entry_dates) for event in ordered]
        rows.append({
            "signal_date": signal_date,
            "economic_signal_id": economic_signal_id,
            "signal_identity": records[0]["signal_identity"],
            "source": ordered[0].source,
            "research_status": ordered[0].research_status,
            "production_eligible": any(is_production_vote(event) for event in ordered),
            "horizons": records,
        })
    return rows


def _next_trading_day(
    current_events: tuple[SignalEvent, ...],
    data_date: str,
) -> str | None:
    c1_targets = {
        event.target_date
        for event in current_events
        if event.horizon == 1 and event.target_date
    }
    if len(c1_targets) > 1:
        raise ValueError("current canonical events contain conflicting next trading dates")
    if c1_targets:
        return next(iter(c1_targets))
    future = sorted({
        event.target_date
        for event in current_events
        if event.target_date and event.target_date > data_date
    })
    return future[0] if future else None


def _ledger_completeness(
    ledger_events: tuple[SignalEvent, ...],
    target_date: str | None,
) -> dict[str, Any]:
    expected = sorted({
        signal.horizon for signal in CANONICAL_SIGNALS if signal.daily_evaluation_ready
    })
    observed = sorted({
        event.horizon
        for event in ledger_events
        if target_date is not None
        and event.target_date == target_date
        and event.daily_evaluation_ready
    })
    missing = sorted(set(expected) - set(observed))
    return {
        "status": "COMPLETE" if target_date is not None and not missing else "INCOMPLETE",
        "expected_horizons": expected,
        "observed_horizons": observed,
        "missing_horizons": missing,
        "warning": (
            None if target_date is not None and not missing else
            "歷史 ledger 未涵蓋所有已登錄 horizon；缺少的事件不能解讀為沒有訊號。"
        ),
    }


def _next_day_view(
    current_events: tuple[SignalEvent, ...],
    ledger_events: tuple[SignalEvent, ...],
    data_date: str,
    entry_dates: dict[str, str],
) -> dict[str, Any]:
    target_date = _next_trading_day(current_events, data_date)
    completeness = _ledger_completeness(ledger_events, target_date)
    items = (
        visible_contributing_events(ledger_events, target_date)
        if target_date is not None else ()
    )
    if target_date is None:
        status = "DATA_UNAVAILABLE"
    elif completeness["status"] != "COMPLETE":
        status = "LEDGER_INCOMPLETE"
    elif items:
        status = "SIGNALS_PRESENT"
    else:
        status = "VALID_NO_SIGNAL"
    return {
        "status": status,
        "target_date": target_date,
        **_counts(items),
        "ledger_completeness": completeness,
        "signals": [_record(event, entry_dates) for event in items],
        "signal_groups": _group_events(items, entry_dates),
    }


def _validation_key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(row.get("signal_date", "")),
        str(row.get("signal_id", "")),
        int(row.get("horizon", 0)),
        str(row.get("target_date", "")),
    )


def _current_day_validation(
    ledger_events: tuple[SignalEvent, ...],
    historical_validation: pd.DataFrame,
    data_date: str,
    entry_dates: dict[str, str],
) -> dict[str, Any]:
    expected = contributing_events(ledger_events, data_date)
    frame = historical_validation_results(
        historical_validation if historical_validation is not None
        else empty_historical_validation()
    )
    if frame.empty:
        selected = frame
    else:
        selected = frame.loc[frame["target_date"].astype(str).eq(data_date)].copy()
    rows = selected.to_dict("records")
    event_lookup = {
        (event.signal_date, event.signal_id, event.horizon, event.target_date or ""): event
        for event in expected
    }
    expected_keys = set(event_lookup)
    actual_keys = {_validation_key(row) for row in rows}
    missing = sorted(expected_keys - actual_keys)
    if missing:
        status = "VALIDATION_INCOMPLETE"
    elif not expected:
        status = "VALID_NO_SIGNAL"
    else:
        status = "AVAILABLE"
    records: list[dict[str, Any]] = []
    for row in rows:
        key = _validation_key(row)
        event = event_lookup.get(key)
        identity = _record(event, entry_dates) if event is not None else None
        records.append({
            **row,
            "signal_identity": (
                identity["signal_identity"] if identity is not None
                else "DEFINITION_UNAVAILABLE"
            ),
            "direction_label": (
                identity["direction_label"] if identity is not None
                else "方向未確認"
            ),
            "evidence_level": (
                identity["evidence_grade"] if identity is not None else None
            ),
        })
    return {
        "status": status,
        "selected_trading_date": data_date,
        "expected_forecast_count": len(expected),
        "validation_count": len(records),
        "missing_forecast_keys": [list(key) for key in missing],
        "maturity_counts": {
            state: sum(str(row.get("maturity")) == state for row in records)
            for state in ("MATURED", "PENDING", "DATA_UNAVAILABLE")
        },
        "signals": records,
    }


def _post_close_view(
    current_events: tuple[SignalEvent, ...],
    data_date: str,
    entry_dates: dict[str, str],
) -> dict[str, Any]:
    items = tuple(
        event for event in visible_events(current_events)
        if event.signal_date == data_date
    )
    targets: list[dict[str, Any]] = []
    for target_date in sorted({event.target_date for event in items if event.target_date}):
        target_items = tuple(event for event in items if event.target_date == target_date)
        targets.append({
            "target_date": target_date,
            **_counts(target_items),
            "signals": [_record(event, entry_dates) for event in target_items],
            "signal_groups": _group_events(target_items, entry_dates),
        })
    return {
        "status": "SIGNALS_PRESENT" if items else "VALID_NO_SIGNAL",
        "signal_date": data_date,
        **_counts(items),
        "targets": targets,
        "signal_groups": _group_events(items, entry_dates),
    }


def build_forecast_presentation(
    current_events: tuple[SignalEvent, ...],
    ledger_events: tuple[SignalEvent, ...],
    historical_validation: pd.DataFrame,
    data_date: str,
) -> dict[str, Any]:
    """Build the one shared projection consumed by Email and Work publication."""
    universe = tuple(ledger_events) + tuple(current_events)
    entry_dates = _entry_date_map(universe)
    next_view = _next_day_view(current_events, ledger_events, data_date, entry_dates)
    validation_view = _current_day_validation(
        ledger_events, historical_validation, data_date, entry_dates
    )
    post_close = _post_close_view(current_events, data_date, entry_dates)

    referenced: dict[str, SignalEvent] = {}
    for event in visible_events(ledger_events) + visible_events(current_events):
        referenced.setdefault(event.signal_id, event)
    return {
        "next_trading_day_forecast": next_view,
        "current_day_validation": validation_view,
        "post_close_forward_signals": post_close,
        "signal_identity": [
            _record(referenced[key], entry_dates) for key in sorted(referenced)
        ],
    }

