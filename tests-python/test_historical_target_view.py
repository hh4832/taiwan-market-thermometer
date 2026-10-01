from dataclasses import replace

import pandas as pd
import pytest

from dashboard.daily_email import build_vnext_report
from dashboard.forecast_calendar import aggregate_events_by_target_date, contributing_events
from dashboard.google_sheet_service import EVENT_HEADERS, sync_signal_events
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import SignalEvent, evaluate_signals, events_from_frame, rolling_pr, rolling_z
from dashboard.signal_ledger import build_historical_signal_events, merge_signal_events
from dashboard.signal_presentation import event_audit_record, event_summary_record


def _event(**changes) -> SignalEvent:
    values = dict(
        signal_date="2026-09-25", signal_id="a", economic_signal_id="family-a",
        source="test", subject="test", direction="bullish", horizon=1,
        target_date="2026-09-28", matched=True, evaluation_status="MATCHED",
        research_status="RETAINED", evidence_grade="A", raw_value=-0.083,
        normalized_value=12.4, threshold="rolling_pr: [0, 20)",
        historical_mean_return=.01, historical_median_return=.008,
        historical_win_rate=.6, relative_mean_return=.004, sample_size=100,
        global_fdr=.01, family_fdr=.005, plain_definition="原始定義",
        market_mechanism="測試機制", risks="測試風險", research_commit="abc",
        research_run="run", calculation_timestamp="2026-09-25T20:00:00+08:00",
        source_data_date="2026-09-25", metric="foreign_net_oi_change_ratio_3d",
        normalization="rolling_pr_strict_prior", event_origin="BACKFILL",
        availability_status="KNOWN",
    )
    values.update(changes)
    return SignalEvent(**values)


def test_as_of_backfill_is_unchanged_when_future_values_are_mutated():
    spec = next(s for s in CANONICAL_SIGNALS if s.signal_id == "breadth_up_ratio_1d_pr60_ge95_c1")
    sessions = pd.bdate_range("2026-01-01", periods=90)
    d0 = sessions[70]
    original = pd.Series(range(90), index=sessions, dtype=float)
    mutated = original.copy()
    mutated.loc[mutated.index > d0] = -999999
    left = build_historical_signal_events({"up_ratio": original}, sessions, [d0], "2026-09-29", (spec,))[0]
    right = build_historical_signal_events({"up_ratio": mutated}, sessions, [d0], "2026-09-29", (spec,))[0]
    assert (left.raw_value, left.normalized_value, left.matched) == (right.raw_value, right.normalized_value, right.matched)


def test_backfill_matches_direct_evaluation_on_same_truncated_input():
    spec = next(s for s in CANONICAL_SIGNALS if s.signal_id == "futures_foreign_change_pr0_20_c1")
    sessions = pd.bdate_range("2026-01-01", periods=150)
    d0 = sessions[130]
    metric = pd.Series(range(150), index=sessions, dtype=float)
    direct = evaluate_signals({spec.metric: metric.loc[:d0]}, sessions, signals=(spec,), as_of_date=d0)[0]
    backfill = build_historical_signal_events({spec.metric: metric}, sessions, [d0], "2026-09-29", (spec,))[0]
    assert direct.raw_value == backfill.raw_value
    assert direct.normalized_value == backfill.normalized_value
    assert direct.matched == backfill.matched


def test_normalization_math_inclusive_and_strict_prior():
    values = pd.Series([1.0, 2.0, 3.0, 100.0])
    assert rolling_pr(values, 3, False).iloc[2] == 100.0
    assert rolling_pr(values, 3, True).iloc[3] == 100.0
    assert rolling_z(values, 3, False).iloc[2] == pytest.approx(1.224744871)
    assert rolling_z(values, 3, True).iloc[3] == pytest.approx((100 - 2) / (2 / 3) ** .5)


def test_target_date_aggregate_counts_three_bullish_two_bearish_vintages():
    target = "2026-09-30"
    events = tuple(
        _event(signal_date=f"2026-09-{20+i:02d}", signal_id=f"b{i}", economic_signal_id=f"bull-{i}", target_date=target)
        for i in range(3)
    ) + tuple(
        _event(signal_date=f"2026-09-{23+i:02d}", signal_id=f"s{i}", economic_signal_id=f"bear-{i}", direction="bearish", target_date=target)
        for i in range(2)
    )
    row = aggregate_events_by_target_date(events).iloc[0]
    assert (row.bullish_count, row.bearish_count, row.net_vote, row.contributing_event_count) == (3, 2, 1, 5)
    assert len(contributing_events(events, target)) == 5


def test_presentation_keeps_semantic_raw_value_and_unknowns():
    summary = event_summary_record(_event())
    audit = event_audit_record(_event())
    assert summary["指標原始值"] == "-8.30%"
    assert summary["PR / Z"] == "PR 12.40"
    assert audit["historical_mean_return"] == "+1.000%"
    unknown = event_audit_record(_event(raw_value=None, normalized_value=None, global_fdr=None))
    assert unknown["指標原始值"] == "無法判定"
    assert unknown["normalized_value"] == "無法判定"
    assert unknown["global_fdr"] == "無法判定"


def test_ledger_is_idempotent_and_production_wins_over_backfill():
    backfill = _event()
    production = replace(backfill, event_origin="PRODUCTION", raw_value=.25)
    merged = merge_signal_events((production,), (backfill,))
    assert len(merged) == 1 and merged[0].raw_value == .25
    rerun = merge_signal_events(merged, (backfill, production))
    assert len(rerun) == 1 and rerun[0].event_origin == "PRODUCTION"


def test_sheet_upsert_does_not_let_backfill_overwrite_production():
    class FakeSheet:
        def __init__(self):
            self.values = [EVENT_HEADERS]

        def get_all_values(self):
            return self.values

        def update(self, matrix, *_args, **_kwargs):
            self.values = matrix

        def freeze(self, **_kwargs):
            return None

    sheet = FakeSheet()
    production = replace(_event(), event_origin="PRODUCTION", raw_value=.25)
    sync_signal_events(sheet, (production,), "run-1", "abc")
    sync_signal_events(sheet, (_event(raw_value=-.5),), "run-2", "def")
    assert len(sheet.values) == 2
    stored = dict(zip(EVENT_HEADERS, sheet.values[1]))
    assert stored["event_origin"] == "PRODUCTION"
    assert stored["raw_value"] == .25


def test_events_from_frame_accepts_legacy_blank_optional_cells():
    row = _event(event_origin="PRODUCTION").as_dict()
    for name in (
        "raw_value", "normalized_value", "historical_mean_return", "historical_median_return",
        "historical_win_rate", "relative_mean_return", "sample_size", "global_fdr", "family_fdr",
    ):
        row[name] = ""
    row["event_origin"] = ""
    row["availability_status"] = ""
    restored = events_from_frame(pd.DataFrame([row]))[0]
    assert restored.raw_value is None
    assert restored.normalized_value is None
    assert restored.historical_mean_return is None
    assert restored.historical_median_return is None
    assert restored.historical_win_rate is None
    assert restored.relative_mean_return is None
    assert restored.sample_size is None
    assert restored.global_fdr is None
    assert restored.family_fdr is None
    assert restored.event_origin == "PRODUCTION"
    assert restored.availability_status == "KNOWN"


def test_email_target_section_includes_counts_and_audit_indicators():
    target = "2026-09-30"
    events = (_event(target_date=target), _event(signal_id="bear", economic_signal_id="bear", direction="bearish", target_date=target))
    target_calendar = aggregate_events_by_target_date(events)
    _subject, plain, _html = build_vnext_report(
        (), pd.DataFrame(), target, "run", "abc", "SUCCESS",
        target_events=events, target_calendar=target_calendar,
    )
    assert "1 多 / 1 空｜淨票 +0" in plain
    assert "原始資料 -8.30%" in plain
    assert "標準化 PR 12.40" in plain
    assert "Global FDR 0.01" in plain
