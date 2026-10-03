from __future__ import annotations

import json

import pandas as pd

from dashboard.dashboard_source import write_current_run_artifacts
from dashboard.forecast_calendar import aggregate_events_by_target_date, build_forecast_calendar, contributing_events
from dashboard.historical_validation import empty_historical_validation
from dashboard.outcomes import outcome_for_signal
from dashboard.observability import PipelineProgress
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import (
    SignalEvent,
    evaluate_signals,
    production_evaluation,
    production_events,
)


def _event(**changes: object) -> SignalEvent:
    values: dict[str, object] = dict(
        signal_date="2026-09-30",
        signal_id="synthetic-production-signal",
        economic_signal_id="synthetic-family",
        source="test",
        subject="test",
        direction="bullish",
        horizon=1,
        target_date="2026-10-01",
        matched=False,
        evaluation_status="VALID_NO_SIGNAL",
        research_status="RETAINED",
        evidence_grade="A",
        raw_value=1.0,
        normalized_value=50.0,
        threshold="test",
        historical_mean_return=None,
        historical_median_return=None,
        historical_win_rate=None,
        relative_mean_return=None,
        sample_size=None,
        global_fdr=None,
        family_fdr=None,
        plain_definition="test",
        market_mechanism="test",
        risks="test",
        research_commit="abc",
        research_run="run",
        calculation_timestamp="2026-09-30T20:00:00+08:00",
        source_data_date="2026-09-30",
        availability_status="KNOWN",
    )
    values.update(changes)
    return SignalEvent(**values)  # type: ignore[arg-type]


def test_retest_data_unavailable_does_not_contaminate_valid_no_signal():
    result = production_evaluation((
        _event(),
        _event(signal_id="retest", research_status="RETEST", evaluation_status="DATA_UNAVAILABLE",
               source_data_date=None, availability_status="DATA_UNAVAILABLE"),
    ))
    assert result.result == "VALID_NO_SIGNAL"
    assert result.nonproduction_unavailable_signals == 1


def test_rejected_data_unavailable_does_not_contaminate_valid_no_signal():
    result = production_evaluation((
        _event(),
        _event(signal_id="rejected", research_status="REJECTED", evaluation_status="DATA_UNAVAILABLE",
               source_data_date=None, availability_status="DATA_UNAVAILABLE"),
    ))
    assert result.result == "VALID_NO_SIGNAL"


def test_activation_not_ready_is_diagnostic_only_and_never_votes():
    activation_not_ready = _event(
        signal_id="margin_sell_prior5_c3",
        economic_signal_id="margin_sell_prior5",
        evaluation_status="UNVALIDATED_ACTIVATION_RULE",
        matched=False,
        source_data_date=None,
        availability_status="DATA_UNAVAILABLE",
    )
    result = production_evaluation((_event(), activation_not_ready))
    assert result.result == "VALID_NO_SIGNAL"
    assert result.activation_not_ready_signals == 1
    assert activation_not_ready not in production_events((_event(), activation_not_ready))


def test_activation_not_ready_cannot_vote_even_if_event_is_malformed_as_matched():
    malformed = _event(
        signal_id="margin_sell_prior5_c3",
        economic_signal_id="margin_sell_prior5",
        evaluation_status="MATCHED",
        matched=True,
    )
    assert production_events((malformed,)) == ()
    assert aggregate_events_by_target_date((malformed,)).empty


def test_target_aggregation_preserves_production_origin_precedence():
    backfill = _event(
        signal_id="backfill-variant", matched=True, evaluation_status="MATCHED",
        event_origin="BACKFILL",
    )
    production = _event(
        signal_id="production-variant", matched=True, evaluation_status="MATCHED",
        event_origin="PRODUCTION",
    )
    selected = contributing_events((backfill, production), production.target_date)
    assert len(selected) == 1
    assert selected[0].event_origin == "PRODUCTION"


def test_production_data_unavailable_controls_overall_result():
    unavailable = _event(
        evaluation_status="DATA_UNAVAILABLE", raw_value=None, normalized_value=None,
        source_data_date=None, availability_status="DATA_UNAVAILABLE",
    )
    assert production_evaluation((unavailable,)).result == "DATA_UNAVAILABLE"


def test_production_rolling_warmup_is_not_valid_no_signal():
    warmup = _event(evaluation_status="ROLLING_WARMUP", normalized_value=None)
    result = production_evaluation((warmup,))
    assert result.result == "DATA_UNAVAILABLE"
    assert result.warmup_signals == 1


def test_matched_and_unmatched_production_results():
    assert production_evaluation((_event(matched=True, evaluation_status="MATCHED"),)).result == "SIGNALS_PRESENT"
    assert production_evaluation((_event(),)).result == "VALID_NO_SIGNAL"


def test_write_current_artifact_uses_central_production_result(tmp_path):
    events = (
        _event(),
        _event(signal_id="research-only", research_status="RETEST", evaluation_status="DATA_UNAVAILABLE",
               source_data_date=None, availability_status="DATA_UNAVAILABLE"),
    )
    write_current_run_artifacts(
        tmp_path, events, build_forecast_calendar(events), run_id="run", git_commit="abc",
        calculated_at="2026-09-30T20:00:00+08:00", actual_data_date="2026-09-30",
        historical_validation=empty_historical_validation(),
    )
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["evaluation_result"] == "VALID_NO_SIGNAL"


def test_future_target_and_pending_outcome_do_not_invalidate_forecast():
    event = _event(
        matched=True, evaluation_status="MATCHED", target_date="2026-10-20", horizon=20,
    )
    dates = pd.bdate_range("2026-09-28", "2026-09-30")
    adjusted_open = pd.Series([100.0, 101.0, 102.0], index=dates)
    adjusted_close = pd.Series([100.5, 101.5, 102.5], index=dates)
    assert production_evaluation((event,)).result == "SIGNALS_PRESENT"
    assert len(production_events((event,))) == 1
    assert build_forecast_calendar((event,)).iloc[0]["target_date"] == "2026-10-20"
    assert outcome_for_signal(event.signal_date, adjusted_open, adjusted_close, event.horizon) is None


def test_production_metric_missing_is_data_unavailable_not_false_or_zero():
    spec = next(signal for signal in CANONICAL_SIGNALS if signal.signal_id == "breadth_up_ratio_1d_pr60_ge95_c1")
    sessions = pd.bdate_range("2026-01-01", periods=90)
    event = evaluate_signals({}, sessions, signals=(spec,), as_of_date=sessions[-1])[0]
    assert event.evaluation_status == "DATA_UNAVAILABLE"
    assert event.raw_value is None
    assert not event.matched
    assert production_evaluation((event,)).result == "DATA_UNAVAILABLE"


def test_stale_source_date_is_data_unavailable_even_if_status_says_valid():
    event = _event(source_data_date="2026-09-29")
    assert production_evaluation((event,)).result == "DATA_UNAVAILABLE"


def test_production_diagnostics_are_bounded_and_separate_from_research_only():
    messages: list[str] = []
    progress = PipelineProgress(total=0, emit=messages.append)
    summary = production_evaluation((
        _event(),
        _event(signal_id="retest", research_status="RETEST", evaluation_status="DATA_UNAVAILABLE",
               source_data_date=None, availability_status="DATA_UNAVAILABLE"),
    ))
    progress.diagnostic("production_evaluation", **summary.production_diagnostic())
    progress.diagnostic("nonproduction_evaluation", **summary.nonproduction_diagnostic())
    assert len(messages) == 2
    assert "eligible_signals=1" in messages[0]
    assert "result=VALID_NO_SIGNAL" in messages[0]
    assert "retest=1" in messages[1]
