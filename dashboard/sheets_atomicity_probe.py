"""Staging-only verification of Google Sheets batchUpdate atomic semantics."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import gspread

from .google_sheet_service import _credential_dict


def _write_request(sheet_id: int, value: str) -> dict:
    return {"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "rows": [{"values": [{"userEnteredValue": {"stringValue": value}}]}],
        "fields": "userEnteredValue",
    }}


def run() -> int:
    if os.getenv("ATOMICITY_PROBE_STAGING_ONLY", "") != "true":
        raise RuntimeError("atomicity probe is staging-only")
    client = gspread.service_account_from_dict(_credential_dict(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    book = client.open_by_key(os.environ["TARGET_SHEET_ID"])
    token = uuid.uuid4().hex[:10]
    first = second = None
    report = {"failed_batch_left_no_partial_write": False, "valid_cross_sheet_batch": False,
              "temporary_tabs_removed": False}
    try:
        first = book.add_worksheet(title=f"_atomic_probe_a_{token}", rows=5, cols=5)
        second = book.add_worksheet(title=f"_atomic_probe_b_{token}", rows=5, cols=5)
        first.update([["sentinel"]], "A1", value_input_option="RAW")
        try:
            book.batch_update({"requests": [
                _write_request(first.id, "must-not-commit"),
                _write_request(-999999999, "invalid-sheet"),
            ]})
        except gspread.exceptions.APIError:
            pass
        else:
            raise RuntimeError("invalid batch unexpectedly succeeded")
        if first.acell("A1").value != "sentinel":
            raise RuntimeError("failed batch committed a partial write")
        report["failed_batch_left_no_partial_write"] = True

        book.batch_update({"requests": [
            _write_request(first.id, "committed-a"),
            _write_request(second.id, "committed-b"),
        ]})
        if first.acell("A1").value != "committed-a" or second.acell("A1").value != "committed-b":
            raise RuntimeError("valid cross-sheet batch readback mismatch")
        report["valid_cross_sheet_batch"] = True
    finally:
        for worksheet in (first, second):
            if worksheet is None:
                continue
            try:
                book.del_worksheet(worksheet)
            except Exception:
                report["temporary_tabs_removed"] = False
                raise
        report["temporary_tabs_removed"] = True
        target = Path(os.getenv("ATOMICITY_REPORT_PATH", "outputs/sheets_atomicity_probe.json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
