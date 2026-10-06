"""Validated publication handoff from canonical outputs to the Work Site."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from .dashboard_source import CURRENT_RUN_DIR, load_current_run_artifacts
from .research_registry import CANONICAL_SIGNALS
from .signal_engine import production_events
from .signal_presentation import event_audit_record, historical_validation_results


SCHEMA_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[1]
CURRENT_PAYLOAD_PATH = ROOT / "public" / "data" / "work_site_payload.json"
RUN_ARCHIVE_ROOT = ROOT / "outputs" / "runs"
PUBLICATION_STATUS_PATH = ROOT / "outputs" / "publication_status.json"
PUBLISHABLE_EVALUATIONS = {"SIGNALS_PRESENT", "VALID_NO_SIGNAL"}


class PublicationError(RuntimeError):
    """The canonical run is not safe to expose as the current Site payload."""


def _timestamp(value: object | None = None) -> pd.Timestamp:
    result = pd.Timestamp(value or pd.Timestamp.now(tz="Asia/Taipei"))
    if result.tzinfo is None:
        result = result.tz_localize("Asia/Taipei")
    return result


def _json_safe(value: Any) -> Any:
    """Convert missing values to null and reject non-finite numeric values."""
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if math.isnan(number):
            return None
        if not math.isfinite(number):
            raise PublicationError("publication payload contains an infinite numeric value")
        return number
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, (bool, np.bool_)) and bool(missing):
        return None
    if isinstance(value, (str, int)):
        return value
    raise PublicationError(f"unsupported publication value type: {type(value).__name__}")


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return _json_safe(frame.to_dict(orient="records"))


def _read_manifest(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "run_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublicationError(f"publication manifest missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublicationError(f"publication manifest is malformed: {path}") from exc
    if not isinstance(manifest, dict):
        raise PublicationError("publication manifest must be a JSON object")
    return manifest


def build_work_site_payload(
    artifact_dir: str | Path = CURRENT_RUN_DIR,
    *,
    generated_at: object | None = None,
) -> dict[str, Any]:
    """Validate completed canonical outputs and serialize one Site payload."""
    folder = Path(artifact_dir)
    timestamp = _timestamp(generated_at)
    manifest = _read_manifest(folder)
    try:
        source = load_current_run_artifacts(folder, now=timestamp)
    except Exception as exc:
        raise PublicationError(f"canonical current-run validation failed: {exc}") from exc

    if source.status != "SUCCESS":
        raise PublicationError(f"calculation status is not SUCCESS: {source.status}")
    if source.freshness != "FRESH":
        raise PublicationError(f"current run is not fresh: {source.freshness}")
    if source.evaluation_result not in PUBLISHABLE_EVALUATIONS:
        raise PublicationError(
            f"evaluation result is not publishable: {source.evaluation_result}"
        )
    if manifest.get("run_mode") != "cloud_daily":
        raise PublicationError(
            f"only cloud_daily production runs may be published: {manifest.get('run_mode')}"
        )

    validation = historical_validation_results(source.historical_validation)
    latest_events = production_events(source.events)
    latest_signals = [event_audit_record(event) for event in latest_events]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "data_date": source.data_date,
            "calculation_timestamp": source.calculated_at,
            "run_id": source.run_id,
            "git_commit": source.git_commit,
            "pipeline_status": source.status,
            "evaluation_result": source.evaluation_result,
            "freshness": source.freshness,
            "generated_at": timestamp.isoformat(),
        },
        "forecast_calendar": _records(source.calendar),
        "target_date_calendar": _records(source.target_calendar),
        "latest_signals": _json_safe(latest_signals),
        "historical_validation": _records(validation),
        "research_evidence": _json_safe(
            [asdict(signal) for signal in CANONICAL_SIGNALS]
        ),
        "system_health": {
            "calculation_status": "SUCCESS",
            "publication_status": "SUCCESS",
            "artifact_availability": "AVAILABLE",
            "source_type": source.source_type,
            "historical_validation_status": source.historical_validation_status,
            "run_mode": manifest.get("run_mode"),
            "artifact_checksums_valid": True,
        },
    }
    validate_work_site_payload(payload)
    return payload


def validate_work_site_payload(payload: dict[str, Any]) -> None:
    """Validate the public contract without re-running research calculations."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PublicationError("unsupported work-site payload schema")
    required_sections = {
        "provenance", "forecast_calendar", "target_date_calendar",
        "latest_signals", "historical_validation", "research_evidence",
        "system_health",
    }
    missing_sections = required_sections - set(payload)
    if missing_sections:
        raise PublicationError(
            "work-site payload sections missing: " + ", ".join(sorted(missing_sections))
        )
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PublicationError("work-site provenance must be an object")
    required_provenance = {
        "data_date", "calculation_timestamp", "run_id", "git_commit",
        "pipeline_status", "evaluation_result", "freshness", "generated_at",
    }
    missing_provenance = [
        name for name in sorted(required_provenance) if not provenance.get(name)
    ]
    if missing_provenance:
        raise PublicationError(
            "work-site provenance fields missing: " + ", ".join(missing_provenance)
        )
    if provenance["pipeline_status"] != "SUCCESS":
        raise PublicationError("work-site payload pipeline is not successful")
    if provenance["evaluation_result"] not in PUBLISHABLE_EVALUATIONS:
        raise PublicationError("work-site payload evaluation is not publishable")
    if provenance["freshness"] != "FRESH":
        raise PublicationError("work-site payload is not fresh")
    for name in (
        "forecast_calendar", "target_date_calendar", "latest_signals",
        "historical_validation", "research_evidence",
    ):
        if not isinstance(payload.get(name), list):
            raise PublicationError(f"work-site payload {name} must be an array")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    if decoded != payload:
        raise PublicationError("work-site payload JSON round trip changed values")


def _atomic_json(path: Path, payload: dict[str, Any], *, replace: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        validate_work_site_payload(existing)
        if existing == payload:
            return path
        raise PublicationError(f"immutable publication archive already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        validate_work_site_payload(
            json.loads(temporary.read_text(encoding="utf-8"))
        )
        if not replace and path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            validate_work_site_payload(existing)
            if existing == payload:
                temporary.unlink(missing_ok=True)
                return path
            raise PublicationError(f"immutable publication archive already exists: {path}")
        os.replace(temporary, path)
        return path
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def publish_work_site_payload(
    artifact_dir: str | Path = CURRENT_RUN_DIR,
    *,
    current_path: str | Path = CURRENT_PAYLOAD_PATH,
    archive_root: str | Path = RUN_ARCHIVE_ROOT,
    generated_at: object | None = None,
) -> tuple[Path, Path]:
    """Atomically update current payload and create an immutable run copy."""
    payload = build_work_site_payload(artifact_dir, generated_at=generated_at)
    run_id = str(payload["provenance"]["run_id"])
    archive_path = Path(archive_root) / run_id / "work_site_payload.json"
    _atomic_json(archive_path, payload, replace=False)
    current = _atomic_json(Path(current_path), payload, replace=True)
    return current, archive_path


def write_publication_status(
    status: str,
    *,
    run_id: str,
    error: str | None = None,
    path: str | Path = PUBLICATION_STATUS_PATH,
) -> Path:
    payload = {
        "calculation_status": "SUCCESS",
        "publication_status": status,
        "run_id": run_id,
        "error": error,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def validate_publication_status(
    path: str | Path = PUBLICATION_STATUS_PATH,
) -> None:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublicationError(f"publication status missing: {target}") from exc
    if payload.get("calculation_status") != "SUCCESS":
        raise PublicationError("calculation did not complete successfully")
    if payload.get("publication_status") != "SUCCESS":
        raise PublicationError(
            f"publication did not complete successfully: {payload.get('error')}"
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate-status", "validate-payload"))
    parser.add_argument("--path")
    args = parser.parse_args()
    if args.command == "validate-status":
        validate_publication_status(args.path or PUBLICATION_STATUS_PATH)
    else:
        payload_path = Path(args.path or CURRENT_PAYLOAD_PATH)
        validate_work_site_payload(json.loads(payload_path.read_text(encoding="utf-8")))
