"""Build an immutable, self-consistent input bundle for calendar_apply."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .calendar_apply import load_bundle
from .calendar_reconciliation import ReconciliationPlan, TargetDiff
from .google_sheet_service import CALENDAR_HEADERS, EVENT_HEADERS
from .historical_validation import HISTORICAL_VALIDATION_COLUMNS
from .production_apply import ApplySpec, canonical_checksum, rows_checksum
from .work_site_publication import validate_work_site_payload


def _rows(path: str | Path, headers: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    frame = pd.read_csv(path, keep_default_na=False)
    if tuple(frame.columns) != headers:
        raise RuntimeError(f"schema mismatch for {path}: {tuple(frame.columns)}")
    return tuple(
        {header: (value.item() if hasattr(value, "item") else value) for header, value in row.items()}
        for row in frame.to_dict("records")
    )


def build_bundle(
    *, plan: ReconciliationPlan, operation_id: str, event_id: str,
    operation_kind: str, backup_id: str, backup_checksum: str,
    expected_source_revision: str, calendar_checksum: str,
    approval_version: str, signal_events_path: str | Path,
    forecast_calendar_path: str | Path, historical_validation_path: str | Path,
    work_site_payload_path: str | Path, supersedes_operation_id: str = "",
) -> dict[str, Any]:
    schemas = {
        "signal_events": tuple(EVENT_HEADERS),
        "forecast_calendar": tuple(CALENDAR_HEADERS),
        "historical_validation": tuple(HISTORICAL_VALIDATION_COLUMNS),
    }
    datasets = {
        "signal_events": _rows(signal_events_path, schemas["signal_events"]),
        "forecast_calendar": _rows(forecast_calendar_path, schemas["forecast_calendar"]),
        "historical_validation": _rows(historical_validation_path, schemas["historical_validation"]),
    }
    diff_checksum = canonical_checksum([asdict(row) for row in plan.diffs])
    reconciliation = {
        "operation_id": operation_id,
        "calendar_version": plan.calendar_version,
        "calendar_checksum": calendar_checksum,
        "source_checksum": plan.source_checksum,
        "plan_checksum": plan.checksum(),
        "diff_checksum": diff_checksum,
        "signal_events_checksum": rows_checksum(datasets["signal_events"]),
        "forecast_calendar_checksum": rows_checksum(datasets["forecast_calendar"]),
        "historical_validation_checksum": rows_checksum(datasets["historical_validation"]),
    }
    publication = json.loads(Path(work_site_payload_path).read_text(encoding="utf-8"))
    publication["reconciliation"] = reconciliation
    validate_work_site_payload(publication)
    spec = ApplySpec(
        operation_id, event_id, operation_kind, backup_id, backup_checksum,
        expected_source_revision, plan.source_checksum, plan.calendar_version,
        calendar_checksum, plan.checksum(), diff_checksum, approval_version,
        plan.negative_controls_passed and plan.unresolved_rows == 0,
        schemas, datasets, publication, supersedes_operation_id,
    )
    return {"spec": asdict(spec), "plan": asdict(plan),
            "bundle_checksum": canonical_checksum({"spec": asdict(spec), "plan": asdict(plan)})}


def run() -> int:
    plan_payload = json.loads(Path(os.environ["RECONCILIATION_PLAN_PATH"]).read_text(encoding="utf-8"))
    plan_payload["diffs"] = tuple(TargetDiff(**row) for row in plan_payload.get("diffs", []))
    plan = ReconciliationPlan(**plan_payload)
    bundle = build_bundle(
        plan=plan,
        operation_id=os.environ["OPERATION_ID"], event_id=os.environ["EVENT_ID"],
        operation_kind=os.environ.get("OPERATION_KIND", "APPLY"),
        backup_id=os.environ["BACKUP_ID"], backup_checksum=os.environ["BACKUP_CHECKSUM"],
        expected_source_revision=os.environ["EXPECTED_SOURCE_REVISION"],
        calendar_checksum=os.environ["CALENDAR_CHECKSUM"],
        approval_version=os.environ["APPROVAL_VERSION"],
        signal_events_path=os.environ["REBUILT_SIGNAL_EVENTS_PATH"],
        forecast_calendar_path=os.environ["REBUILT_FORECAST_CALENDAR_PATH"],
        historical_validation_path=os.environ["REBUILT_HISTORICAL_VALIDATION_PATH"],
        work_site_payload_path=os.environ["REBUILT_WORK_SITE_PAYLOAD_PATH"],
        supersedes_operation_id=os.environ.get("SUPERSEDES_OPERATION_ID", ""),
    )
    target = Path(os.environ.get("APPLY_BUNDLE_PATH", "outputs/calendar_apply_bundle.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    # Round-trip through the executor parser before the bundle is accepted.
    load_bundle(target)
    print(f"bundle={target}; checksum={bundle['bundle_checksum']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
