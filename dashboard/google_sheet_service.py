from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

SIGNAL_SHEET = "daily_signals"
RUN_SHEET = "run_log"
HORIZONS = (1, 3, 5, 10, 20)

SIGNAL_HEADERS = [
    "data_date",
    "recorded_at_taipei",
    "data_status",
    "overall_state",
    "headline",
    "reference_action",
    "breadth_score",
    "down_ratio",
    "coverage_ratio",
    "foreign_direction_score",
    "foreign_oi_ratio",
    "foreign_oi_change_ratio",
    "foreign_long_change_ratio",
    "foreign_short_change_ratio",
    "foreign_net_oi",
    "0050_close",
    "price_source",
    "d1_return",
    "d3_return",
    "d5_return",
    "d10_return",
    "d20_return",
    "version",
]

RUN_HEADERS = ["run_at_taipei", "status", "data_date", "message", "version"]


@dataclass(frozen=True)
class SheetSyncResult:
    action: str
    updated_outcomes: int


def _credential_dict(secret: str) -> dict[str, Any]:
    value = secret.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return json.loads(base64.b64decode(value).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON不是有效JSON或Base64 JSON。") from exc


def _worksheet(spreadsheet: Any, title: str, headers: list[str]) -> Any:
    import gspread

    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(26, len(headers)))
    first_row = worksheet.row_values(1)
    if not first_row:
        worksheet.append_row(headers, value_input_option="RAW")
    elif first_row != headers:
        raise RuntimeError(f"Google Sheet分頁「{title}」欄位與v1.5.0規格不同，請勿手動改欄名。")
    return worksheet


def connect_sheet(sheet_id: str, service_account_secret: str) -> tuple[Any, Any]:
    import gspread

    client = gspread.service_account_from_dict(_credential_dict(service_account_secret))
    spreadsheet = client.open_by_key(sheet_id.strip())
    return (
        _worksheet(spreadsheet, SIGNAL_SHEET, SIGNAL_HEADERS),
        _worksheet(spreadsheet, RUN_SHEET, RUN_HEADERS),
    )


def _display(value: Any) -> Any:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def _outcome_updates(records: list[dict[str, Any]], close: pd.Series) -> tuple[list[dict[str, Any]], int]:
    close = close.dropna().sort_index()
    positions = {pd.Timestamp(index).date().isoformat(): i for i, index in enumerate(close.index)}
    updated = 0
    for record in records:
        key = str(record.get("data_date", ""))
        position = positions.get(key)
        if position is None:
            continue
        base = float(close.iloc[position])
        for horizon in HORIZONS:
            column = f"d{horizon}_return"
            if str(record.get(column, "")).strip() or position + horizon >= len(close):
                continue
            record[column] = float(close.iloc[position + horizon]) / base - 1
            updated += 1
    return records, updated


def sync_daily_signal(signal_sheet: Any, snapshot: dict[str, Any], close: pd.Series) -> SheetSyncResult:
    values = signal_sheet.get_all_values()
    records = [dict(zip(SIGNAL_HEADERS, row + [""] * (len(SIGNAL_HEADERS) - len(row)))) for row in values[1:]]
    records, updated_outcomes = _outcome_updates(records, close)

    data_date = str(snapshot["data_date"])
    existing = next((record for record in records if record.get("data_date") == data_date), None)
    action = "updated"
    if existing is None:
        existing = {header: "" for header in SIGNAL_HEADERS}
        records.append(existing)
        action = "inserted"
    # 重跑同一天時更新衍生指標，但保留已填入的未來報酬。
    for key, value in snapshot.items():
        if key in SIGNAL_HEADERS and not key.endswith("_return"):
            existing[key] = value

    matrix = [SIGNAL_HEADERS]
    for record in sorted(records, key=lambda row: row.get("data_date", "")):
        matrix.append([_display(record.get(header, "")) for header in SIGNAL_HEADERS])
    signal_sheet.clear()
    signal_sheet.update(matrix, "A1", value_input_option="RAW")
    signal_sheet.freeze(rows=1)
    return SheetSyncResult(action, updated_outcomes)


def append_run_log(run_sheet: Any, run_at: datetime, status: str, data_date: str, message: str, version: str) -> None:
    run_sheet.append_row(
        [run_at.isoformat(), status, data_date, message[:1000], version],
        value_input_option="RAW",
    )
