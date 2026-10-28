"""Deterministic mock/staging acceptance evidence for calendar resilience."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile

import pandas as pd

from .official_market import EvidenceStatus, OfficialEvidence
from .pipeline_exceptions import build_exception
from .pipeline_schedule import RunPhase, decide_run
from .review_processor import process_review_row
from .trading_calendar import TradingCalendar
from .calendar_reconciliation import ReconciliationPlan
from .production_apply import (
    ApprovalState, ApplySpec, FilesystemPublicationBackend, OperationStore,
    ProjectionSnapshot, RevisionState, canonical_checksum, execute_apply,
    rows_checksum,
)


class _StagingBackend:
    def __init__(self) -> None:
        self.payload = {"signal_events": [], "forecast_calendar": []}
        self.approval = ApprovalState("approval-v1", "APPROVED_STAGING")

    def verify_backup(self, backup_id: str, expected_checksum: str) -> bool:
        return backup_id == "staging-backup" and expected_checksum == "staging-backup-checksum"

    def source_state(self) -> RevisionState:
        return RevisionState("staging-revision", "staging-source", "staging-calendar-v2", "staging-calendar-checksum")

    def approval_state(self, _event_id: str) -> ApprovalState:
        return self.approval

    def snapshot(self) -> ProjectionSnapshot:
        payload = json.loads(json.dumps(self.payload))
        return ProjectionSnapshot(canonical_checksum(payload), payload)

    def apply_atomic(self, spec: ApplySpec) -> None:
        self.payload = {
            name: [{key: str(value) for key, value in row.items()} for row in spec.datasets[name]]
            for name in ("signal_events", "forecast_calendar")
        }

    def readback_checksum(self) -> str:
        return canonical_checksum(self.payload)

    def rollback(self, snapshot: ProjectionSnapshot) -> None:
        self.payload = snapshot.payload


def _apply_spec(operation_id: str, kind: str = "APPLY", supersedes: str = "") -> tuple[ApplySpec, ReconciliationPlan]:
    plan = ReconciliationPlan("staging-calendar-v2", "staging-source", 1, 0, 1, 0, True, ())
    schemas = {
        "signal_events": ("signal_date", "signal_id", "horizon", "target_date"),
        "forecast_calendar": ("target_date", "net_vote"),
        "historical_validation": ("signal_date", "signal_id", "horizon", "target_date", "maturity"),
    }
    datasets = {
        "signal_events": ({"signal_date": "2026-10-22", "signal_id": "s", "horizon": 1, "target_date": "2026-10-26"},),
        "forecast_calendar": ({"target_date": "2026-10-26", "net_vote": 1},),
        "historical_validation": ({"signal_date": "2026-10-22", "signal_id": "s", "horizon": 1, "target_date": "2026-10-26", "maturity": "PENDING"},),
    }
    diff_checksum = canonical_checksum([])
    meta = {
        "operation_id": operation_id, "calendar_version": plan.calendar_version,
        "calendar_checksum": "staging-calendar-checksum", "source_checksum": plan.source_checksum,
        "plan_checksum": plan.checksum(), "diff_checksum": diff_checksum,
        "signal_events_checksum": rows_checksum(datasets["signal_events"]),
        "forecast_calendar_checksum": rows_checksum(datasets["forecast_calendar"]),
        "historical_validation_checksum": rows_checksum(datasets["historical_validation"]),
    }
    return ApplySpec(
        operation_id, "staging-event", kind, "staging-backup", "staging-backup-checksum",
        "staging-revision", plan.source_checksum, plan.calendar_version,
        "staging-calendar-checksum", plan.checksum(), diff_checksum, "approval-v1", True,
        schemas, datasets, {"schema_version": "1.1", "reconciliation": meta}, supersedes,
    ), plan


def _production_apply_simulation() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="calendar-resilience-") as folder:
        root = Path(folder)
        backend = _StagingBackend()
        publication = FilesystemPublicationBackend(root / "current.json", root / "versions")
        operations = OperationStore(root / "operations")
        spec, plan = _apply_spec("staging-apply")
        first = execute_apply(spec, plan, backend, publication, operations)
        repeated = execute_apply(spec, plan, backend, publication, operations)
        reversal, reverse_plan = _apply_spec("staging-reversal", "REVERSAL", "staging-apply")
        reversed_result = execute_apply(reversal, reverse_plan, backend, publication, operations)
        return {
            "apply_status": first.status,
            "repeat_is_no_op": repeated.no_op,
            "reversal_status": reversed_result.status,
            "original_superseded_by": operations.read("staging-apply").get("superseded_by"),
            "projection_checksum": backend.readback_checksum(),
            "publication_checksum": publication.pointer_checksum(),
            "production_sheet_written": False,
        }


def build_simulation_report() -> dict[str, object]:
    calendar = TradingCalendar(
        pd.DatetimeIndex(["2026-10-09", "2026-10-26"]), "mock", "mock-v1"
    )
    primary = decide_run("2026-10-22 20:13+08:00", RunPhase.PRIMARY, set(), calendar=calendar)
    fallback = decide_run("2026-10-22 20:47+08:00", RunPhase.FALLBACK, set(), calendar=calendar)
    final = decide_run(
        "2026-10-23 05:00+08:00", RunPhase.FINAL,
        {"2026-10-20", "2026-10-21"}, calendar=calendar,
    )
    official = OfficialEvidence(
        "TWSE", "DAY", "2026-10-22", EvidenceStatus.TRADED,
        ("2026-10-22",), "mock://twse", "2026-10-23T05:00:00+08:00",
    )
    exception = build_exception(
        "2026-10-22", "2026-10-21", "finlab", official,
        configured_is_session=True, retry_count=3,
    )
    closure_evidence = OfficialEvidence(
        "TWSE", "DAY", "2026-10-23", EvidenceStatus.NO_RECORD,
        ("2026-10-22",), "mock://twse-announcement", "2026-10-24T05:00:00+08:00",
    )
    closure = build_exception(
        "2026-10-23", "2026-10-22", "finlab", closure_evidence,
        configured_is_session=False, retry_count=3,
    )
    review = process_review_row({
        **asdict(closure),
        "review_decision": "CONFIRM_CLOSED",
        "review_confirm": True,
    })
    return {
        "checkpoints": [asdict(primary), asdict(fallback), asdict(final)],
        "official_trade_classification": exception.exception_type,
        "closure_classification": closure.exception_type,
        "review_processing_status": review.processing_status,
        "override_reconciliation_status": review.override["reconciliation_status"],
        "production_apply_simulation": _production_apply_simulation(),
        "production_sheet_written": False,
    }


def run(path: str | Path = "outputs/calendar_resilience_simulation.json") -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_simulation_report(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
