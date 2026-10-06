"""Canonical signal evaluation and long-form event generation."""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
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
    metric: str = ""
    normalization: str = ""
    event_origin: str = "PRODUCTION"
    availability_status: str = "KNOWN"
    evidence_scope: str = "absolute"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionEvaluation:
    """Production-only availability summary; research-only events are diagnostic."""

    result: str
    eligible_signals: int
    evaluated_signals: int
    matched_signals: int
    unavailable_signals: int
    warmup_signals: int
    retest_signals: int
    rejected_signals: int
    activation_not_ready_signals: int
    nonproduction_unavailable_signals: int

    def production_diagnostic(self) -> dict[str, object]:
        return {
            "eligible_signals": self.eligible_signals,
            "evaluated_signals": self.evaluated_signals,
            "matched_signals": self.matched_signals,
            "unavailable_signals": self.unavailable_signals,
            "warmup_signals": self.warmup_signals,
            "result": self.result,
        }

    def nonproduction_diagnostic(self) -> dict[str, object]:
        return {
            "retest": self.retest_signals,
            "rejected": self.rejected_signals,
            "activation_not_ready": self.activation_not_ready_signals,
            "unavailable": self.nonproduction_unavailable_signals,
        }


_REGISTRY_BY_ID = {signal.signal_id: signal for signal in CANONICAL_SIGNALS}
_EVALUATED_STATUSES = {"MATCHED", "VALID_NO_SIGNAL"}


def is_production_eligible(event: SignalEvent) -> bool:
    """Return whether retained evidence is active, independent of voting scope."""
    if event.research_status != "RETAINED":
        return False
    spec = _REGISTRY_BY_ID.get(event.signal_id)
    if spec is not None:
        return spec.research_status == "RETAINED" and spec.activation_ready
    # Backward-compatible handling for legacy/custom events that predate the
    # registry lookup.  An explicitly unvalidated activation rule is never eligible.
    return event.evaluation_status != "UNVALIDATED_ACTIVATION_RULE"


def is_directional_vote_eligible(event: SignalEvent) -> bool:
    """Only absolute retained evidence may affect a directional forecast."""
    spec = _REGISTRY_BY_ID.get(event.signal_id)
    scope = spec.evidence_scope if spec is not None else event.evidence_scope
    return is_production_eligible(event) and scope == "absolute"


def _is_evaluated(event: SignalEvent) -> bool:
    return (
        event.evaluation_status in _EVALUATED_STATUSES
        and event.availability_status == "KNOWN"
        and bool(event.source_data_date)
        and event.source_data_date == event.signal_date
        and event.raw_value is not None
        and event.normalized_value is not None
        and (
            (event.evaluation_status == "MATCHED" and event.matched)
            or (event.evaluation_status == "VALID_NO_SIGNAL" and not event.matched)
        )
    )


def production_evaluation(events: tuple[SignalEvent, ...]) -> ProductionEvaluation:
    """Classify formal forecast availability using production-eligible signals only."""
    eligible = tuple(event for event in events if is_directional_vote_eligible(event))
    evaluated = tuple(event for event in eligible if _is_evaluated(event))
    matched = tuple(event for event in evaluated if event.matched and event.evaluation_status == "MATCHED")
    warmup = tuple(event for event in eligible if event.evaluation_status == "ROLLING_WARMUP")
    unavailable = tuple(event for event in eligible if not _is_evaluated(event) and event not in warmup)

    if warmup or unavailable:
        result = "DATA_UNAVAILABLE"
    elif matched:
        result = "SIGNALS_PRESENT"
    else:
        result = "VALID_NO_SIGNAL"

    nonproduction = tuple(event for event in events if not is_directional_vote_eligible(event))
    return ProductionEvaluation(
        result=result,
        eligible_signals=len(eligible),
        evaluated_signals=len(evaluated),
        matched_signals=len(matched),
        unavailable_signals=len(unavailable),
        warmup_signals=len(warmup),
        retest_signals=sum(event.research_status == "RETEST" for event in nonproduction),
        rejected_signals=sum(event.research_status == "REJECTED" for event in nonproduction),
        activation_not_ready_signals=sum(
            event.research_status == "RETAINED" and not is_production_eligible(event)
            for event in nonproduction
        ),
        nonproduction_unavailable_signals=sum(
            event.evaluation_status in {"DATA_UNAVAILABLE", "ROLLING_WARMUP"}
            for event in nonproduction
        ),
    )


def is_production_vote(event: SignalEvent) -> bool:
    """Return whether one event may enter formal forecast vote aggregation."""
    return (
        is_directional_vote_eligible(event)
        and _is_evaluated(event)
        and event.matched
        and event.evaluation_status == "MATCHED"
    )


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
    lower_bracket = "[" if spec.threshold_lower is not None and spec.threshold_lower_inclusive else "("
    upper_bracket = "]" if spec.threshold_upper is not None and spec.threshold_upper_inclusive else ")"
    return f"{spec.normalization}: {lower_bracket}{lower}, {upper}{upper_bracket}"


def _inside(value: float, spec: ResearchSignal) -> bool:
    if not np.isfinite(value):
        return False
    lower_ok = spec.threshold_lower is None or (
        value >= spec.threshold_lower if spec.threshold_lower_inclusive else value > spec.threshold_lower
    )
    upper_ok = spec.threshold_upper is None or (
        value <= spec.threshold_upper if spec.threshold_upper_inclusive else value < spec.threshold_upper
    )
    return lower_ok and upper_ok


def evaluate_signals(
    metrics: Mapping[str, pd.Series],
    sessions: pd.Index | list[object],
    calculation_timestamp: datetime | pd.Timestamp | None = None,
    signals: tuple[ResearchSignal, ...] = CANONICAL_SIGNALS,
    as_of_date: object | None = None,
    event_origin: str = "PRODUCTION",
) -> tuple[SignalEvent, ...]:
    calculation_timestamp = pd.Timestamp(calculation_timestamp or pd.Timestamp.now(tz="Asia/Taipei"))
    dates = sorted({pd.Timestamp(s.dropna().index[-1]).normalize() for s in metrics.values() if not s.dropna().empty})
    if as_of_date is not None:
        signal_date = pd.Timestamp(as_of_date).tz_localize(None).normalize()
    else:
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
                matched = _inside(normalized, spec)
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
            metric=spec.metric, normalization=spec.normalization, event_origin=event_origin,
            availability_status="KNOWN" if source_date is not None else "DATA_UNAVAILABLE",
            evidence_scope=spec.evidence_scope,
        ))
    return tuple(events)


def production_events(events: tuple[SignalEvent, ...]) -> tuple[SignalEvent, ...]:
    """One vote per eligible, evaluated economic signal and vintage."""
    selected: dict[tuple[str, str | None, str], SignalEvent] = {}
    for event in events:
        if not is_production_vote(event):
            continue
        key = (event.signal_date, event.economic_signal_id, event.target_date, event.direction)
        selected.setdefault(key, event)
    return tuple(selected.values())


def events_frame(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    columns = [field.name for field in fields(SignalEvent)]
    return pd.DataFrame([event.as_dict() for event in events], columns=columns)


def _missing_optional_cell(value: object) -> bool:
    """Treat blank Sheet/CSV cells as missing without converting them to numeric zero."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _optional_float(value: object) -> float | None:
    return None if _missing_optional_cell(value) else float(value)


def _optional_int(value: object) -> int | None:
    return None if _missing_optional_cell(value) else int(float(value))


def events_from_frame(frame: pd.DataFrame) -> tuple[SignalEvent, ...]:
    """Restore canonical events from CSV/Sheet rows, including legacy blank cells."""
    required = [field.name for field in fields(SignalEvent) if field.default is MISSING and field.default_factory is MISSING]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError("signal_events schema missing: " + ", ".join(sorted(missing)))
    result: list[SignalEvent] = []
    defaults = {field.name: field.default for field in fields(SignalEvent) if field.default is not MISSING}
    for row in frame.to_dict("records"):
        values: dict[str, object] = {}
        for field in fields(SignalEvent):
            value = row.get(field.name, defaults.get(field.name))
            values[field.name] = None if _missing_optional_cell(value) else value
        values["matched"] = values["matched"] if isinstance(values["matched"], bool) else str(values["matched"]).lower() in {"true", "1", "yes"}
        values["horizon"] = int(values["horizon"])
        values["sample_size"] = _optional_int(values["sample_size"])
        for name in (
            "raw_value", "normalized_value", "historical_mean_return", "historical_median_return",
            "historical_win_rate", "relative_mean_return", "global_fdr", "family_fdr",
        ):
            values[name] = _optional_float(values[name])
        if not values.get("event_origin"):
            values["event_origin"] = "PRODUCTION"
        if not values.get("availability_status"):
            values["availability_status"] = "KNOWN" if values.get("source_data_date") else "DATA_UNAVAILABLE"
        if "evidence_scope" not in row or not values.get("evidence_scope"):
            spec = _REGISTRY_BY_ID.get(str(values.get("signal_id", "")))
            values["evidence_scope"] = spec.evidence_scope if spec is not None else "absolute"
        result.append(SignalEvent(**values))
    return tuple(result)
