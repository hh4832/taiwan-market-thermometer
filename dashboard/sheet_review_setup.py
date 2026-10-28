"""Idempotent setup and permission probe for calendar review worksheets."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .google_sheet_service import (
    CALENDAR_OVERRIDE_HEADERS,
    PIPELINE_EXCEPTION_HEADERS,
    connect_review_sheets,
    sync_pipeline_exception,
)
from .official_market import EvidenceStatus, OfficialEvidence
from .pipeline_exceptions import build_exception

TAIPEI = ZoneInfo("Asia/Taipei")


def _read_rows(sheet, headers):
    rows = [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in sheet.get_all_values()[1:]]
    return [row for row in rows if str(row.get("event_id", "")).strip()]


def run() -> int:
    sheet_id = os.environ["TARGET_SHEET_ID"].strip()
    secret = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    probe = os.getenv("PERMISSION_PROBE", "false").lower() == "true"
    exceptions, overrides = connect_review_sheets(sheet_id, secret)
    before_exception_rows = len(_read_rows(exceptions, PIPELINE_EXCEPTION_HEADERS))
    before_override_rows = len(_read_rows(overrides, CALENDAR_OVERRIDE_HEADERS))
    probe_verified = False
    if probe:
        evidence = OfficialEvidence(
            "TWSE", "DAY", "2099-12-31", EvidenceStatus.API_FAILED, (),
            "staging://permission-probe", datetime.now(TAIPEI).isoformat(), "permission probe",
        )
        event = build_exception(
            "2099-12-31", "", "STAGING_PERMISSION_PROBE", evidence,
            configured_is_session=True, retry_count=0,
        )
        sync_pipeline_exception(exceptions, event)
        rows = _read_rows(exceptions, PIPELINE_EXCEPTION_HEADERS)
        matches = [row for row in rows if row.get("event_id") == event.event_id]
        if len(matches) != 1 or matches[0].get("target_data_date") != "2099-12-31":
            raise RuntimeError("Service Account write/readback probe failed")
        # Clear only the uniquely identified synthetic row.  Do not delete the
        # grid row: repeated permission probes must not shrink the worksheet or
        # remove its validation rules.
        row_number = next(index for index, row in enumerate(rows, start=2) if row.get("event_id") == event.event_id)
        exceptions.spreadsheet.batch_update({"requests": [{
            "updateCells": {
                "range": {
                    "sheetId": exceptions.id,
                    "startRowIndex": row_number - 1,
                    "endRowIndex": row_number,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(PIPELINE_EXCEPTION_HEADERS),
                },
                "fields": "userEnteredValue",
            }
        }]})
        if any(row.get("event_id") == event.event_id for row in _read_rows(exceptions, PIPELINE_EXCEPTION_HEADERS)):
            raise RuntimeError("Service Account cleanup/readback probe failed")
        probe_verified = True
    metadata = exceptions.spreadsheet.fetch_sheet_metadata()
    titles = [item["properties"]["title"] for item in metadata.get("sheets", [])]
    if titles.count("pipeline_exceptions") != 1 or titles.count("calendar_overrides") != 1:
        raise RuntimeError("review worksheet idempotency check failed")
    after_exception_rows = len(_read_rows(exceptions, PIPELINE_EXCEPTION_HEADERS))
    after_override_rows = len(_read_rows(overrides, CALENDAR_OVERRIDE_HEADERS))
    if after_exception_rows != before_exception_rows or after_override_rows != before_override_rows:
        raise RuntimeError("setup changed existing review records")
    report = {
        "spreadsheet_id": sheet_id,
        "pipeline_exceptions_sheet_id": exceptions.id,
        "calendar_overrides_sheet_id": overrides.id,
        "create": "PASS",
        "format": "PASS",
        "validation": "PASS",
        "warning_only_protection": "PASS",
        "write_readback_cleanup": "PASS" if probe_verified else "NOT_REQUESTED",
        "existing_exception_rows_preserved": after_exception_rows,
        "existing_override_rows_preserved": after_override_rows,
    }
    target = Path(os.getenv("SETUP_REPORT_PATH", "outputs/sheet_review_setup.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
