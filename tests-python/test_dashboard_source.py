from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from dashboard.dashboard_source import (
    DashboardSource,
    build_snapshot_source,
    load_current_run_artifacts,
    select_dashboard_source,
    write_current_run_artifacts,
)
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.historical_validation import empty_historical_validation
from dashboard.signal_engine import SignalEvent


NOW = pd.Timestamp("2026-09-30 20:30", tz="Asia/Taipei")


def _event(**changes) -> SignalEvent:
    values = dict(
        signal_date="2026-09-30", signal_id="test-signal", economic_signal_id="test-family",
        source="test", subject="test", direction="bullish", horizon=1, target_date="2026-10-01",
        matched=True, evaluation_status="MATCHED", research_status="RETAINED", evidence_grade="A",
        raw_value=1.0, normalized_value=99.0, threshold="PR: [95, +∞]",
        historical_mean_return=None, historical_median_return=None, historical_win_rate=None,
        relative_mean_return=None, sample_size=None, global_fdr=None, family_fdr=None,
        plain_definition="test", market_mechanism="test", risks="test", research_commit="abc",
        research_run="run", calculation_timestamp=NOW.isoformat(), source_data_date="2026-09-30",
    )
    values.update(changes)
    return SignalEvent(**values)


def _write(folder: Path, *, date: str = "2026-09-30", events=None) -> Path:
    events = (_event(),) if events is None else tuple(events)
    return write_current_run_artifacts(
        folder, events, build_forecast_calendar(events), run_id="run-1", git_commit="abc123",
        calculated_at=NOW.isoformat(), actual_data_date=date,
        historical_validation=empty_historical_validation(),
    )


def _dummy_source(source_type: str) -> DashboardSource:
    return DashboardSource(
        source_type, "2026-09-30", NOW.isoformat(), "dummy", "abc", "SUCCESS",
        "VALID_NO_SIGNAL", "FRESH", True, None, (),
        pd.DataFrame(columns=["target_date", "bullish_count", "bearish_count", "net_vote", "active_signals"]),
        pd.DataFrame(), None,
    )


def test_case_a_fresh_complete_current_run_is_selected(tmp_path: Path):
    _write(tmp_path)
    source = select_dashboard_source(tmp_path, now=NOW, live_available=False)
    assert source.source_type == "CURRENT_RUN"
    assert source.freshness == "FRESH"
    assert source.calendar.iloc[0]["target_date"] == "2026-10-01"


def test_case_b_stale_current_run_uses_live_when_available(tmp_path: Path):
    _write(tmp_path, date="2026-09-01")
    source = select_dashboard_source(
        tmp_path, now=NOW, live_available=True,
        live_loader=lambda: _dummy_source("LIVE_FINLAB"),
    )
    assert source.source_type == "LIVE_FINLAB"


@pytest.mark.parametrize("status", ["FAILED", "INCOMPLETE"])
def test_cases_c_d_unsuccessful_manifest_is_rejected(tmp_path: Path, status: str):
    _write(tmp_path)
    path = tmp_path / "run_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["overall_status"] = status
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not successful"):
        load_current_run_artifacts(tmp_path, NOW)


def test_case_e_no_artifact_uses_live_when_available(tmp_path: Path):
    source = select_dashboard_source(
        tmp_path, now=NOW, live_available=True,
        live_loader=lambda: _dummy_source("LIVE_FINLAB"),
    )
    assert source.source_type == "LIVE_FINLAB"


def test_case_f_no_artifact_and_no_finlab_falls_back_with_warning(tmp_path: Path):
    snapshot = _dummy_source("RESEARCH_SNAPSHOT")
    snapshot = DashboardSource(**{**snapshot.__dict__, "is_production": False, "warning": "snapshot warning"})
    source = select_dashboard_source(
        tmp_path, now=NOW, live_available=False, snapshot_loader=lambda: snapshot,
    )
    assert source.source_type == "RESEARCH_SNAPSHOT"
    assert not source.is_production
    assert "snapshot warning" in source.warning
    assert "Current-run artifact" in source.warning


def test_case_g_research_snapshot_exposes_latest_date():
    source = build_snapshot_source(NOW)
    assert source.source_type == "RESEARCH_SNAPSHOT"
    assert source.data_date
    assert "不是最新市場資料" in source.warning


def test_case_h_zero_signal_event_success_remains_current_run(tmp_path: Path):
    _write(tmp_path, events=())
    source = load_current_run_artifacts(tmp_path, NOW)
    assert source.source_type == "CURRENT_RUN"
    assert source.evaluation_result == "VALID_NO_SIGNAL"
    assert source.calendar.empty


def test_case_i_data_unavailable_is_not_valid_no_signal(tmp_path: Path):
    event = _event(matched=False, evaluation_status="DATA_UNAVAILABLE", raw_value=None, normalized_value=None)
    _write(tmp_path, events=(event,))
    source = load_current_run_artifacts(tmp_path, NOW)
    assert source.evaluation_result == "DATA_UNAVAILABLE"
    assert source.evaluation_result != "VALID_NO_SIGNAL"
    assert not source.is_production


def test_case_j_calendar_must_match_active_source_events(tmp_path: Path):
    _write(tmp_path)
    calendar_path = tmp_path / "forecast_calendar.csv"
    calendar = pd.read_csv(calendar_path)
    calendar.loc[0, "target_date"] = "2026-07-15"
    calendar.to_csv(calendar_path, index=False)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_checksums"]["forecast_calendar.csv"] = hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_current_run_artifacts(tmp_path, NOW)
