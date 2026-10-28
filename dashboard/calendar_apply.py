"""Production-capable calendar apply entrypoint with no bypass switches.

The command consumes one immutable JSON bundle produced by the reconciliation
stage.  It is intentionally not scheduled; an approved deployment workflow
must supply the bundle and credentials after staging acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import gspread

from .calendar_reconciliation import ReconciliationPlan, TargetDiff
from .google_sheet_service import (
    CALENDAR_HEADERS,
    EVENT_HEADERS,
    PIPELINE_EXCEPTION_HEADERS,
    AtomicGoogleSheetsBackend,
    _credential_dict,
)
from .production_apply import (
    ApprovalState,
    ApplySpec,
    FilesystemPublicationBackend,
    OperationStore,
    RevisionState,
    canonical_checksum,
    execute_apply,
)
from .trading_calendar import HOLIDAY_FILE, TradingCalendar


def _records(sheet: Any, headers: list[str]) -> list[dict[str, str]]:
    return [
        dict(zip(headers, row + [""] * (len(headers) - len(row))))
        for row in sheet.get_all_values()[1:]
        if any(str(value).strip() for value in row)
    ]


def workbook_projection_checksum(book: Any) -> str:
    return canonical_checksum({
        "signal_events": _records(book.worksheet("signal_events"), EVENT_HEADERS),
        "forecast_calendar": _records(book.worksheet("forecast_calendar"), CALENDAR_HEADERS),
    })


def approval_reader(book: Any, event_id: str) -> ApprovalState:
    rows = _records(book.worksheet("pipeline_exceptions"), PIPELINE_EXCEPTION_HEADERS)
    matches = [row for row in rows if row.get("event_id") == event_id]
    if len(matches) != 1:
        return ApprovalState("", "MISSING")
    row = matches[0]
    if str(row.get("exception_type", "")).upper() == "CALENDAR_CONFLICT":
        return ApprovalState(canonical_checksum(row), "BLOCKED_CALENDAR_CONFLICT")
    confirmed = str(row.get("review_confirm", "")).upper() in {"TRUE", "1", "YES"}
    status = str(row.get("processing_status", "")) if confirmed else "NOT_CONFIRMED"
    return ApprovalState(canonical_checksum(row), status)


def source_reader(book: Any) -> RevisionState:
    event_rows = _records(book.worksheet("signal_events"), EVENT_HEADERS)
    calendar_rows = _records(book.worksheet("forecast_calendar"), CALENDAR_HEADERS)
    revisions = sorted({row.get("run_id", "") for row in event_rows if row.get("run_id")})
    calendar = TradingCalendar.configured()
    return RevisionState(
        revisions[-1] if revisions else "",
        canonical_checksum({"signal_events": event_rows, "forecast_calendar": calendar_rows}),
        calendar.version,
        hashlib.sha256(Path(HOLIDAY_FILE).read_bytes()).hexdigest(),
    )


def load_bundle(path: str | Path) -> tuple[ApplySpec, ReconciliationPlan]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    plan_payload = dict(payload["plan"])
    plan_payload["diffs"] = tuple(TargetDiff(**row) for row in plan_payload.get("diffs", []))
    plan = ReconciliationPlan(**plan_payload)
    spec_payload = dict(payload["spec"])
    spec_payload["schemas"] = {name: tuple(value) for name, value in spec_payload["schemas"].items()}
    spec_payload["datasets"] = {
        name: tuple(dict(row) for row in rows) for name, rows in spec_payload["datasets"].items()
    }
    return ApplySpec(**spec_payload), plan


def run() -> int:
    bundle_path = os.environ.get("PRODUCTION_APPLY_BUNDLE", "").strip()
    if not bundle_path:
        raise RuntimeError("PRODUCTION_APPLY_BUNDLE is required")
    sheet_id = os.environ["GOOGLE_SHEET_ID"].strip()
    credentials = _credential_dict(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    client = gspread.service_account_from_dict(credentials)
    book = client.open_by_key(sheet_id)
    spec, plan = load_bundle(bundle_path)
    event_sheet = book.worksheet("signal_events")
    calendar_sheet = book.worksheet("forecast_calendar")

    def verify_backup(backup_id: str, expected_checksum: str) -> bool:
        try:
            backup = client.open_by_key(backup_id)
            return workbook_projection_checksum(backup) == expected_checksum
        except Exception:
            return False

    backend = AtomicGoogleSheetsBackend(
        book, event_sheet, calendar_sheet,
        verify_backup,
        lambda: source_reader(book),
        lambda event_id: approval_reader(book, event_id),
    )
    publication = FilesystemPublicationBackend(
        os.getenv("WORK_SITE_PAYLOAD", "public/data/work_site_payload.json"),
        os.getenv("PUBLICATION_VERSIONS_ROOT", "outputs/publication_versions"),
    )
    result = execute_apply(
        spec, plan, backend, publication,
        OperationStore(os.getenv("CALENDAR_OPERATIONS_ROOT", "outputs/calendar_operations")),
    )
    print(json.dumps(result.__dict__, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
