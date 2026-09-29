"""Run completion contract.  SUCCESS is impossible when a mandatory stage fails."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


MANDATORY_STAGES = ("data_fetch", "registry", "signal_evaluation", "signal_events", "forecast_calendar", "sheet_write", "archive")


@dataclass
class RunManifest:
    run_id: str
    run_timestamp: str
    git_commit: str
    expected_data_date: str | None
    actual_data_date: str | None
    stage_status: dict[str, str] = field(default_factory=dict)
    email_status: str = "NOT_ATTEMPTED"
    daily_record_action: str = "UNKNOWN"
    signal_event_count: int = 0
    calendar_row_count: int = 0
    overall_status: str = "INCOMPLETE"

    def finalize(self) -> str:
        statuses = [self.stage_status.get(stage) for stage in MANDATORY_STAGES]
        if any(value == "STALE_DATA" for value in statuses) or (
            self.expected_data_date and self.actual_data_date and self.expected_data_date != self.actual_data_date
        ):
            self.overall_status = "STALE_DATA"
        elif all(value == "SUCCESS" for value in statuses) and self.daily_record_action in {"inserted", "updated", "VALID_NO_SIGNAL"}:
            self.overall_status = "SUCCESS"
        elif any(value == "FAILED" for value in statuses):
            self.overall_status = "FAILED"
        else:
            self.overall_status = "INCOMPLETE"
        return self.overall_status

    def write(self, path: str | Path) -> Path:
        self.finalize()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return target


def validate_manifest(path: str | Path) -> None:
    target = Path(path)
    if not target.is_file():
        raise RuntimeError(f"completion manifest missing: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("overall_status") != "SUCCESS":
        raise RuntimeError(f"pipeline did not complete successfully: {payload.get('overall_status')}")
    for stage in MANDATORY_STAGES:
        if payload.get("stage_status", {}).get(stage) != "SUCCESS":
            raise RuntimeError(f"mandatory stage not successful: {stage}")
