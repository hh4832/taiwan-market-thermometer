from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.archive_service import archive_run
from dashboard.canonical_pipeline import build_canonical_events, canonical_metrics
from dashboard.daily_email import TAIPEI, VERSION, build_vnext_report, configure_logging
from dashboard.data_service import load_live_0050_prices, load_live_breadth, load_live_futures
from dashboard.dashboard_source import CURRENT_RUN_DIR, write_current_run_artifacts
from dashboard.email_service import EmailSettings, send_gmail, simple_html
from dashboard.forecast_calendar import aggregate_events_by_target_date, build_forecast_calendar
from dashboard.finlab_auth import authenticate_finlab_headless
from dashboard.google_sheet_service import append_run_log, connect_sheet, sync_daily_signal
from dashboard.google_sheet_service import connect_spot_sheet, sync_spot_signals
from dashboard.google_sheet_service import (
    append_run_audit,
    connect_vnext_sheets,
    load_signal_events,
    sync_forecast_calendar,
    sync_signal_events,
)
from dashboard.observability import PipelineProgress
from dashboard.run_manifest import RunManifest
from dashboard.signal_engine import events_frame, production_evaluation, production_events
from dashboard.signal_ledger import build_signal_ledger
from dashboard.spot_flow_service import load_live_spot_flow
from dashboard.trading_calendar import expected_latest_trading_date, extend_future_sessions


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少GitHub Secret：{name}")
    return value


def current_git_commit() -> str:
    configured = os.getenv("GITHUB_SHA", "").strip()
    if configured:
        return configured
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return "unavailable"


def build_snapshot(
    breadth: pd.DataFrame,
    futures: pd.DataFrame,
    close: pd.Series,
    now: datetime,
) -> dict[str, object]:
    breadth = breadth.sort_index()
    futures = futures.dropna(subset=["foreign_direction_score"]).sort_index()
    brow = breadth.iloc[-1]
    frow = futures.iloc[-1]
    bdate = pd.Timestamp(breadth.index[-1]).date()
    fdate = pd.Timestamp(futures.index[-1]).date()
    cdate = pd.Timestamp(close.index[-1]).date()
    if not (bdate == fdate == cdate):
        raise RuntimeError(f"資料日期不一致：breadth={bdate}, futures={fdate}, 0050={cdate}")
    conclusion = build_conclusion(
        float(brow.get("breadth_rebound_score", float("nan"))),
        float(frow["foreign_direction_score"]),
        bdate,
        fdate,
        float(brow.get("coverage_ratio", float("nan"))),
    )
    today = now.astimezone(TAIPEI).date()
    return {
        "data_date": bdate.isoformat(),
        "recorded_at_taipei": now.astimezone(TAIPEI).isoformat(),
        "data_status": "資料完成" if bdate == today else "資料日期未齊",
        "overall_state": conclusion.overall_state,
        "headline": conclusion.headline,
        "reference_action": conclusion.reference_action,
        "breadth_score": float(brow.get("breadth_rebound_score", float("nan"))),
        "down_ratio": float(brow["down_ratio"]),
        "coverage_ratio": float(brow.get("coverage_ratio", float("nan"))),
        "foreign_direction_score": float(frow["foreign_direction_score"]),
        "foreign_oi_ratio": float(frow["foreign_oi_ratio"]),
        "foreign_oi_change_ratio": float(frow["foreign_oi_change_ratio"]),
        "foreign_long_change_ratio": float(frow["foreign_long_change_ratio"]),
        "foreign_short_change_ratio": float(frow["foreign_short_change_ratio"]),
        "foreign_net_oi": float(frow["foreign_net_oi"]),
        "0050_close": float(close.iloc[-1]),
        "price_source": str(close.name),
        "version": VERSION,
    }


def run() -> int:
    configure_logging()
    progress = PipelineProgress(total=22, label="CLOUD_DAILY", emit=logging.info)
    sender = os.getenv("GMAIL_SENDER", "").strip()
    recipients = os.getenv("EMAIL_RECIPIENTS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    settings = EmailSettings(sender, recipients, password) if sender and recipients and password else None
    now = datetime.now(TAIPEI)
    signal_sheet = run_sheet = audit_sheet = None
    data_date = ""
    git_commit = current_git_commit()
    run_id = f"{now:%Y%m%dT%H%M%S%z}_{os.getenv('GITHUB_RUN_ID', 'local')}"
    manifest = RunManifest(run_id, now.isoformat(), git_commit, None, None)
    manifest_path = Path(os.getenv("RUN_MANIFEST_PATH", "outputs/run_manifest.json"))
    try:
        progress.run("FinLab headless authentication", authenticate_finlab_headless)
        signal_sheet, run_sheet = progress.run(
            "Connect primary Google Sheets",
            connect_sheet,
            required_env("GOOGLE_SHEET_ID"),
            required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
        breadth = progress.run("Load FinLab market breadth", load_live_breadth)
        futures = progress.run("Load FinLab futures", load_live_futures)
        adjusted_open, adjusted_close = progress.run("Load FinLab 0050 prices", load_live_0050_prices)
        spot = progress.run("Load FinLab spot institutional flow", load_live_spot_flow)
        progress.diagnostic(
            "market_data",
            breadth_rows=len(breadth),
            futures_rows=len(futures),
            spot_evidence_count=len(spot.evidence),
            spot_metric_count=len(spot.canonical_metrics or {}),
            spot_data_date=spot.data_date,
            spot_data_quality=spot.data_quality,
            price_rows=len(adjusted_close),
            breadth_latest=pd.Timestamp(breadth.index[-1]).date(),
            futures_latest=pd.Timestamp(futures.index[-1]).date(),
            price_latest=pd.Timestamp(adjusted_close.index[-1]).date(),
        )
        manifest.stage_status["data_fetch"] = "SUCCESS"
        snapshot = build_snapshot(breadth, futures, adjusted_close, now)
        data_date = str(snapshot["data_date"])
        manifest.actual_data_date = data_date
        manifest.expected_data_date = str(expected_latest_trading_date(now, adjusted_close.index).date())
        if manifest.expected_data_date != data_date:
            manifest.stage_status["data_fetch"] = "STALE_DATA"
            raise RuntimeError(f"資料過期：expected={manifest.expected_data_date}, actual={data_date}")
        from dashboard.research_registry import validate_registry
        registry_errors = progress.run("Validate canonical research registry", validate_registry)
        if registry_errors:
            raise RuntimeError("Canonical registry validation failed: " + "; ".join(registry_errors))
        manifest.stage_status["registry"] = "SUCCESS"
        events = progress.run(
            "Evaluate canonical signals",
            build_canonical_events,
            breadth,
            futures,
            spot,
            extend_future_sessions(adjusted_close.index),
            now,
        )
        evaluation = production_evaluation(events)
        progress.diagnostic("production_evaluation", **evaluation.production_diagnostic())
        progress.diagnostic("nonproduction_evaluation", **evaluation.nonproduction_diagnostic())
        manifest.stage_status["signal_evaluation"] = "SUCCESS"
        event_table = events_frame(events)
        manifest.signal_event_count = len(event_table)
        manifest.stage_status["signal_events"] = "SUCCESS"
        calendar = build_forecast_calendar(events)
        manifest.stage_status["forecast_calendar"] = "SUCCESS"
        sync_result = progress.run(
            "Sync daily signal sheet", sync_daily_signal, signal_sheet, snapshot, adjusted_open, adjusted_close
        )
        spot_sheet = progress.run(
            "Connect spot-flow sheet",
            connect_spot_sheet,
            required_env("GOOGLE_SHEET_ID"),
            required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
        spot_rows = progress.run("Sync spot-flow evidence", sync_spot_signals, spot_sheet, spot, now, VERSION, git_commit)
        event_sheet, calendar_sheet, audit_sheet = progress.run(
            "Connect vNext ledger sheets",
            connect_vnext_sheets,
            required_env("GOOGLE_SHEET_ID"),
            required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
        existing_events = progress.run("Load existing signal_events ledger", load_signal_events, event_sheet)
        progress.diagnostic("existing_signal_ledger", rows=len(existing_events))
        ledger = progress.run(
            "Build historical signal ledger",
            build_signal_ledger,
            canonical_metrics(breadth, futures, spot),
            extend_future_sessions(adjusted_close.index),
            events,
            now,
            existing=existing_events,
        )
        progress.diagnostic(
            "signal_ledger",
            current_events=len(events),
            existing_events=len(existing_events),
            merged_events=len(ledger),
        )
        target_calendar = progress.run("Aggregate target-date calendar", aggregate_events_by_target_date, ledger)
        manifest.calendar_row_count = len(target_calendar)
        event_rows = progress.run("Sync signal_events ledger", sync_signal_events, event_sheet, ledger, run_id, git_commit)
        calendar_rows = progress.run(
            "Sync target-date forecast calendar", sync_forecast_calendar, calendar_sheet, target_calendar, run_id, git_commit
        )
        manifest.stage_status["sheet_write"] = "SUCCESS"
        manifest.daily_record_action = sync_result.action if production_events(events) else "VALID_NO_SIGNAL"
        sheet_note = (
            f"Google Sheet：{sync_result.action}；"
            f"本次補登未來報酬 {sync_result.updated_outcomes} 格；"
            f"法人現貨證據 {spot_rows} 列；signal_events {event_rows} 列；calendar {calendar_rows} 列。"
        )
        archive_folder = progress.run(
            "Archive immutable run outputs",
            archive_run,
            Path("outputs/runs"), run_id, event_table, calendar, manifest,
            history_events=events_frame(ledger), target_calendar=target_calendar,
        )
        manifest.stage_status["archive"] = "SUCCESS"
        if manifest.finalize() != "SUCCESS":
            raise RuntimeError(f"completion contract failed: {manifest.overall_status}")
        manifest.write(manifest_path); manifest.write(archive_folder / "run_manifest.json")
        try:
            subject, plain, html_body = build_vnext_report(
                events, calendar, data_date, run_id, git_commit, manifest.overall_status,
                os.getenv("DASHBOARD_URL", ""), target_events=ledger,
                target_calendar=target_calendar,
            )
            def send_optional_report() -> None:
                if settings is None:
                    raise RuntimeError("Gmail settings unavailable")
                send_gmail(
                    settings,
                    subject,
                    plain + "\n" + sheet_note,
                    html_body.replace("</body>", f"<p>{sheet_note}</p></body>"),
                )

            progress.run("Send optional Gmail report", send_optional_report)
            manifest.email_status = "SUCCESS"
        except Exception:
            manifest.email_status = "FAILED_OPTIONAL"
            logging.exception("Optional email delivery failed")
        manifest.write(manifest_path); manifest.write(archive_folder / "run_manifest.json")
        try:
            progress.run("Append run audit", append_run_audit, audit_sheet, manifest)
            progress.run("Append run log", append_run_log, run_sheet, now, "success", data_date, sheet_note, VERSION)
            progress.run(
                "Publish current-run artifacts",
                write_current_run_artifacts,
                CURRENT_RUN_DIR, events, calendar, ledger_events=ledger,
                run_id=run_id, git_commit=git_commit,
                calculated_at=now.isoformat(), actual_data_date=data_date,
                pipeline_status="SUCCESS", run_mode="cloud_daily",
            )
        except Exception:
            manifest.stage_status["sheet_write"] = "FAILED"
            manifest.finalize(); manifest.write(manifest_path); manifest.write(archive_folder / "run_manifest.json")
            raise
        progress.finish("SUCCESS", run_id=run_id, data_date=data_date, ledger_rows=len(ledger))
        logging.info("Cloud daily completed; run_id=%s; %s", run_id, sheet_note)
        return 0
    except Exception as exc:
        logging.exception("Cloud daily failed")
        progress.finish("FAILED", run_id=run_id, data_date=data_date or "unknown", error=type(exc).__name__)
        message = f"{type(exc).__name__}: {exc}"
        if manifest.stage_status.get("data_fetch") != "STALE_DATA":
            first_missing = next((name for name in ("data_fetch", "registry", "signal_evaluation", "signal_events", "forecast_calendar", "sheet_write", "archive") if name not in manifest.stage_status), None)
            if first_missing:
                manifest.stage_status[first_missing] = "FAILED"
        manifest.finalize()
        try:
            manifest.write(manifest_path)
        except Exception:
            logging.exception("Could not write failure manifest")
        if run_sheet is not None:
            try:
                append_run_log(run_sheet, now, "failed", data_date, message, VERSION)
            except Exception:
                logging.exception("Could not append failure run log")
        title = f"【臺股市場溫度計】{now:%Y-%m-%d} 雲端更新失敗"
        body = f"雲端自動更新失敗：{message}\n請查看GitHub Actions執行紀錄。"
        try:
            if settings is not None:
                send_gmail(settings, title, body, simple_html(title, body.splitlines()))
        except Exception:
            logging.exception("Failure notification email also failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
