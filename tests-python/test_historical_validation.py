from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from dashboard.dashboard_source import load_current_run_artifacts, write_current_run_artifacts
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.historical_validation import (
    build_historical_validation,
    load_historical_validation,
)
from dashboard.outcomes import outcome_for_signal
from dashboard.signal_engine import SignalEvent
from dashboard.signal_presentation import historical_validation_view


NOW = pd.Timestamp("2026-10-02 20:13", tz="Asia/Taipei")
SESSIONS = pd.bdate_range("2026-09-21", periods=20)


def _event(**changes: object) -> SignalEvent:
    values: dict[str, object] = dict(
        signal_date="2026-09-21",
        signal_id="historical-a",
        economic_signal_id="historical-family",
        source="market_breadth",
        subject="breadth",
        direction="bullish",
        horizon=3,
        target_date="2026-09-24",
        matched=True,
        evaluation_status="MATCHED",
        research_status="RETAINED",
        evidence_grade="A",
        raw_value=0.8,
        normalized_value=98.0,
        threshold="PR >= 95",
        historical_mean_return=0.01,
        historical_median_return=0.009,
        historical_win_rate=0.6,
        relative_mean_return=0.005,
        sample_size=100,
        global_fdr=0.01,
        family_fdr=0.01,
        plain_definition="test",
        market_mechanism="test",
        risks="test",
        research_commit="research-abc",
        research_run="run",
        calculation_timestamp="2026-09-21T20:00:00+08:00",
        source_data_date="2026-09-21",
        event_origin="BACKFILL",
        availability_status="KNOWN",
    )
    values.update(changes)
    return SignalEvent(**values)  # type: ignore[arg-type]


def _prices(through: str = "2026-10-02") -> tuple[pd.Series, pd.Series]:
    index = SESSIONS[SESSIONS <= pd.Timestamp(through)]
    opens = pd.Series(100.0, index=index, name="0050_adj_open")
    closes = pd.Series(100.0, index=index, name="0050_adj_close")
    if pd.Timestamp("2026-09-24") in closes.index:
        closes.loc["2026-09-24"] = 110.0
    return opens, closes


def test_cumulative_ledger_is_used_when_current_run_has_no_signal(tmp_path: Path):
    opens, closes = _prices()
    ledger = (_event(),)
    validation = build_historical_validation(ledger, opens, closes, SESSIONS, as_of_date="2026-10-02")
    write_current_run_artifacts(
        tmp_path,
        (),
        build_forecast_calendar(()),
        run_id="valid-no-signal",
        git_commit="abc",
        calculated_at=NOW.isoformat(),
        actual_data_date="2026-10-02",
        ledger_events=ledger,
        historical_validation=validation,
    )
    source = load_current_run_artifacts(tmp_path, NOW)
    assert source.evaluation_result == "VALID_NO_SIGNAL"
    assert len(source.historical_validation) == 1


def test_matured_outcome_reuses_canonical_o1_cn_result():
    opens, closes = _prices()
    result = build_historical_validation((_event(),), opens, closes, SESSIONS, as_of_date="2026-10-02")
    row = result.iloc[0]
    assert row.maturity == "MATURED"
    assert row.entry_date == "2026-09-22"
    assert row.actual_return == pytest.approx(outcome_for_signal("2026-09-21", opens.reindex(SESSIONS), closes.reindex(SESSIONS), 3))
    assert row.actual_return == pytest.approx(0.10)


def test_future_outcome_is_pending_and_never_zero():
    opens, closes = _prices("2026-09-23")
    result = build_historical_validation((_event(),), opens, closes, SESSIONS, as_of_date="2026-09-23")
    row = result.iloc[0]
    assert row.maturity == "PENDING"
    assert pd.isna(row.actual_return)


@pytest.mark.parametrize("missing", ["entry", "exit"])
def test_matured_but_missing_required_price_is_data_unavailable(missing: str):
    opens, closes = _prices()
    if missing == "entry":
        opens.loc["2026-09-22"] = float("nan")
    else:
        closes.loc["2026-09-24"] = float("nan")
    result = build_historical_validation((_event(),), opens, closes, SESSIONS, as_of_date="2026-10-02")
    row = result.iloc[0]
    assert row.maturity == "DATA_UNAVAILABLE"
    assert pd.isna(row.actual_return)


def test_pending_becomes_matured_when_target_session_price_arrives():
    early_open, early_close = _prices("2026-09-23")
    pending = build_historical_validation(
        (_event(),), early_open, early_close, SESSIONS, as_of_date="2026-09-23"
    )
    full_open, full_close = _prices()
    matured = build_historical_validation(
        (_event(),), full_open, full_close, SESSIONS, as_of_date="2026-10-02"
    )
    assert pending.iloc[0].maturity == "PENDING"
    assert matured.iloc[0].maturity == "MATURED"
    assert matured.iloc[0].actual_return == pytest.approx(0.10)


def test_historical_validation_is_idempotent():
    opens, closes = _prices()
    first = build_historical_validation((_event(),), opens, closes, SESSIONS, as_of_date="2026-10-02")
    second = build_historical_validation((_event(),), opens, closes, SESSIONS, as_of_date="2026-10-02")
    pd.testing.assert_frame_equal(first, second)


def test_as_of_slice_prevents_future_price_lookahead():
    full_open, full_close = _prices()
    early_open = full_open.loc[:"2026-09-23"]
    early_close = full_close.loc[:"2026-09-23"]
    result = build_historical_validation(
        (_event(),), early_open, early_close, SESSIONS, as_of_date="2026-09-23"
    )
    assert result.iloc[0].maturity == "PENDING"
    assert pd.isna(result.iloc[0].actual_return)


def test_only_production_eligible_votes_enter_validation():
    opens, closes = _prices()
    events = (
        _event(),
        _event(signal_id="retest", economic_signal_id="retest", research_status="RETEST"),
        _event(signal_id="rejected", economic_signal_id="rejected", research_status="REJECTED"),
        _event(
            signal_id="margin_sell_prior5_c3",
            economic_signal_id="activation-not-ready",
            evaluation_status="UNVALIDATED_ACTIVATION_RULE",
            matched=False,
        ),
    )
    result = build_historical_validation(events, opens, closes, SESSIONS, as_of_date="2026-10-02")
    assert result.signal_id.tolist() == ["historical-a"]


def test_production_origin_wins_over_backfill_for_same_forecast():
    opens, closes = _prices()
    backfill = _event()
    production = replace(backfill, event_origin="PRODUCTION", raw_value=0.9)
    result = build_historical_validation(
        (backfill, production), opens, closes, SESSIONS, as_of_date="2026-10-02"
    )
    assert len(result) == 1
    assert result.iloc[0].event_origin == "PRODUCTION"


def test_production_origin_precedes_backfill_robustness_variant():
    opens, closes = _prices()
    backfill = _event(signal_id="a-backfill-variant")
    production = _event(signal_id="z-production-variant", event_origin="PRODUCTION")
    result = build_historical_validation(
        (backfill, production), opens, closes, SESSIONS, as_of_date="2026-10-02"
    )
    assert len(result) == 1
    assert result.iloc[0].signal_id == "z-production-variant"
    assert result.iloc[0].event_origin == "PRODUCTION"


def test_artifact_round_trip_preserves_pending_blank_as_missing(tmp_path: Path):
    opens, closes = _prices("2026-09-23")
    ledger = (_event(),)
    validation = build_historical_validation(ledger, opens, closes, SESSIONS, as_of_date="2026-09-23")
    write_current_run_artifacts(
        tmp_path,
        (),
        build_forecast_calendar(()),
        run_id="round-trip",
        git_commit="abc",
        calculated_at="2026-09-23T20:00:00+08:00",
        actual_data_date="2026-09-23",
        ledger_events=ledger,
        historical_validation=validation,
    )
    restored = load_historical_validation(tmp_path / "historical_validation.csv")
    assert restored.iloc[0].maturity == "PENDING"
    assert pd.isna(restored.iloc[0].actual_return)
    assert restored.iloc[0].actual_return != 0


def test_historical_validation_view_renders_nonempty_frame():
    frame = pd.DataFrame([{
        "signal_date": "2026-09-21",
        "signal_id": "breadth_big_up_ratio_5d_pr60_60_80_c3",
        "direction": "bullish",
        "horizon": 3,
        "entry_date": "2026-09-22",
        "target_date": "2026-09-24",
        "actual_return": -0.008381,
        "maturity": "MATURED",
        "event_origin": "BACKFILL",
    }])

    shown = historical_validation_view(frame)

    assert shown.columns.tolist() == [
        "Signal Date", "Signal", "方向", "Horizon", "Entry Date",
        "Target Date", "Actual Return", "Maturity", "Event Origin",
    ]
    assert shown.iloc[0]["方向"] == "偏多"
    assert shown.iloc[0]["Horizon"] == "C3"
    assert shown.iloc[0]["Actual Return"] == "-0.84%"


def test_historical_validation_view_handles_empty_frame():
    shown = historical_validation_view(pd.DataFrame())
    assert shown.empty
    assert shown.columns.tolist() == [
        "Signal Date", "Signal", "方向", "Horizon", "Entry Date",
        "Target Date", "Actual Return", "Maturity", "Event Origin",
    ]


def test_historical_validation_view_keeps_unrealized_returns_na():
    frame = pd.DataFrame([
        {
            "signal_date": "2026-09-21", "signal_id": "pending", "direction": "bullish",
            "horizon": 5, "entry_date": "2026-09-22", "target_date": "2026-09-30",
            "actual_return": None, "maturity": "PENDING", "event_origin": "BACKFILL",
        },
        {
            "signal_date": "2026-09-21", "signal_id": "missing", "direction": "bearish",
            "horizon": 5, "entry_date": "2026-09-22", "target_date": "2026-09-30",
            "actual_return": None, "maturity": "DATA_UNAVAILABLE", "event_origin": "BACKFILL",
        },
    ])
    shown = historical_validation_view(frame)
    assert shown["Actual Return"].tolist() == ["N/A", "N/A"]
