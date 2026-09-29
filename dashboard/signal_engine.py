"""Canonical signal evaluation and long-form event generation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping

import numpy as np
import pandas as pd

from .research_registry import CANONICAL_SIGNALS, ResearchSignal
from .trading_calendar import target_session


@dataclass(frozen=True)
class SignalEvent:
    signal_date: str
    signal_id: str
    economic_signal_id: str
    source: str
    subject: str
    direction: str
    horizon: int
    target_date: str | None
    matched: bool
    evaluation_status: str
    research_status: str
    evidence_grade: str
    raw_value: float | None
    normalized_value: float | None
    threshold: str
    historical_mean_return: float | None
    historical_median_return: float | None
    historical_win_rate: float | None
    relative_mean_return: float | None
    sample_size: int | None
    global_fdr: float | None
    family_fdr: float | None
    plain_definition: str
    market_mechanism: str
    risks: str
    research_commit: str
    research_run: str
    calculation_timestamp: str
    source_data_date: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def rolling_pr(series: pd.Series, window: int, strict_prior: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    reference = values.shift(1) if strict_prior else values
    def rank(items: np.ndarray) -> float:
        current = items[-1]
        valid = items[np.isfinite(items)]
        return float(np.mean(valid <= current) * 100) if len(valid) == window and np.isfinite(current) else np.nan
    result = reference.rolling(window, min_periods=window).apply(rank, raw=True)
    if strict_prior:
        # Compare today's value to the completed prior window.
        result = pd.Series(index=values.index, dtype=float)
        for i in range(window, len(values)):
            prior = values.iloc[i-window:i].dropna()
            current = values.iloc[i]
            if len(prior) == window and np.isfinite(current):
                result.iloc[i] = float((prior <= current).mean() * 100)
    return result


def rolling_z(series: pd.Series, window: int, strict_prior: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    base = values.shift(1) if strict_prior else values
    mean = base.rolling(window, min_periods=window).mean()
    std = base.rolling(window, min_periods=window).std(ddof=0)
    return (values - mean) / std.replace(0, np.nan)


def _threshold_text(spec: ResearchSignal) -> str:
    lower = "−∞" if spec.threshold_lower is None else f"{spec.threshold_lower:g}"
    upper = "+∞" if spec.threshold_upper is None else f"{spec.threshold_upper:g}"
    return f"{spec.normalization}: [{lower}, {upper}{']' if spec.threshold_upper is None else ')'}"


def _inside(value: float, lower: float | None, upper: float | None) -> bool:
    return np.isfinite(value) and (lower is None or value >= lower) and (upper is None or value < upper)


def evaluate_signals(
    metrics: Mapping[str, pd.Series],
    sessions: pd.Index | list[object],
    calculation_timestamp: datetime | pd.Timestamp | None = None,
    signals: tuple[ResearchSignal, ...] = CANONICAL_SIGNALS,
) -> tuple[SignalEvent, ...]:
    calculation_timestamp = pd.Timestamp(calculation_timestamp or pd.Timestamp.now(tz="Asia/Taipei"))
    dates = sorted({pd.Timestamp(s.dropna().index[-1]).normalize() for s in metrics.values() if not s.dropna().empty})
    signal_date = dates[-1] if dates else calculation_timestamp.tz_localize(None).normalize()
    events: list[SignalEvent] = []
    for spec in signals:
        series = metrics.get(f"{spec.subject}_{spec.metric}_{spec.accumulation_days}d", metrics.get(spec.metric))
        raw: float | None = None
        normalized: float | None = None
        matched = False
        status = "DATA_UNAVAILABLE"
        source_date: str | None = None
        if not spec.activation_ready:
            status = "UNVALIDATED_ACTIVATION_RULE"
        elif series is not None and not series.dropna().empty:
            series = pd.to_numeric(series, errors="coerce").sort_index()
            source_date = str(pd.Timestamp(series.dropna().index[-1]).date())
            raw_candidate = series.iloc[-1]
            raw = float(raw_candidate) if np.isfinite(raw_candidate) else None
            strict = spec.normalization.endswith("strict_prior")
            if "rolling_pr" in spec.normalization:
                candidate = rolling_pr(series, spec.rolling_window, strict).iloc[-1]
            elif "rolling_z" in spec.normalization:
                candidate = rolling_z(series, spec.rolling_window, strict).iloc[-1]
            else:
                candidate = raw_candidate
            normalized = float(candidate) if np.isfinite(candidate) else None
            if normalized is None:
                status = "ROLLING_WARMUP"
            else:
                matched = _inside(normalized, spec.threshold_lower, spec.threshold_upper)
                status = "MATCHED" if matched else "VALID_NO_SIGNAL"
        try:
            target = str(target_session(signal_date, spec.horizon, sessions).date())
        except LookupError:
            target = None
        events.append(SignalEvent(
            signal_date=str(signal_date.date()), signal_id=spec.signal_id,
            economic_signal_id=spec.economic_signal_id, source=spec.source, subject=spec.subject,
            direction=spec.direction, horizon=spec.horizon, target_date=target, matched=matched,
            evaluation_status=status, research_status=spec.research_status, evidence_grade=spec.evidence_grade,
            raw_value=raw, normalized_value=normalized, threshold=_threshold_text(spec),
            historical_mean_return=spec.mean_return, historical_median_return=spec.median_return,
            historical_win_rate=spec.win_rate, relative_mean_return=spec.relative_mean_return,
            sample_size=spec.sample_size, global_fdr=spec.global_fdr, family_fdr=spec.family_fdr,
            plain_definition=spec.plain_definition, market_mechanism=spec.market_mechanism,
            risks=spec.risks, research_commit=spec.research_commit, research_run=spec.research_run,
            calculation_timestamp=calculation_timestamp.isoformat(), source_data_date=source_date,
        ))
    return tuple(events)


def production_events(events: tuple[SignalEvent, ...]) -> tuple[SignalEvent, ...]:
    """One vote per economic signal, target date and direction; retained only."""
    selected: dict[tuple[str, str | None, str], SignalEvent] = {}
    for event in events:
        if not (event.matched and event.research_status == "RETAINED"):
            continue
        key = (event.economic_signal_id, event.target_date, event.direction)
        selected.setdefault(key, event)
    return tuple(selected.values())


def events_frame(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    return pd.DataFrame([event.as_dict() for event in events])
