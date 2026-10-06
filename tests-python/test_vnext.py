from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.outcomes import all_outcomes
from dashboard.research_registry import CANONICAL_SIGNALS, validate_registry
from dashboard.run_manifest import MANDATORY_STAGES, RunManifest, validate_manifest
from dashboard.signal_engine import SignalEvent, evaluate_signals, production_events, rolling_pr
from dashboard.research_evidence import build_breadth_evidence
from dashboard.daily_email import build_daily_report
from dashboard.trading_calendar import expected_latest_trading_date, extend_future_sessions, target_session


def _event(**changes):
    values = dict(signal_date="2026-09-25", signal_id="a", economic_signal_id="family",
        source="test", subject="test", direction="bullish", horizon=1, target_date="2026-09-28",
        matched=True, evaluation_status="MATCHED", research_status="RETAINED", evidence_grade="A",
        raw_value=1.0, normalized_value=99.0, threshold="PR95+", historical_mean_return=None,
        historical_median_return=None, historical_win_rate=None, relative_mean_return=None,
        sample_size=None, global_fdr=None, family_fdr=None, plain_definition="test",
        market_mechanism="test", risks="test", research_commit="abc", research_run="run",
        calculation_timestamp="2026-09-25T20:00:00+08:00", source_data_date="2026-09-25")
    values.update(changes)
    return SignalEvent(**values)


def test_registry_schema_and_provenance():
    assert validate_registry() == []
    assert len({signal.signal_id for signal in CANONICAL_SIGNALS}) == len(CANONICAL_SIGNALS)
    assert {signal.research_status for signal in CANONICAL_SIGNALS} <= {"RETAINED", "RETEST", "REJECTED"}
    assert all(signal.research_commit and signal.research_run for signal in CANONICAL_SIGNALS if signal.research_status == "RETAINED")
    assert any(signal.mean_return is None for signal in CANONICAL_SIGNALS)


def test_rejected_and_retest_never_vote_and_family_is_deduplicated():
    events = (_event(signal_id="first"), _event(signal_id="robustness"),
              _event(signal_id="retest", economic_signal_id="r", research_status="RETEST"),
              _event(signal_id="rejected", economic_signal_id="x", research_status="REJECTED"))
    assert len(production_events(events)) == 1


@pytest.mark.parametrize("horizon,expected", [(1,"2026-09-28"),(3,"2026-09-30"),(5,"2026-10-02"),(10,"2026-10-09"),(20,"2026-10-23")])
def test_target_session_uses_trading_sessions(horizon, expected):
    sessions = pd.bdate_range("2026-09-25", "2026-10-30")
    assert str(target_session("2026-09-25", horizon, sessions).date()) == expected


def test_target_session_holiday_month_and_year_end():
    sessions = pd.DatetimeIndex(["2026-12-31", "2027-01-04", "2027-01-05"])
    assert target_session("2026-12-31", 1, sessions) == pd.Timestamp("2027-01-04")


def test_configured_twse_holidays_are_skipped():
    extended = extend_future_sessions(pd.DatetimeIndex(["2026-09-24"]), periods=3)
    assert list(extended[1:]) == [pd.Timestamp("2026-09-29"), pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-01")]


def test_freshness_expected_date_handles_weekend_and_holiday():
    sessions = pd.bdate_range("2026-09-01", "2026-09-30")
    assert expected_latest_trading_date("2026-09-28 20:13", sessions) == pd.Timestamp("2026-09-24")
    assert expected_latest_trading_date("2026-10-04 20:13", sessions) == pd.Timestamp("2026-10-02")


def test_o1_cn_uses_next_open_not_signal_close():
    dates = pd.bdate_range("2026-08-10", periods=21)
    opens = pd.Series([200.0] + [100.0] * 20, index=dates)
    closes = pd.Series([200.0] + [101.0, 102.0, 103.0, 104.0, 105.0] + [110.0] * 15, index=dates)
    result = all_outcomes(dates[0], opens, closes)
    assert result["o1_c1_return"] == pytest.approx(.01)
    assert result["o1_c3_return"] == pytest.approx(.03)
    assert result["o1_c5_return"] == pytest.approx(.05)
    assert result["o1_c10_return"] == pytest.approx(.10)
    assert result["o1_c20_return"] == pytest.approx(.10)


def test_signal_boundaries_missing_and_warmup():
    spec = next(signal for signal in CANONICAL_SIGNALS if signal.signal_id == "breadth_up_ratio_1d_pr60_ge95_c1")
    sessions = pd.bdate_range("2026-01-01", periods=90)
    values = pd.Series(range(90), index=sessions, dtype=float)
    event = evaluate_signals({"up_ratio": values}, sessions, signals=(spec,))[0]
    assert event.matched
    unavailable = evaluate_signals({}, sessions, signals=(spec,))[0]
    assert unavailable.evaluation_status == "DATA_UNAVAILABLE"
    warmup = evaluate_signals({"up_ratio": values.iloc[:10]}, sessions, signals=(spec,))[0]
    assert warmup.evaluation_status == "ROLLING_WARMUP"



def test_breadth_strict_prior_pr_excludes_current_observation():
    values = pd.Series([1.0, 2.0, 3.0, 0.0])
    strict = rolling_pr(values, window=3, strict_prior=True)
    inclusive = rolling_pr(values, window=3, strict_prior=False)
    assert strict.iloc[:3].isna().all()
    assert strict.iloc[3] == pytest.approx(0.0)
    assert inclusive.iloc[3] == pytest.approx(100.0 / 3.0)


def test_canonical_breadth_registry_matches_frozen_candidate_set():
    breadth = {signal.signal_id: signal for signal in CANONICAL_SIGNALS if signal.source == "market_breadth"}
    expected_retained = {
        "breadth_up_ratio_1d_pr60_ge95_c1",
        "breadth_big_up_ratio_5d_pr126_60_80_c3",
        "breadth_big_up_ratio_5d_pr126_60_80_c5",
        "breadth_big_up_ratio_5d_pr252_60_80_c3",
    }
    assert {signal_id for signal_id, signal in breadth.items() if signal.research_status == "RETAINED"} == expected_retained
    assert "breadth_big_up_ratio_5d_pr60_60_80_c3" not in breadth
    assert "breadth_big_up_ratio_5d_pr60_60_80_c5" not in breadth

    for signal_id in expected_retained:
        spec = breadth[signal_id]
        assert spec.normalization == "rolling_pr_strict_prior"
        assert spec.sample_size is None
        assert spec.mean_return is None
        assert spec.median_return is None
        assert spec.win_rate is None
        assert spec.relative_mean_return is None
        assert spec.global_fdr is None
        assert spec.family_fdr is None

    assert breadth["breadth_down_ratio_high_legacy"].research_status == "RETEST"


def test_legacy_breadth_presentation_is_hard_disabled():
    with pytest.raises(RuntimeError, match="Legacy market-breadth evidence is disabled"):
        build_breadth_evidence(pd.DataFrame({"up_ratio": [0.5]}))


def test_legacy_daily_report_is_hard_disabled():
    now = pd.Timestamp("2026-10-06 20:00", tz="Asia/Taipei").to_pydatetime()
    with pytest.raises(RuntimeError, match="Legacy daily report is disabled"):
        build_daily_report(
            pd.DataFrame({"up_ratio": [0.5]}, index=[pd.Timestamp("2026-10-06")]),
            pd.DataFrame({"foreign_direction_score": [0.0]}, index=[pd.Timestamp("2026-10-06")]),
            now,
        )


def test_margin_interaction_cannot_vote_without_validated_cutoff():
    spec = next(signal for signal in CANONICAL_SIGNALS if signal.signal_id == "margin_sell_prior5_c3")
    sessions = pd.bdate_range("2026-01-01", periods=300)
    event = evaluate_signals({"margin_sell": pd.Series(range(300), index=sessions)}, sessions, signals=(spec,))[0]
    assert event.evaluation_status == "UNVALIDATED_ACTIVATION_RULE"
    assert not event.matched


def test_calendar_counts_only_retained_deduplicated_votes():
    calendar = build_forecast_calendar((_event(), _event(signal_id="same"),
        _event(signal_id="bear", economic_signal_id="bear", direction="bearish"),
        _event(signal_id="retest", economic_signal_id="research", research_status="RETEST")))
    row = calendar.iloc[0]
    assert row.bullish_count == 1 and row.bearish_count == 1 and row.net_vote == 0


def test_run_manifest_success_no_signal_and_failures(tmp_path: Path):
    manifest = RunManifest("r", "t", "c", "2026-09-25", "2026-09-25")
    manifest.stage_status = {stage: "SUCCESS" for stage in MANDATORY_STAGES}
    manifest.daily_record_action = "VALID_NO_SIGNAL"
    path = manifest.write(tmp_path / "manifest.json")
    validate_manifest(path)
    assert manifest.overall_status == "SUCCESS"
    manifest.actual_data_date = "2026-09-24"
    manifest.write(path)
    assert manifest.overall_status == "STALE_DATA"
    with pytest.raises(RuntimeError): validate_manifest(path)


def test_manifest_missing_fails(tmp_path: Path):
    with pytest.raises(RuntimeError): validate_manifest(tmp_path / "missing.json")
