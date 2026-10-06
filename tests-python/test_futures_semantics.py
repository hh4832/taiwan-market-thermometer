from __future__ import annotations

from dataclasses import replace

import pandas as pd

from dashboard.daily_email import build_vnext_report
from dashboard.dashboard_source import write_current_run_artifacts
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.google_sheet_service import EVENT_HEADERS
from dashboard.historical_validation import empty_historical_validation
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import (
    evaluate_signals,
    events_frame,
    events_from_frame,
    production_evaluation,
    production_events,
)
from dashboard.signal_presentation import event_audit_record
from dashboard.work_site_publication import build_work_site_payload


NOW = pd.Timestamp("2026-10-02 20:13", tz="Asia/Taipei")


def _spec(signal_id: str):
    return next(signal for signal in CANONICAL_SIGNALS if signal.signal_id == signal_id)


def _foreign_event(signal_id: str, percentile: int):
    sessions = pd.bdate_range("2026-01-01", periods=140)
    prior = list(range(1, 121))
    current = {20: 24, 80: 96, 100: 121}[percentile]
    values = pd.Series(prior + [current], index=sessions[:121], dtype=float)
    return evaluate_signals(
        {"foreign_net_oi_change_ratio_3d": values},
        sessions,
        calculation_timestamp=NOW,
        signals=(_spec(signal_id),),
        as_of_date=values.index[-1],
    )[0]


def _divergence_event(signal_id: str, value: float):
    sessions = pd.bdate_range("2026-01-01", periods=20)
    values = pd.Series([value], index=sessions[:1], dtype=float)
    return evaluate_signals(
        {"foreign_dealer_pr_divergence": values},
        sessions,
        calculation_timestamp=NOW,
        signals=(_spec(signal_id),),
        as_of_date=values.index[-1],
    )[0]


def test_foreign_inclusive_percentile_boundaries_match():
    bearish = _foreign_event("futures_foreign_change_pr0_20_c1", 20)
    bullish_80 = _foreign_event("futures_foreign_change_pr80_100_c1", 80)
    bullish_100 = _foreign_event("futures_foreign_change_pr80_100_c1", 100)

    assert bearish.normalized_value == 20
    assert bearish.matched
    assert bearish.threshold.endswith("[0, 20]")
    assert bullish_80.normalized_value == 80
    assert bullish_80.matched
    assert bullish_100.normalized_value == 100
    assert bullish_100.matched
    assert bullish_100.threshold.endswith("[80, 100]")


def test_divergence_inclusive_boundaries_match_without_magic_upper_bound():
    bearish = _divergence_event("futures_divergence_le_m60_c1", -0.60)
    bullish = _divergence_event("futures_divergence_ge60_c1", 0.60)

    assert bearish.matched
    assert bearish.threshold.endswith("[-1, -0.6]")
    assert bullish.matched
    assert bullish.threshold.endswith("[0.6, 1]")
    assert _spec("futures_divergence_ge60_c1").threshold_upper == 1


def test_relative_c10_is_retained_in_events_but_never_votes():
    c10 = _foreign_event("futures_foreign_change_pr0_20_c10", 20)

    assert c10.research_status == "RETAINED"
    assert c10.evidence_scope == "relative"
    assert c10.matched
    assert events_frame((c10,)).iloc[0]["evidence_scope"] == "relative"
    assert event_audit_record(c10)["evidence_scope"] == "relative"
    assert "evidence_scope" in EVENT_HEADERS
    assert production_events((c10,)) == ()
    assert build_forecast_calendar((c10,)).empty
    assert production_evaluation((c10,)).result == "VALID_NO_SIGNAL"


def test_relative_c10_is_not_rendered_as_a_formal_email_forecast():
    c10 = _foreign_event("futures_foreign_change_pr0_20_c10", 20)
    calendar = build_forecast_calendar((c10,))

    subject, plain, _html = build_vnext_report(
        (c10,), calendar, c10.signal_date, "run", "abc", "SUCCESS"
    )
    assert "正式訊號 0 個" in subject
    assert "VALID_NO_SIGNAL" in plain
    assert c10.plain_definition not in plain


def test_absolute_futures_votes_and_retest_does_not():
    bearish = tuple(
        _foreign_event(signal_id, 20)
        for signal_id in (
            "futures_foreign_change_pr0_20_c1",
            "futures_foreign_change_pr0_20_c3",
            "futures_foreign_change_pr0_20_c5",
        )
    )
    bullish = _foreign_event("futures_foreign_change_pr80_100_c1", 80)
    bullish_retest = _foreign_event("futures_foreign_change_pr80_100_c3", 80)
    divergence = (
        _divergence_event("futures_divergence_le_m60_c1", -0.60),
        _divergence_event("futures_divergence_ge60_c1", 0.60),
    )

    selected = production_events(bearish + (bullish, bullish_retest) + divergence)
    selected_ids = {event.signal_id for event in selected}
    assert {event.signal_id for event in bearish} <= selected_ids
    assert bullish.signal_id in selected_ids
    assert bullish_retest.signal_id not in selected_ids
    assert {event.signal_id for event in divergence} <= selected_ids


def test_legacy_event_frame_defaults_evidence_scope_to_absolute():
    row = _foreign_event("futures_foreign_change_pr0_20_c1", 20).as_dict()
    row.pop("evidence_scope")

    restored = events_from_frame(pd.DataFrame([row]))[0]
    assert restored.evidence_scope == "absolute"


def test_legacy_c10_frame_is_enriched_from_canonical_relative_scope():
    row = _foreign_event("futures_foreign_change_pr0_20_c10", 20).as_dict()
    row.pop("evidence_scope")

    restored = events_from_frame(pd.DataFrame([row]))[0]
    assert restored.evidence_scope == "relative"
    assert production_events((restored,)) == ()


def test_work_site_keeps_relative_research_evidence_out_of_directional_latest(tmp_path):
    c10 = replace(
        _foreign_event("futures_foreign_change_pr0_20_c10", 20),
        signal_date="2026-10-02",
        source_data_date="2026-10-02",
        target_date="2026-10-16",
        calculation_timestamp=NOW.isoformat(),
    )
    artifact = write_current_run_artifacts(
        tmp_path / "current",
        (c10,),
        build_forecast_calendar((c10,)),
        run_id="20261002T201300+0800_relative",
        git_commit="abc123",
        calculated_at=NOW.isoformat(),
        actual_data_date="2026-10-02",
        historical_validation=empty_historical_validation(),
        run_mode="cloud_daily",
        ledger_events=(c10,),
    )

    payload = build_work_site_payload(artifact, generated_at=NOW)
    evidence = next(
        row for row in payload["research_evidence"]
        if row["signal_id"] == "futures_foreign_change_pr0_20_c10"
    )
    assert payload["provenance"]["evaluation_result"] == "VALID_NO_SIGNAL"
    assert payload["latest_signals"] == []
    assert payload["forecast_calendar"] == []
    assert evidence["research_status"] == "RETAINED"
    assert evidence["evidence_scope"] == "relative"


def test_futures_registry_robustness_and_monotonicity_metadata():
    assert _spec("futures_foreign_change_pr0_20_c1").annual_robustness.startswith("15/20")
    assert _spec("futures_foreign_change_pr80_100_c1").annual_robustness.startswith("15/20")
    assert _spec("futures_divergence_le_m60_c1").annual_robustness.startswith("15/20")
    assert _spec("futures_divergence_ge60_c1").annual_robustness.startswith("14/20")
    assert _spec("futures_foreign_change_pr0_20_c3").annual_robustness == "UNKNOWN"
    assert _spec("futures_foreign_change_pr0_20_c10").nonoverlap_robustness == "UNKNOWN"
    for signal_id in (
        "futures_divergence_le_m60_c1",
        "futures_divergence_ge60_c1",
    ):
        assert _spec(signal_id).monotonicity == "4/4 ordered; higher divergence -> higher return"
