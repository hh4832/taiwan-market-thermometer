"""Optimistic-claim worker for approved Google Sheet review events."""
from __future__ import annotations

from dataclasses import asdict
import os
import uuid
from typing import Any

from .google_sheet_service import (
    CALENDAR_OVERRIDE_HEADERS,
    PIPELINE_EXCEPTION_HEADERS,
    connect_review_sheets,
)
from .pipeline_exceptions import claim_review_event
from .review_processor import process_review_row


def _records(sheet: Any, headers: list[str]) -> list[dict[str, Any]]:
    return [
        dict(zip(headers, row + [""] * (len(headers) - len(row))))
        for row in sheet.get_all_values()[1:]
    ]


def _write_record(sheet: Any, headers: list[str], row_number: int, record: dict[str, Any]) -> None:
    values = [[record.get(name, "") for name in headers]]
    sheet.update(values, f"A{row_number}", value_input_option="RAW")


def _upsert_override(sheet: Any, incoming: dict[str, Any]) -> None:
    rows = _records(sheet, CALENDAR_OVERRIDE_HEADERS)
    position = next((i for i, row in enumerate(rows) if row.get("event_id") == incoming["event_id"]), None)
    row_number = (position + 2) if position is not None else (len(rows) + 2)
    _write_record(sheet, CALENDAR_OVERRIDE_HEADERS, row_number, incoming)


def process_pending_reviews(exception_sheet: Any, override_sheet: Any, worker_id: str) -> int:
    processed = 0
    for index, row in enumerate(_records(exception_sheet, PIPELINE_EXCEPTION_HEADERS), start=2):
        decision = str(row.get("review_decision", "")).strip().upper()
        confirmed = str(row.get("review_confirm", "")).strip().upper() in {"TRUE", "1", "YES"}
        if not confirmed or decision == "":
            continue
        if str(row.get("exception_type", "")).strip().upper() == "CALENDAR_CONFLICT":
            row["processing_status"] = "ON_HOLD"
            _write_record(exception_sheet, PIPELINE_EXCEPTION_HEADERS, index, row)
            processed += 1
            continue
        status = str(row.get("processing_status", "PENDING_REVIEW"))
        if decision in {"INVESTIGATE", "CANCEL"}:
            result = process_review_row(row)
            row["processing_status"] = result.processing_status
            _write_record(exception_sheet, PIPELINE_EXCEPTION_HEADERS, index, row)
            processed += 1
            continue
        if status not in {"PENDING_REVIEW", "RETRY_PENDING"}:
            continue
        claimed = claim_review_event(row, worker_id)
        _write_record(exception_sheet, PIPELINE_EXCEPTION_HEADERS, index, claimed)
        # Compare-and-verify the claim.  Google Sheets has no transaction; if a
        # competing writer changed the row, this worker stops before side effects.
        current = _records(exception_sheet, PIPELINE_EXCEPTION_HEADERS)[index - 2]
        if current.get("processing_status") != f"CLAIMED:{worker_id}":
            raise RuntimeError(f"lost review-event claim: {row.get('event_id')}")
        result = process_review_row(row)
        if result.override is not None:
            _upsert_override(override_sheet, result.override)
        claimed["processing_status"] = result.processing_status
        _write_record(exception_sheet, PIPELINE_EXCEPTION_HEADERS, index, claimed)
        processed += 1
    return processed


def run() -> int:
    if os.getenv("CALENDAR_REVIEW_ENABLED", "").lower() != "true":
        print("calendar review disabled; no Sheet changes")
        return 0
    exception_sheet, override_sheet = connect_review_sheets(
        os.environ["GOOGLE_SHEET_ID"], os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    )
    worker = f"{os.getenv('GITHUB_RUN_ID', 'local')}-{uuid.uuid4().hex[:8]}"
    print(f"processed={process_pending_reviews(exception_sheet, override_sheet, worker)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
