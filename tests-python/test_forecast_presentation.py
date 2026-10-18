from __future__ import annotations

import pandas as pd

from dashboard.daily_email import build_vnext_report
from dashboard.forecast_presentation import build_forecast_presentation
from dashboard.historical_validation import (
    HISTORICAL_VALIDATION_COLUMNS,
    empty_historical_validation,
)
from dashboard.signal_engine import SignalEvent
from dashboard.signal_presentation import event_audit_record


def _event(**changes: object) -> SignalEvent:
    values: dict[str, object] = {
        "signal_date": "2026-10-02",
        "signal_id": "presentation-test",
        "economic_signal_id": "presentation-family",
        "source": "test",
        "subject": "test",
        "direction": "bullish",
        "horizon": 1,
        "target_date": "2026-10-05",
        "matched": True,
        "evaluation_status": "MATCHED",
        "research_status": "RETAINED",
        "evidence_grade": "A",
        "raw_value": 0.0123,
        "normalized_value": 97.0,
        "threshold": "rolling_pr: [95, 100]",
        "historical_mean_return": 0.01,
        "historical_median_return": 0.008,
        "historical_win_rate": 0.6,
        "relative_mean_return": None,
        "sample_size": 100,
        "global_fdr": 0.01,
        "family_fdr": 0.005,
        "plain_definition": "presentation test",
        "market_mechanism": "test",
        "risks": "test",
        "research_commit": "research-abc",
        "research_run": "research-run",
        "calculation_timestamp": "2026-10-02T20:13:00+08:00",
        "source_data_date": "2026-10-02",
        "metric": "test_metric",
        "normalization": "rolling_pr_strict_prior",
        "event_origin": "PRODUCTION",
        "availability_status": "KNOWN",
    }
    values.update(changes)
    if "signal_date" in changes and "source_data_date" not in changes:
        values["source_data_date"] = changes["signal_date"]
    return SignalEvent(**values)  # type: ignore[arg-type]


def _validation(**changes: object) -> pd.DataFrame:
    values: dict[str, object] = {
        "signal_date": "2026-10-01",
        "signal_id": "prior-c1",
        "economic_signal_id": "prior-family",
        "source": "test",
        "direction": "bearish",
        "horizon": 1,
        "entry_date": "2026-10-02",
        "target_date": "2026-10-02",
        "actual_return": -0.004,
        "maturity": "MATURED",
        "event_origin": "PRODUCTION",
        "research_status": "RETAINED",
        "research_commit": "research-abc",
    }
    values.update(changes)
    return pd.DataFrame([values], columns=HISTORICAL_VALIDATION_COLUMNS)


def test_next_day_uses_canonical_target_instead_of_calendar_arithmetic():
    friday = _event(signal_date="2026-10-02", target_date="2026-10-05")
    view = build_forecast_presentation(
        (friday,), (friday,), empty_historical_validation(), "2026-10-02"
    )["next_trading_day_forecast"]
    assert view["target_date"] == "2026-10-05"
    assert view["signals"][0]["entry_date"] == "2026-10-05"


def test_target_view_keeps_different_vintages_and_groups_multi_horizon_family():
    current_c1 = _event()
    earlier_c1 = _event(
        signal_date="2026-10-01", signal_id="earlier-c1",
        economic_signal_id="earlier-family", target_date="2026-10-02",
    )
    earlier_c3 = _event(
        signal_date="2026-09-30", signal_id="family-c3", horizon=3,
        economic_signal_id="multi-family", target_date="2026-10-05",
    )
    earlier_c5 = _event(
        signal_date="2026-09-28", signal_id="family-c5", horizon=5,
        economic_signal_id="multi-family", target_date="2026-10-05",
    )
    ledger = (current_c1, earlier_c1, earlier_c3, earlier_c5)
    view = build_forecast_presentation(
        (current_c1,), ledger, empty_historical_validation(), "2026-10-02"
    )["next_trading_day_forecast"]
    assert {row["signal_date"] for row in view["signals"]} == {
        "2026-09-28", "2026-09-30", "2026-10-02"
    }
    assert len(view["signal_groups"]) == 3


def test_research_signal_is_visible_but_not_counted_as_production():
    research = _event(
        research_status="RETEST", activation_ready=False,
        evidence_scope="relative", signal_id="research-only",
    )
    view = build_forecast_presentation(
        (research,), (research,), empty_historical_validation(), "2026-10-02"
    )["next_trading_day_forecast"]
    assert view["production_bullish_count"] == 0
    assert view["research_bullish_count"] == 1
    assert view["research_relative_stronger_count"] == 1
    assert view["research_relative_weaker_count"] == 0
    assert view["signals"][0]["formal_vote_eligible"] is False
    assert view["signals"][0]["direction_label"] == "相對偏強"


def test_missing_research_values_remain_unknown_and_never_become_zero():
    unknown = _event(
        direction="unknown", raw_value=None, normalized_value=None,
        relative_mean_return=None, historical_mean_return=None,
        historical_median_return=None, historical_win_rate=None,
        sample_size=None, global_fdr=None, family_fdr=None,
    )
    row = event_audit_record(unknown)
    assert row["raw_value"] is None
    assert row["normalized_value_raw"] is None
    assert row["relative_mean_return"] == "無法判定"
    assert row["direction_label"] == "方向未確認"


def test_spot_identity_and_plain_definition_come_from_canonical_registry():
    spot = _event(
        signal_id="spot_otc_total_sell5_pr5_20_c5",
        economic_signal_id="spot_otc_total_sell_pressure_low",
        source="spot_flow", subject="otc_total_institutional",
        metric="sell", horizon=5,
    )
    row = event_audit_record(spot)
    assert row["signal_identity"] == "上櫃三大法人｜5日賣出金額比率"
    assert "含當日504日滾動百分位 PR" in row["metric_definition"]
    assert "rolling_pr_inclusive: (5, 20]" in row["metric_definition"]
    assert row["raw_unit"] == "ratio (decimal; UI displays percent)"


def test_current_day_validation_reuses_actual_directional_and_origin_semantics():
    prior = _event(
        signal_date="2026-10-01", signal_id="prior-c1",
        economic_signal_id="prior-family", direction="bearish",
        target_date="2026-10-02", event_origin="PRODUCTION",
    )
    view = build_forecast_presentation(
        (), (prior,), _validation(), "2026-10-02"
    )["current_day_validation"]
    assert view["status"] == "AVAILABLE"
    row = view["signals"][0]
    assert row["actual_return"] == -0.004
    assert row["directional_return"] == 0.004
    assert row["outcome"] == "HIT"
    assert row["event_origin"] == "PRODUCTION"


def test_missing_expected_validation_is_explicitly_incomplete():
    prior = _event(
        signal_date="2026-10-01", signal_id="prior-c1",
        economic_signal_id="prior-family", target_date="2026-10-02",
    )
    view = build_forecast_presentation(
        (), (prior,), empty_historical_validation(), "2026-10-02"
    )["current_day_validation"]
    assert view["status"] == "VALIDATION_INCOMPLETE"
    assert view["missing_forecast_keys"]


def test_post_close_groups_one_economic_signal_across_horizons():
    c1 = _event(signal_id="family-c1", target_date="2026-10-05")
    c5 = _event(
        signal_id="family-c5", horizon=5, target_date="2026-10-09",
    )
    view = build_forecast_presentation(
        (c1, c5), (c1, c5), empty_historical_validation(), "2026-10-02"
    )["post_close_forward_signals"]
    assert len(view["signal_groups"]) == 1
    assert [row["horizon"] for row in view["signal_groups"][0]["horizons"]] == [1, 5]
    assert [row["target_date"] for row in view["signal_groups"][0]["horizons"]] == [
        "2026-10-05", "2026-10-09"
    ]


def test_email_and_publication_projection_share_dates_and_vote_counts():
    bullish = _event()
    bearish = _event(
        signal_id="bearish", economic_signal_id="bearish-family",
        direction="bearish",
    )
    events = (bullish, bearish)
    projection = build_forecast_presentation(
        events, events, empty_historical_validation(), "2026-10-02"
    )["next_trading_day_forecast"]
    subject, plain, _html = build_vnext_report(
        events, pd.DataFrame(), "2026-10-02", "run", "commit", "SUCCESS",
        target_events=events,
    )
    assert projection["target_date"] == "2026-10-05"
    assert projection["production_bullish_count"] == 1
    assert projection["production_bearish_count"] == 1
    assert "下一交易日 2026/10/05｜正式 1多 1空" in subject
    assert "Target Date：2026-10-05" in plain
    assert "正式：1 多 / 1 空" in plain
