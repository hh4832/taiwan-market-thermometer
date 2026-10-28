from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import threading

import pytest

from dashboard.calendar_reconciliation import ReconciliationPlan
from dashboard.google_sheet_service import CALENDAR_HEADERS, sync_forecast_calendar
from dashboard.production_apply import (
    ApprovalState,
    ApplyError,
    ApplySpec,
    FilesystemPublicationBackend,
    GateError,
    OperationStore,
    OperationStatus,
    ProjectionSnapshot,
    RecoveryRequired,
    RevisionState,
    canonical_checksum,
    execute_apply,
    rows_checksum,
)


SCHEMAS = {
    "signal_events": ("signal_date", "signal_id", "horizon", "target_date"),
    "forecast_calendar": ("target_date", "net_vote"),
    "historical_validation": ("signal_date", "signal_id", "horizon", "target_date", "maturity"),
}


def plan(source="source", calendar="cal-v2"):
    return ReconciliationPlan(calendar, source, 1, 0, 1, 0, True, ())


def make_spec(p=None, **changes):
    p = p or plan()
    datasets = {
        "signal_events": ({"signal_date": "2026-10-22", "signal_id": "s", "horizon": 1, "target_date": "2026-10-26"},),
        "forecast_calendar": ({"target_date": "2026-10-26", "net_vote": 1},),
        "historical_validation": ({"signal_date": "2026-10-22", "signal_id": "s", "horizon": 1, "target_date": "2026-10-26", "maturity": "PENDING"},),
    }
    base = dict(
        operation_id="op-1", event_id="event-1", operation_kind="APPLY",
        backup_id="backup-1", backup_checksum="backup-checksum",
        expected_source_revision="rev-1", source_checksum=p.source_checksum,
        calendar_version=p.calendar_version, calendar_checksum="calendar-checksum",
        plan_checksum=p.checksum(),
        diff_checksum=canonical_checksum([asdict(row) for row in p.diffs]),
        approval_version="approval-v1", negative_controls_passed=True,
        schemas=SCHEMAS, datasets=datasets, publication_payload={},
    )
    base.update(changes)
    meta = {
        "operation_id": base["operation_id"], "calendar_version": base["calendar_version"],
        "calendar_checksum": base["calendar_checksum"], "source_checksum": base["source_checksum"],
        "plan_checksum": base["plan_checksum"], "diff_checksum": base["diff_checksum"],
        "signal_events_checksum": rows_checksum(base["datasets"]["signal_events"]),
        "forecast_calendar_checksum": rows_checksum(base["datasets"]["forecast_calendar"]),
        "historical_validation_checksum": rows_checksum(base["datasets"]["historical_validation"]),
    }
    if "publication_payload" not in changes:
        base["publication_payload"] = {"schema_version": "1.1", "reconciliation": meta}
    return ApplySpec(**base)


class Backend:
    def __init__(self):
        self.backup_ok = True
        self.source = RevisionState("rev-1", "source", "cal-v2", "calendar-checksum")
        self.approval = ApprovalState("approval-v1", "APPROVED_STAGING")
        self.payload = {"signal_events": [{"old": "event"}], "forecast_calendar": [{"old": "calendar"}]}
        self.fail_apply = False; self.bad_readbacks = 0; self.fail_rollback = False
        self.applies = 0

    def verify_backup(self, *_): return self.backup_ok
    def source_state(self): return self.source
    def approval_state(self, _): return self.approval
    def snapshot(self): return ProjectionSnapshot(canonical_checksum(self.payload), json.loads(json.dumps(self.payload)))
    def apply_atomic(self, spec):
        self.applies += 1
        if self.fail_apply: raise ApplyError("atomic apply failed")
        self.payload = {
            "signal_events": [{k: str(v) for k, v in row.items()} for row in spec.datasets["signal_events"]],
            "forecast_calendar": [{k: str(v) for k, v in row.items()} for row in spec.datasets["forecast_calendar"]],
        }
    def readback_checksum(self):
        if self.bad_readbacks:
            self.bad_readbacks -= 1
            return "bad"
        return canonical_checksum(self.payload)
    def rollback(self, snapshot):
        if self.fail_rollback: raise ApplyError("rollback failed")
        self.payload = snapshot.payload


class Publication:
    def __init__(self):
        self.pointer = "old"; self.checksum = "old-checksum"
        self.fail_stage = False; self.fail_switch = False; self.on_stage = None
    def current_pointer(self): return self.pointer
    def stage(self, spec):
        if self.fail_stage: raise ApplyError("downstream rebuild/stage failed")
        if self.on_stage: self.on_stage()
        self.staged = spec.publication_checksum(); return "staged"
    def switch(self, _token, expected):
        if self.fail_switch: raise ApplyError("switch failed")
        self.pointer = "staged"; self.checksum = expected
    def pointer_checksum(self): return self.checksum
    def restore(self, previous): self.pointer = previous; self.checksum = "old-checksum"


@pytest.mark.parametrize("field", ["backup_id", "backup_checksum"])
def test_backup_gate_missing(field, tmp_path, field_value=""):
    spec = make_spec(**{field: field_value})
    with pytest.raises(GateError, match="missing"):
        execute_apply(spec, plan(), Backend(), Publication(), OperationStore(tmp_path))


def test_backup_inaccessible(tmp_path):
    backend = Backend(); backend.backup_ok = False
    with pytest.raises(GateError, match="backup"):
        execute_apply(make_spec(), plan(), backend, Publication(), OperationStore(tmp_path))


@pytest.mark.parametrize("mutation,match", [
    ({"source_checksum": "changed"}, "source"),
    ({"calendar_version": "changed"}, "calendar"),
    ({"plan_checksum": "changed"}, "plan checksum"),
    ({"diff_checksum": "changed"}, "diff checksum"),
])
def test_checksum_and_version_gates(mutation, match, tmp_path):
    with pytest.raises(GateError, match=match):
        execute_apply(make_spec(**mutation), plan(), Backend(), Publication(), OperationStore(tmp_path))


def test_approval_revoked_after_dry_run(tmp_path):
    backend = Backend(); backend.approval = ApprovalState("approval-v2", "CANCELLED")
    with pytest.raises(GateError, match="approval"):
        execute_apply(make_spec(), plan(), backend, Publication(), OperationStore(tmp_path))


def test_approval_revoked_while_staging_is_rechecked_before_write(tmp_path):
    backend = Backend(); publication = Publication()
    publication.on_stage = lambda: setattr(backend, "approval", ApprovalState("approval-v2", "CANCELLED"))
    with pytest.raises(GateError, match="approval"):
        execute_apply(make_spec(), plan(), backend, publication, OperationStore(tmp_path))
    assert backend.applies == 0


def test_live_calendar_checksum_changed_after_dry_run(tmp_path):
    backend = Backend()
    backend.source = RevisionState("rev-1", "source", "cal-v2", "changed")
    with pytest.raises(GateError, match="calendar version/checksum"):
        execute_apply(make_spec(), plan(), backend, Publication(), OperationStore(tmp_path))


def test_target_schema_mismatch_blocks_before_write(tmp_path):
    spec = make_spec()
    broken = dict(spec.datasets)
    broken["forecast_calendar"] = ({"target_date": "2026-10-26"},)
    spec = make_spec(datasets=broken)
    with pytest.raises(GateError, match="schema mismatch"):
        execute_apply(spec, plan(), Backend(), Publication(), OperationStore(tmp_path))


def test_readback_mismatch_rolls_back_and_never_switches_publication(tmp_path):
    backend = Backend(); before = json.loads(json.dumps(backend.payload)); backend.bad_readbacks = 1
    publication = Publication()
    with pytest.raises(ApplyError, match="readback"):
        execute_apply(make_spec(), plan(), backend, publication, OperationStore(tmp_path))
    assert backend.payload == before and publication.pointer == "old"
    assert OperationStore(tmp_path).read("op-1")["status"] == OperationStatus.ROLLED_BACK.value


def test_pointer_switch_failure_rolls_back_projection(tmp_path):
    backend = Backend(); before = json.loads(json.dumps(backend.payload))
    publication = Publication(); publication.fail_switch = True
    with pytest.raises(ApplyError, match="switch"):
        execute_apply(make_spec(), plan(), backend, publication, OperationStore(tmp_path))
    assert backend.payload == before and publication.pointer == "old"


def test_downstream_rebuild_failure_happens_before_any_sheet_write(tmp_path):
    backend = Backend(); before = json.loads(json.dumps(backend.payload))
    publication = Publication(); publication.fail_stage = True
    with pytest.raises(ApplyError, match="downstream"):
        execute_apply(make_spec(), plan(), backend, publication, OperationStore(tmp_path))
    assert backend.applies == 0 and backend.payload == before


def test_rollback_failure_marks_recovery_required(tmp_path):
    backend = Backend(); backend.bad_readbacks = 1; backend.fail_rollback = True
    with pytest.raises(RecoveryRequired):
        execute_apply(make_spec(), plan(), backend, Publication(), OperationStore(tmp_path))
    assert OperationStore(tmp_path).read("op-1")["status"] == OperationStatus.RECOVERY_REQUIRED.value

    # The next Actions attempt remains blocked; it cannot silently retry over
    # an unresolved recovery state.
    with pytest.raises(GateError, match="incomplete state"):
        execute_apply(make_spec(), plan(), backend, Publication(), OperationStore(tmp_path))


def test_success_is_idempotent_no_op(tmp_path):
    backend = Backend(); publication = Publication(); store = OperationStore(tmp_path)
    first = execute_apply(make_spec(), plan(), backend, publication, store)
    second = execute_apply(make_spec(), plan(), backend, publication, store)
    assert not first.no_op and second.no_op and backend.applies == 1


def test_successful_reversal_and_duplicate_reversal_blocked(tmp_path):
    backend = Backend(); publication = Publication(); store = OperationStore(tmp_path)
    execute_apply(make_spec(), plan(), backend, publication, store)
    reversal = make_spec(operation_id="reverse-1", operation_kind="REVERSAL", supersedes_operation_id="op-1")
    execute_apply(reversal, plan(), backend, publication, store)
    assert store.read("op-1")["superseded_by"] == "reverse-1"
    another = make_spec(operation_id="reverse-2", operation_kind="REVERSAL", supersedes_operation_id="op-1")
    with pytest.raises(GateError, match="already reversed"):
        execute_apply(another, plan(), backend, publication, store)


def test_two_workers_cannot_claim_same_operation(tmp_path):
    store = OperationStore(tmp_path); descriptor = store.acquire("op-1")
    try:
        with pytest.raises(GateError, match="claimed"):
            store.acquire("op-1")
    finally:
        store.release("op-1", descriptor)


def test_filesystem_publication_keeps_previous_payload_on_bad_stage(tmp_path):
    current = tmp_path / "public.json"; current.write_text('{"old": true}', encoding="utf-8")
    publication = FilesystemPublicationBackend(current, tmp_path / "versions")
    previous = publication.current_pointer()
    spec = make_spec()
    staged = publication.stage(spec)
    with pytest.raises(GateError):
        publication.switch(staged, "wrong")
    assert json.loads(current.read_text()) == {"old": True}
    publication.restore(previous)


class AtomicSpreadsheet:
    def __init__(self, fail=False): self.calls = []; self.fail = fail
    def batch_update(self, body):
        self.calls.append(body)
        if self.fail:
            raise RuntimeError("injected atomic request failure")


class AtomicSheet:
    def __init__(self, fail=False):
        self.id = 7; self.spreadsheet = AtomicSpreadsheet(fail)
        self.values = [CALENDAR_HEADERS, ["old"] * len(CALENDAR_HEADERS), ["tail"] * len(CALENDAR_HEADERS)]
    def get_all_values(self): return self.values
    def freeze(self, **_): pass


def test_forecast_replace_uses_one_atomic_request_and_clears_tail():
    import pandas as pd
    sheet = AtomicSheet()
    frame = pd.DataFrame([{"target_date": "2026-10-26", "bullish_count": 1, "bearish_count": 0,
                           "net_vote": 1, "active_signals": "s", "contributing_event_count": 1}])
    sync_forecast_calendar(sheet, frame, "run", "sha")
    assert len(sheet.spreadsheet.calls) == 1
    request = sheet.spreadsheet.calls[0]["requests"][0]["updateCells"]
    assert request["range"]["endRowIndex"] == 3
    assert request["fields"] == "userEnteredValue"


def test_forecast_atomic_request_failure_leaves_previous_projection_visible():
    import pandas as pd
    sheet = AtomicSheet(fail=True); before = json.loads(json.dumps(sheet.values))
    frame = pd.DataFrame([{"target_date": "2026-10-26", "bullish_count": 1, "bearish_count": 0,
                           "net_vote": 1, "active_signals": "s", "contributing_event_count": 1}])
    with pytest.raises(RuntimeError, match="atomic request"):
        sync_forecast_calendar(sheet, frame, "run", "sha")
    assert sheet.values == before
