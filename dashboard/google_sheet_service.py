from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd

SIGNAL_SHEET = "daily_signals"
RUN_SHEET = "run_log"
SPOT_SHEET = "spot_signal_daily"
EVENT_SHEET = "signal_events"
CALENDAR_SHEET = "forecast_calendar"
RUN_AUDIT_SHEET = "run_audit"
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

SPOT_HEADERS = [
    "data_date", "recorded_at_taipei", "family", "trigger_id", "label", "market",
    "institution", "metric", "direction",
    "horizon", "accumulation_days", "reference_window", "raw_buy_amount",
    "raw_sell_amount", "market_turnover", "current_value", "percentile",
    "a_grade_status", "evidence_grade", "evidence_statement", "research_only",
    "data_quality", "quality_flags", "version", "git_commit",
    "research_status", "evidence_scope", "normalization", "normalized_value", "threshold_label",
]
EVENT_HEADERS = [
    "signal_date", "signal_id", "economic_signal_id", "source", "subject", "direction", "horizon",
    "target_date", "matched", "evaluation_status", "research_status", "evidence_grade", "raw_value",
    "normalized_value", "threshold", "historical_mean_return", "historical_median_return",
    "historical_win_rate", "relative_mean_return", "sample_size", "global_fdr", "family_fdr",
    "plain_definition", "market_mechanism", "risks", "research_commit", "research_run",
    "calculation_timestamp", "source_data_date", "run_id", "git_commit",
    "metric", "normalization", "event_origin", "availability_status",
    "evidence_scope",
]
CALENDAR_HEADERS = [
    "target_date", "bullish_count", "bearish_count", "net_vote",
    "active_signals", "run_id", "git_commit", "contributing_event_count",
]
RUN_AUDIT_HEADERS = ["run_id", "run_timestamp", "git_commit", "data_date", "pipeline_status", "manifest_json"]


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
        if first_row == headers[:len(first_row)]:
            worksheet.update([headers], "A1", value_input_option="RAW")
        else:
            raise RuntimeError(f"Google Sheet分頁「{title}」欄位與目前程式規格不同，請勿手動改欄名。")
    return worksheet


def connect_sheet(sheet_id: str, service_account_secret: str) -> tuple[Any, Any]:
    import gspread

    client = gspread.service_account_from_dict(_credential_dict(service_account_secret))
    spreadsheet = client.open_by_key(sheet_id.strip())
    return (
        _worksheet(spreadsheet, SIGNAL_SHEET, SIGNAL_HEADERS),
        _worksheet(spreadsheet, RUN_SHEET, RUN_HEADERS),
    )


def connect_spot_sheet(sheet_id: str, service_account_secret: str) -> Any:
    """Connect the deprecated diagnostic table projected from canonical events."""
    import gspread

    client = gspread.service_account_from_dict(_credential_dict(service_account_secret))
    return _worksheet(client.open_by_key(sheet_id.strip()), SPOT_SHEET, SPOT_HEADERS)


def connect_vnext_sheets(sheet_id: str, service_account_secret: str) -> tuple[Any, Any, Any]:
    import gspread
    client = gspread.service_account_from_dict(_credential_dict(service_account_secret))
    book = client.open_by_key(sheet_id.strip())
    return (
        _worksheet(book, EVENT_SHEET, EVENT_HEADERS),
        _worksheet(book, CALENDAR_SHEET, CALENDAR_HEADERS),
        _worksheet(book, RUN_AUDIT_SHEET, RUN_AUDIT_HEADERS),
    )


def sync_spot_signals(
    spot_sheet: Any,
    report: Any,
    recorded_at: datetime,
    version: str,
    git_commit: str = "",
) -> int:
    """Upsert canonical-registry diagnostics; this sheet is not a forecast source."""
    values = spot_sheet.get_all_values()
    records = [dict(zip(SPOT_HEADERS, row + [""] * (len(SPOT_HEADERS) - len(row)))) for row in values[1:]]
    positions = {
        (str(row.get("data_date", "")), str(row.get("family", "")), str(row.get("trigger_id", "")), str(row.get("reference_window", ""))): index
        for index, row in enumerate(records)
    }
    recorded = recorded_at.isoformat()
    for item in report.evidence:
        incoming = item.as_record(report.data_date, recorded, version, git_commit)
        row = {header: incoming.get(header, "") for header in SPOT_HEADERS}
        key = (report.data_date, item.family, item.trigger_id, str(item.reference_window))
        if key in positions:
            records[positions[key]] = row
        else:
            positions[key] = len(records)
            records.append(row)
    matrix = [SPOT_HEADERS]
    matrix.extend([[_display(row.get(header, "")) for header in SPOT_HEADERS] for row in records])
    spot_sheet.clear()
    spot_sheet.update(matrix, "A1", value_input_option="RAW")
    spot_sheet.freeze(rows=1)
    return len(report.evidence)


def _display(value: Any) -> Any:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def _outcome_updates(records: list[dict[str, Any]], adjusted_open: pd.Series, adjusted_close: pd.Series) -> tuple[list[dict[str, Any]], int]:
    from .outcomes import outcome_for_signal
    adjusted_open = adjusted_open.dropna().sort_index()
    adjusted_close = adjusted_close.dropna().sort_index()
    updated = 0
    for record in records:
        signal_date = str(record.get("data_date", ""))
        if not signal_date:
            continue
        for horizon in HORIZONS:
            column = f"d{horizon}_return"
            value = outcome_for_signal(signal_date, adjusted_open, adjusted_close, horizon)
            if value is not None:
                if str(record.get(column, "")) != str(value):
                    updated += 1
                record[column] = value
            else:
                # Never preserve a legacy C0→Ch value for an immature O1→Ch outcome.
                record[column] = ""
    return records, updated


def sync_daily_signal(signal_sheet: Any, snapshot: dict[str, Any], adjusted_open: pd.Series, adjusted_close: pd.Series) -> SheetSyncResult:
    values = signal_sheet.get_all_values()
    records = [dict(zip(SIGNAL_HEADERS, row + [""] * (len(SIGNAL_HEADERS) - len(row)))) for row in values[1:]]
    records, updated_outcomes = _outcome_updates(records, adjusted_open, adjusted_close)

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


def _upsert_long_table(
    sheet: Any,
    headers: list[str],
    incoming: list[dict[str, Any]],
    keys: tuple[str, ...],
    production_precedence: bool = False,
) -> int:
    values = sheet.get_all_values()
    records = [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in values[1:]]
    positions = {tuple(str(row.get(key, "")) for key in keys): i for i, row in enumerate(records)}
    for row in incoming:
        normalized = {header: row.get(header, "") for header in headers}
        key = tuple(str(normalized.get(name, "")) for name in keys)
        if key in positions:
            previous = records[positions[key]]
            previous_origin = str(previous.get("event_origin", "") or "PRODUCTION")
            incoming_origin = str(normalized.get("event_origin", "") or "PRODUCTION")
            if not (production_precedence and previous_origin == "PRODUCTION" and incoming_origin == "BACKFILL"):
                records[positions[key]] = normalized
        else:
            positions[key] = len(records)
            records.append(normalized)
    matrix = [headers] + [[_display(row.get(header, "")) for header in headers] for row in records]
    # Cumulative tables only grow or update existing keys. Avoid clear()+rewrite,
    # which could destroy the ledger if the following network write failed.
    sheet.update(matrix, "A1", value_input_option="RAW"); sheet.freeze(rows=1)
    return len(incoming)


def sync_signal_events(sheet: Any, events: tuple[Any, ...], run_id: str, git_commit: str) -> int:
    rows = []
    for event in events:
        row = event.as_dict(); row.update(run_id=run_id, git_commit=git_commit); rows.append(row)
    return _upsert_long_table(
        sheet, EVENT_HEADERS, rows, ("signal_date", "signal_id", "horizon"),
        production_precedence=True,
    )


def load_signal_events(sheet: Any) -> tuple[Any, ...]:
    from .signal_engine import events_from_frame

    values = sheet.get_all_values()
    if len(values) <= 1:
        return ()
    rows = [dict(zip(EVENT_HEADERS, row + [""] * (len(EVENT_HEADERS) - len(row)))) for row in values[1:]]
    return events_from_frame(pd.DataFrame(rows))


def sync_forecast_calendar(sheet: Any, calendar: pd.DataFrame, run_id: str, git_commit: str) -> int:
    rows = []
    for row in calendar.to_dict("records"):
        row.update(run_id=run_id, git_commit=git_commit); rows.append(row)
    return _upsert_long_table(sheet, CALENDAR_HEADERS, rows, ("target_date",))


def append_run_audit(sheet: Any, manifest: Any) -> None:
    sheet.append_row([
        manifest.run_id, manifest.run_timestamp, manifest.git_commit, manifest.actual_data_date or "",
        manifest.overall_status, json.dumps(asdict(manifest), ensure_ascii=False),
    ], value_input_option="RAW")


def append_run_log(run_sheet: Any, run_at: datetime, status: str, data_date: str, message: str, version: str) -> None:
    run_sheet.append_row(
        [run_at.isoformat(), status, data_date, message[:1000], version],
        value_input_option="RAW",
    )
