from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dashboard.daily_email import build_vnext_report
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.google_sheet_service import EVENT_HEADERS, RUN_AUDIT_HEADERS, LIVE_TRACK_RECORD_HEADERS
from dashboard.historical_validation import load_historical_validation
from dashboard.report_analytics import build_episode_index, build_live_track_record
from dashboard.signal_engine import events_from_frame
from dashboard.trading_calendar import official_sessions, target_session

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "daily-report-2026-11-04"
RUN = FIXTURE / "run"
SESSIONS = official_sessions("2026-08-31", "2026-12-31")


def _fixture_inputs():
    current = events_from_frame(pd.read_csv(RUN / "signal_events.csv"))
    history_paths = sorted(RUN.glob("signal_events_history_*.csv"))
    history = pd.concat([pd.read_csv(path) for path in history_paths], ignore_index=True)
    ledger = events_from_frame(history)
    historical = load_historical_validation(RUN / "historical_validation.csv")
    manifest = json.loads((RUN / "run_manifest.json").read_text(encoding="utf-8"))
    context = json.loads((FIXTURE / "report_context.json").read_text(encoding="utf-8"))
    prices = pd.read_csv(FIXTURE / "ohlc_manual_check.csv").set_index("date")
    adjusted_open = pd.to_numeric(prices["open"], errors="coerce").dropna()
    adjusted_open.index = pd.to_datetime(adjusted_open.index)
    adjusted_close = pd.to_numeric(prices["close"], errors="coerce").dropna()
    adjusted_close.index = pd.to_datetime(adjusted_close.index)
    return current, ledger, historical, manifest, context, adjusted_open, adjusted_close


def _render_fixture() -> tuple[str, str, pd.DataFrame]:
    current, ledger, historical, manifest, context, opens, closes = _fixture_inputs()
    track, _ = build_live_track_record(
        ledger, opens, closes, SESSIONS, manifest["actual_data_date"]
    )
    before = build_forecast_calendar(current)
    _subject, plain, _html = build_vnext_report(
        current,
        before,
        manifest["actual_data_date"],
        manifest["run_id"],
        manifest["git_commit"],
        "SUCCESS",
        target_events=ledger,
        target_calendar=pd.read_csv(RUN / "target_date_calendar.csv"),
        historical_validation=historical,
        publication_status=context["publication_status"],
        snapshot=context,
        adjusted_open=opens,
        adjusted_close=closes,
        trading_sessions=SESSIONS,
        live_track_record=track,
        spreadsheet_url=context["spreadsheet_url"],
        sheet_write_note=context["sheet_write_note"],
    )
    pd.testing.assert_frame_equal(before, build_forecast_calendar(current))
    frozen = pd.read_csv(RUN / "forecast_calendar.csv")
    assert list(before.columns) == list(frozen.columns)
    assert before.to_dict("records") == frozen.to_dict("records")
    return plain, manifest["actual_data_date"], track


def test_20261104_new_report_matches_golden_and_remains_compact():
    report, report_date, _track = _render_fixture()
    assert report_date == "2026-11-04"
    expected = (FIXTURE / "new-report.txt").read_text(encoding="utf-8").rstrip("\n")
    assert report == expected
    main = report.split("④ 明細", 1)[0]
    assert len(main.splitlines()) <= 25
    assert len(main) <= 1200
    assert "多空票數不是上漲機率；歷史勝率不是本次命中機率。" in report


def test_20261104_old_report_snapshot_is_kept_for_comparison():
    old = (FIXTURE / "old-report.txt").read_text(encoding="utf-8")
    assert "01｜下一交易日預測" in old
    assert "04｜Research / Experimental Details" in old
    assert "05｜Run Information" in old
    assert "Global FDR" in old and "Family FDR" in old


def test_retest_watchlist_has_one_row_per_economic_signal_id():
    report, _date, _track = _render_fixture()
    section = report.split("③ 觀察名單（RETEST／非正式，不計票）", 1)[1].split("④ 明細", 1)[0]
    assert section.count("外資賣出比率極端｜") == 1
    assert section.count("PCR 偏低／Call 相對占優｜") == 1


def test_every_legacy_detail_field_remains_in_sheet_schema():
    available = set(EVENT_HEADERS + RUN_AUDIT_HEADERS + LIVE_TRACK_RECORD_HEADERS)
    legacy_mappings = {
        "variant window": {"rolling_window"},
        "threshold": {"threshold"},
        "Global FDR": {"global_fdr"},
        "Family FDR": {"family_fdr"},
        "Annual": {"annual_robustness"},
        "PARAMETER_ISLAND": {"research_flag", "parameter_island"},
        "Raw": {"raw_value"},
        "Z/PR": {"normalized_value", "normalization"},
        "Mean": {"historical_mean_return"},
        "Median": {"historical_median_return"},
        "Win": {"historical_win_rate"},
        "N": {"sample_size"},
        "Run ID": {"run_id"},
        "Git commit": {"git_commit"},
        "Data Date": {"data_date"},
        "Pipeline Status": {"pipeline_status"},
        "Publication Status": {"publication_status"},
        "Sheet write statistics": {"sheet_write_stats"},
    }
    for label, required in legacy_mappings.items():
        assert required <= available, label
    assert EVENT_HEADERS[:38] == [
        "signal_date", "signal_id", "economic_signal_id", "source", "subject", "direction", "horizon",
        "target_date", "matched", "evaluation_status", "research_status", "evidence_grade", "raw_value",
        "normalized_value", "threshold", "historical_mean_return", "historical_median_return",
        "historical_win_rate", "relative_mean_return", "sample_size", "global_fdr", "family_fdr",
        "plain_definition", "market_mechanism", "risks", "research_commit", "research_run",
        "calculation_timestamp", "source_data_date", "run_id", "git_commit", "metric", "normalization",
        "event_origin", "availability_status", "evidence_scope", "activation_ready", "daily_evaluation_ready",
    ]


def test_20261029_foreign_sell_c5_uses_one_episode_and_hand_calculates_due_return():
    _current, ledger, _historical, _manifest, _context, opens, closes = _fixture_inputs()
    signal = next(
        event for event in ledger
        if event.signal_id == "spot_combined_foreign_sell10_z_ge25_c5"
        and event.signal_date == "2026-10-29"
        and event.matched
    )
    _episodes, event_episode = build_episode_index(ledger, SESSIONS)
    # Daily matched observations on 10/27–10/30 and 11/02–11/04 share the
    # episode that starts at 10/27 under the C5 session-gap rule.
    assert event_episode[(signal.signal_id, signal.signal_date)] == "2026-10-27"
    assert target_session("2026-10-27", 1, SESSIONS) == pd.Timestamp("2026-10-28")
    assert target_session("2026-10-27", 5, SESSIONS) == pd.Timestamp("2026-11-03")

    track, _lookup = build_live_track_record(
        ledger, opens, closes, SESSIONS, "2026-11-04"
    )
    row = track.loc[
        track["signal_id"].eq(signal.signal_id)
        & track["episode_start"].eq("2026-10-27")
    ].iloc[0]
    manual = 100.60 / 108.35 - 1
    assert row["status"] == "DUE"
    assert row["calculation_status"] == "READY"
    assert row["realized_return"] == pytest.approx(manual)
    assert row["hit"] is False or row["hit"] == False  # noqa: E712
    assert round(float(row["realized_return"]) * 100, 1) == -7.2


def test_live_track_sheet_has_required_columns_and_unavailable_is_not_zero():
    _current, ledger, _historical, manifest, _context, _opens, _closes = _fixture_inputs()
    track, _lookup = build_live_track_record(
        ledger, None, None, SESSIONS, manifest["actual_data_date"]
    )
    required = {
        "signal_id", "economic_signal_id", "episode_start", "horizon", "direction",
        "realized_return", "hit", "status",
    }
    assert required <= set(LIVE_TRACK_RECORD_HEADERS)
    assert set(track["status"]).issubset({"DUE", "OPEN"})
    assert track["realized_return"].isna().all()
    assert track["hit"].isna().all()
    assert track["calculation_status"].eq("DATA_UNAVAILABLE").all()


def test_publication_failure_is_marked_in_report_body():
    current, _ledger, _historical, manifest, context, *_ = _fixture_inputs()
    _subject, report, _html = build_vnext_report(
        current,
        pd.read_csv(RUN / "forecast_calendar.csv"),
        manifest["actual_data_date"],
        manifest["run_id"],
        manifest["git_commit"],
        "SUCCESS",
        publication_status="FAILED",
        snapshot=context,
        historical_validation=_historical,
    )
    assert "⚠️ Publication Status=FAILED" in report
