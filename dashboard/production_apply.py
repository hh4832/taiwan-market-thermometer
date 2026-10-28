"""Guarded, idempotent production reconciliation execution.

The executor owns the cross-resource state machine.  A projection backend must
make the Google Sheets visibility change atomically; a publication backend must
stage an immutable version before switching its pointer.  No force/bypass path
exists by design.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol

from .calendar_reconciliation import ReconciliationPlan


class ApplyError(RuntimeError):
    pass


class GateError(ApplyError):
    pass


class RecoveryRequired(ApplyError):
    pass


class OperationStatus(str, Enum):
    APPLYING = "APPLYING"
    SUCCESS = "SUCCESS"
    ROLLED_BACK = "ROLLED_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


def canonical_checksum(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def sheet_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def rows_checksum(rows: tuple[dict[str, Any], ...]) -> str:
    return canonical_checksum(list(rows))


@dataclass(frozen=True)
class RevisionState:
    revision: str
    checksum: str
    calendar_version: str
    calendar_checksum: str


@dataclass(frozen=True)
class ApprovalState:
    version: str
    status: str


@dataclass(frozen=True)
class ProjectionSnapshot:
    checksum: str
    payload: Any


@dataclass(frozen=True)
class ApplySpec:
    operation_id: str
    event_id: str
    operation_kind: str
    backup_id: str
    backup_checksum: str
    expected_source_revision: str
    source_checksum: str
    calendar_version: str
    calendar_checksum: str
    plan_checksum: str
    diff_checksum: str
    approval_version: str
    negative_controls_passed: bool
    schemas: dict[str, tuple[str, ...]]
    datasets: dict[str, tuple[dict[str, Any], ...]]
    publication_payload: dict[str, Any]
    supersedes_operation_id: str = ""

    def request_checksum(self) -> str:
        return canonical_checksum(asdict(self))

    def projection_checksum(self) -> str:
        return canonical_checksum({
            name: [
                {key: sheet_value(value) for key, value in row.items()}
                for row in self.datasets[name]
            ]
            for name in ("signal_events", "forecast_calendar")
        })

    def publication_checksum(self) -> str:
        return canonical_checksum(self.publication_payload)


@dataclass(frozen=True)
class ExecutionResult:
    operation_id: str
    status: str
    no_op: bool
    manifest_path: str


class ApplyBackend(Protocol):
    def verify_backup(self, backup_id: str, expected_checksum: str) -> bool: ...
    def source_state(self) -> RevisionState: ...
    def approval_state(self, event_id: str) -> ApprovalState: ...
    def snapshot(self) -> ProjectionSnapshot: ...
    def apply_atomic(self, spec: ApplySpec) -> None: ...
    def readback_checksum(self) -> str: ...
    def rollback(self, snapshot: ProjectionSnapshot) -> None: ...


class PublicationBackend(Protocol):
    def current_pointer(self) -> str: ...
    def stage(self, spec: ApplySpec) -> str: ...
    def switch(self, staged_version: str, expected_checksum: str) -> None: ...
    def pointer_checksum(self) -> str: ...
    def restore(self, previous_pointer: str) -> None: ...


class OperationStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def manifest_path(self, operation_id: str) -> Path:
        return self.root / operation_id / "execution_manifest.json"

    def read(self, operation_id: str) -> dict[str, Any] | None:
        path = self.manifest_path(operation_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def write(self, operation_id: str, payload: dict[str, Any]) -> Path:
        path = self.manifest_path(operation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path

    def acquire(self, operation_id: str) -> int:
        lock = self.root / operation_id / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise GateError(f"operation is already claimed: {operation_id}") from exc

    def release(self, operation_id: str, descriptor: int) -> None:
        os.close(descriptor)
        (self.root / operation_id / ".lock").unlink(missing_ok=True)

    def successful_reversal_for(self, operation_id: str) -> str | None:
        if not self.root.is_dir():
            return None
        for path in self.root.glob("*/execution_manifest.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (payload.get("operation_kind") == "REVERSAL"
                    and payload.get("supersedes_operation_id") == operation_id
                    and payload.get("status") == OperationStatus.SUCCESS.value):
                return str(payload.get("operation_id"))
        return None


def validate_spec(
    spec: ApplySpec,
    plan: ReconciliationPlan,
    backend: ApplyBackend,
) -> None:
    required = {
        "operation_id": spec.operation_id,
        "event_id": spec.event_id,
        "backup_id": spec.backup_id,
        "backup_checksum": spec.backup_checksum,
        "expected_source_revision": spec.expected_source_revision,
        "source_checksum": spec.source_checksum,
        "calendar_version": spec.calendar_version,
        "calendar_checksum": spec.calendar_checksum,
        "plan_checksum": spec.plan_checksum,
        "diff_checksum": spec.diff_checksum,
        "approval_version": spec.approval_version,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise GateError("required apply gates missing: " + ", ".join(missing))
    if spec.operation_kind not in {"APPLY", "REVERSAL"}:
        raise GateError(f"unsupported operation kind: {spec.operation_kind}")
    if spec.operation_kind == "REVERSAL" and not spec.supersedes_operation_id:
        raise GateError("reversal must identify superseded operation")
    if not spec.negative_controls_passed or not plan.negative_controls_passed or plan.unresolved_rows:
        raise GateError("negative-control or unresolved-row gate failed")
    if plan.calendar_version != spec.calendar_version or plan.source_checksum != spec.source_checksum:
        raise GateError("calendar/source plan metadata mismatch")
    if plan.checksum() != spec.plan_checksum:
        raise GateError("reconciliation plan checksum mismatch")
    if canonical_checksum([asdict(row) for row in plan.diffs]) != spec.diff_checksum:
        raise GateError("row-level diff checksum mismatch")
    required_schemas = {"signal_events", "forecast_calendar", "historical_validation"}
    if set(spec.datasets) != required_schemas or set(spec.schemas) != required_schemas:
        raise GateError("target dataset schema set mismatch")
    for name in required_schemas:
        expected = tuple(spec.schemas[name])
        if not expected or len(set(expected)) != len(expected) or any(set(row) != set(expected) for row in spec.datasets[name]):
            raise GateError(f"target dataset schema mismatch: {name}")
    publication_meta = spec.publication_payload.get("reconciliation", {})
    expected_meta = {
        "operation_id": spec.operation_id,
        "calendar_version": spec.calendar_version,
        "calendar_checksum": spec.calendar_checksum,
        "source_checksum": spec.source_checksum,
        "plan_checksum": spec.plan_checksum,
        "diff_checksum": spec.diff_checksum,
        "signal_events_checksum": rows_checksum(spec.datasets["signal_events"]),
        "forecast_calendar_checksum": rows_checksum(spec.datasets["forecast_calendar"]),
        "historical_validation_checksum": rows_checksum(spec.datasets["historical_validation"]),
    }
    if any(publication_meta.get(key) != value for key, value in expected_meta.items()):
        raise GateError("publication metadata is inconsistent with apply datasets/gates")
    if not backend.verify_backup(spec.backup_id, spec.backup_checksum):
        raise GateError("immutable backup is inaccessible or failed integrity verification")
    source = backend.source_state()
    if source.revision != spec.expected_source_revision or source.checksum != spec.source_checksum:
        raise GateError("source revision/checksum changed after dry-run")
    if source.calendar_version != spec.calendar_version or source.calendar_checksum != spec.calendar_checksum:
        raise GateError("calendar version/checksum changed after dry-run")
    approval = backend.approval_state(spec.event_id)
    if approval.version != spec.approval_version or approval.status != "APPROVED_STAGING":
        raise GateError("event approval changed after dry-run")


def execute_apply(
    spec: ApplySpec,
    plan: ReconciliationPlan,
    backend: ApplyBackend,
    publication: PublicationBackend,
    operations: OperationStore,
) -> ExecutionResult:
    existing = operations.read(spec.operation_id)
    request_checksum = spec.request_checksum()
    if existing:
        if existing.get("status") == OperationStatus.SUCCESS.value and existing.get("request_checksum") == request_checksum:
            return ExecutionResult(spec.operation_id, OperationStatus.SUCCESS.value, True, str(operations.manifest_path(spec.operation_id)))
        raise GateError(f"operation ID already exists with non-identical or incomplete state: {spec.operation_id}")
    if spec.operation_kind == "REVERSAL":
        superseded = operations.read(spec.supersedes_operation_id)
        if not superseded or superseded.get("status") != OperationStatus.SUCCESS.value:
            raise GateError("reversal target is not a successful operation")
        previous_reversal = operations.successful_reversal_for(spec.supersedes_operation_id)
        if previous_reversal:
            raise GateError(f"operation already reversed by {previous_reversal}")
    descriptor = operations.acquire(spec.operation_id)
    snapshot: ProjectionSnapshot | None = None
    prior_pointer = ""
    projection_applied = False
    try:
        validate_spec(spec, plan, backend)
        snapshot = backend.snapshot()
        prior_pointer = publication.current_pointer()
        staged = publication.stage(spec)
        manifest = {
            "operation_id": spec.operation_id,
            "event_id": spec.event_id,
            "operation_kind": spec.operation_kind,
            "supersedes_operation_id": spec.supersedes_operation_id,
            "status": OperationStatus.APPLYING.value,
            "request_checksum": request_checksum,
            "backup_id": spec.backup_id,
            "backup_checksum": spec.backup_checksum,
            "expected_source_revision": spec.expected_source_revision,
            "source_checksum": spec.source_checksum,
            "calendar_version": spec.calendar_version,
            "calendar_checksum": spec.calendar_checksum,
            "plan_checksum": spec.plan_checksum,
            "diff_checksum": spec.diff_checksum,
            "approval_version": spec.approval_version,
            "negative_controls_passed": spec.negative_controls_passed,
            "signal_events_checksum": rows_checksum(spec.datasets["signal_events"]),
            "forecast_calendar_checksum": rows_checksum(spec.datasets["forecast_calendar"]),
            "historical_validation_checksum": rows_checksum(spec.datasets["historical_validation"]),
            "previous_projection_checksum": snapshot.checksum,
            "previous_publication_pointer": prior_pointer,
            "staged_publication": staged,
        }
        operations.write(spec.operation_id, manifest)
        # Re-evaluate every mutable external gate immediately before the sole
        # visibility-changing Sheets request.  Approval or source state may
        # have changed while the immutable publication was being staged.
        validate_spec(spec, plan, backend)
        backend.apply_atomic(spec)
        projection_applied = True
        actual = backend.readback_checksum()
        if actual != spec.projection_checksum():
            raise ApplyError("projection readback checksum mismatch")
        publication.switch(staged, spec.publication_checksum())
        if publication.pointer_checksum() != spec.publication_checksum():
            raise ApplyError("publication pointer checksum mismatch")
        manifest.update(status=OperationStatus.SUCCESS.value, projection_checksum=actual,
                        publication_checksum=spec.publication_checksum())
        path = operations.write(spec.operation_id, manifest)
        if spec.operation_kind == "REVERSAL":
            original = operations.read(spec.supersedes_operation_id) or {}
            original["superseded_by"] = spec.operation_id
            operations.write(spec.supersedes_operation_id, original)
        return ExecutionResult(spec.operation_id, OperationStatus.SUCCESS.value, False, str(path))
    except Exception as original:
        if snapshot is not None and projection_applied:
            try:
                backend.rollback(snapshot)
                publication.restore(prior_pointer)
                if backend.readback_checksum() != snapshot.checksum:
                    raise RecoveryRequired("rollback projection checksum mismatch")
                payload = operations.read(spec.operation_id) or {"operation_id": spec.operation_id, "request_checksum": request_checksum}
                payload.update(status=OperationStatus.ROLLED_BACK.value, error=f"{type(original).__name__}: {original}")
                operations.write(spec.operation_id, payload)
            except Exception as recovery:
                payload = operations.read(spec.operation_id) or {"operation_id": spec.operation_id, "request_checksum": request_checksum}
                payload.update(status=OperationStatus.RECOVERY_REQUIRED.value,
                               error=f"{type(original).__name__}: {original}",
                               recovery_error=f"{type(recovery).__name__}: {recovery}")
                operations.write(spec.operation_id, payload)
                raise RecoveryRequired(str(recovery)) from original
        raise
    finally:
        operations.release(spec.operation_id, descriptor)


class FilesystemPublicationBackend:
    """Versioned publication compatible with the existing fixed-path reader."""

    def __init__(self, current_path: str | Path, versions_root: str | Path) -> None:
        self.current_path = Path(current_path)
        self.versions_root = Path(versions_root)

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def current_pointer(self) -> str:
        if not self.current_path.is_file():
            return ""
        payload = json.loads(self.current_path.read_text(encoding="utf-8"))
        checksum = canonical_checksum(payload)
        snapshot = self.versions_root / "previous" / f"{checksum}.json"
        if not snapshot.is_file():
            self._atomic_write(snapshot, payload)
        return str(snapshot)

    def stage(self, spec: ApplySpec) -> str:
        target = self.versions_root / spec.operation_id / "work_site_payload.json"
        if target.is_file():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if canonical_checksum(existing) != spec.publication_checksum():
                raise GateError("immutable staged publication collision")
            return str(target)
        self._atomic_write(target, spec.publication_payload)
        return str(target)

    def switch(self, staged_version: str, expected_checksum: str) -> None:
        staged = Path(staged_version)
        payload = json.loads(staged.read_text(encoding="utf-8"))
        if canonical_checksum(payload) != expected_checksum:
            raise GateError("staged publication checksum mismatch")
        self._atomic_write(self.current_path, payload)

    def pointer_checksum(self) -> str:
        if not self.current_path.is_file():
            return canonical_checksum({})
        return canonical_checksum(json.loads(self.current_path.read_text(encoding="utf-8")))

    def restore(self, previous_pointer: str) -> None:
        if not previous_pointer:
            self.current_path.unlink(missing_ok=True)
            return
        payload = json.loads(Path(previous_pointer).read_text(encoding="utf-8"))
        self._atomic_write(self.current_path, payload)
