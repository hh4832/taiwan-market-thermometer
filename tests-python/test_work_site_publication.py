from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dashboard.dashboard_source import write_current_run_artifacts
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.historical_validation import HISTORICAL_VALIDATION_COLUMNS, empty_historical_validation
from dashboard.signal_engine import SignalEvent
from dashboard.work_site_publication import (
    PublicationError,
    build_work_site_payload,
    publish_work_site_payload,
    validate_work_site_payload,
)


NOW = pd.Timestamp("2026-10-02 20:13", tz="Asia/Taipei")


def _event(**changes: object) -> SignalEvent:
    values: dict[str, object] = {
        "signal_date": "2026-10-02",
        "signal_id": "publication-test",
        "economic_signal_id": "publication-family",
        "source": "test",
        "subject": "test",
        "direction": "bullish",
        "horizon": 1,
        "target_date": "2026-10-05",
        "matched": True,
        "evaluation_status": "MATCHED",
        "research_status": "RETAINED",
        "evidence_grade": "A",
        "raw_value": 1.0,
        "normalized_value": 99.0,
        "threshold": "PR >= 95",
        "historical_mean_return": 0.01,
        "historical_median_return": 0.009,
        "historical_win_rate": 0.6,
        "relative_mean_return": 0.005,
        "sample_size": 100,
        "global_fdr": 0.01,
        "family_fdr": 0.01,
        "plain_definition": "publication test",
        "market_mechanism": "test",
        "risks": "test",
        "research_commit": "research-abc",
        "research_run": "research-run",
        "calculation_timestamp": NOW.isoformat(),
        "source_data_date": "2026-10-02",
        "event_origin": "PRODUCTION",
        "availability_status": "KNOWN",
    }
    values.update(changes)
    return SignalEvent(**values)  # type: ignore[arg-type]


def _validation_row(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "signal_date": "2026-09-30",
        "signal_id": "historical-test",
        "economic_signal_id": "historical-family",
        "source": "test",
        "direction": "bullish",
        "horizon": 1,
        "entry_date": "2026-10-01",
        "target_date": "2026-10-01",
        "actual_return": 0.01,
        "maturity": "MATURED",
        "event_origin": "PRODUCTION",
        "research_status": "RETAINED",
        "research_commit": "research-abc",
    }
    values.update(changes)
    return values


def _write_run(
    folder: Path,
    *,
    events: tuple[SignalEvent, ...] | None = None,
    validation: pd.DataFrame | None = None,
) -> Path:
    selected = (_event(),) if events is None else events
    return write_current_run_artifacts(
        folder,
        selected,
        build_forecast_calendar(selected),
        run_id="20261002T201300+0800_test",
        git_commit="abc123",
        calculated_at=NOW.isoformat(),
        actual_data_date="2026-10-02",
        historical_validation=(
            empty_historical_validation() if validation is None else validation
        ),
        pipeline_status="SUCCESS",
        run_mode="cloud_daily",
        ledger_events=selected,
    )


def test_normal_success_run_builds_complete_payload(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    payload = build_work_site_payload(run, generated_at=NOW)
    validate_work_site_payload(payload)
    assert payload["schema_version"] == "1.0"
    assert len(payload["latest_signals"]) == 1
    assert payload["provenance"]["evaluation_result"] == "SIGNALS_PRESENT"


def test_valid_no_signal_is_publishable_with_empty_latest_signals(tmp_path: Path):
    run = _write_run(tmp_path / "current", events=())
    payload = build_work_site_payload(run, generated_at=NOW)
    assert payload["provenance"]["evaluation_result"] == "VALID_NO_SIGNAL"
    assert payload["latest_signals"] == []
    assert payload["forecast_calendar"] == []


def test_data_unavailable_is_not_publishable(tmp_path: Path):
    unavailable = _event(
        matched=False,
        evaluation_status="DATA_UNAVAILABLE",
        raw_value=None,
        normalized_value=None,
        source_data_date=None,
        availability_status="DATA_UNAVAILABLE",
    )
    run = _write_run(tmp_path / "current", events=(unavailable,))
    with pytest.raises(PublicationError, match="DATA_UNAVAILABLE"):
        build_work_site_payload(run, generated_at=NOW)


def test_missing_artifact_is_rejected(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    (run / "target_date_calendar.csv").unlink()
    with pytest.raises(PublicationError, match="validation failed"):
        build_work_site_payload(run, generated_at=NOW)


def test_malformed_artifact_is_rejected(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    (run / "signal_events.csv").write_text("not,the,canonical,schema\n", encoding="utf-8")
    with pytest.raises(PublicationError, match="validation failed"):
        build_work_site_payload(run, generated_at=NOW)


def test_nan_serializes_as_null_and_infinity_is_rejected(tmp_path: Path):
    pending = pd.DataFrame(
        [_validation_row(actual_return=None, maturity="PENDING")],
        columns=HISTORICAL_VALIDATION_COLUMNS,
    )
    run = _write_run(tmp_path / "current", validation=pending)
    payload = build_work_site_payload(run, generated_at=NOW)
    assert payload["historical_validation"][0]["actual_return"] is None
    assert payload["historical_validation"][0]["directional_return"] is None
    payload["forecast_calendar"][0]["net_vote"] = float("inf")
    with pytest.raises((PublicationError, ValueError)):
        validate_work_site_payload(payload)


@pytest.mark.parametrize(
    ("direction", "actual_return", "expected_return", "expected_outcome"),
    [
        ("bullish", 0.01, 0.01, "HIT"),
        ("bearish", 0.01, -0.01, "MISS"),
    ],
)
def test_historical_validation_directional_results_reuse_shared_helper(
    tmp_path: Path,
    direction: str,
    actual_return: float,
    expected_return: float,
    expected_outcome: str,
):
    validation = pd.DataFrame(
        [_validation_row(direction=direction, actual_return=actual_return)],
        columns=HISTORICAL_VALIDATION_COLUMNS,
    )
    run = _write_run(tmp_path / "current", validation=validation)
    row = build_work_site_payload(run, generated_at=NOW)["historical_validation"][0]
    assert row["actual_return"] == actual_return
    assert row["directional_return"] == expected_return
    assert row["outcome"] == expected_outcome


def test_pending_keeps_directional_result_null(tmp_path: Path):
    validation = pd.DataFrame(
        [_validation_row(actual_return=None, maturity="PENDING")],
        columns=HISTORICAL_VALIDATION_COLUMNS,
    )
    run = _write_run(tmp_path / "current", validation=validation)
    row = build_work_site_payload(run, generated_at=NOW)["historical_validation"][0]
    assert row["directional_return"] is None
    assert row["outcome"] == "PENDING"


@pytest.mark.parametrize("origin", ["BACKFILL", "PRODUCTION"])
def test_event_origin_is_preserved(tmp_path: Path, origin: str):
    validation = pd.DataFrame(
        [_validation_row(event_origin=origin)],
        columns=HISTORICAL_VALIDATION_COLUMNS,
    )
    run = _write_run(tmp_path / "current", validation=validation)
    row = build_work_site_payload(run, generated_at=NOW)["historical_validation"][0]
    assert row["event_origin"] == origin


def test_provenance_fields_are_preserved(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    provenance = build_work_site_payload(run, generated_at=NOW)["provenance"]
    assert provenance == {
        "data_date": "2026-10-02",
        "calculation_timestamp": NOW.isoformat(),
        "run_id": "20261002T201300+0800_test",
        "git_commit": "abc123",
        "pipeline_status": "SUCCESS",
        "evaluation_result": "SIGNALS_PRESENT",
        "freshness": "FRESH",
        "generated_at": NOW.isoformat(),
    }


def test_atomic_publication_writes_current_and_immutable_archive(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    current = tmp_path / "public" / "work_site_payload.json"
    archive_root = tmp_path / "runs"
    current_path, archive_path = publish_work_site_payload(
        run,
        current_path=current,
        archive_root=archive_root,
        generated_at=NOW,
    )
    assert current_path == current
    assert archive_path == archive_root / "20261002T201300+0800_test" / "work_site_payload.json"
    assert json.loads(current.read_text(encoding="utf-8")) == json.loads(
        archive_path.read_text(encoding="utf-8")
    )
    assert not list(current.parent.glob("*.tmp"))


def test_incomplete_run_does_not_replace_last_known_good_payload(tmp_path: Path):
    run = _write_run(tmp_path / "current")
    current = tmp_path / "public" / "work_site_payload.json"
    current.parent.mkdir(parents=True)
    current.write_text('{"last_known_good": true}\n', encoding="utf-8")
    before = current.read_bytes()
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["overall_status"] = "INCOMPLETE"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PublicationError):
        publish_work_site_payload(
            run,
            current_path=current,
            archive_root=tmp_path / "runs",
            generated_at=NOW,
        )
    assert current.read_bytes() == before
