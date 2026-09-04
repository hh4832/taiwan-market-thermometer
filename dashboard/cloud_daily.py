from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.daily_email import TAIPEI, VERSION, build_daily_report, configure_logging
from dashboard.data_service import load_live_0050_close, load_live_breadth, load_live_futures
from dashboard.email_service import EmailSettings, send_gmail, simple_html
from dashboard.google_sheet_service import append_run_log, connect_sheet, sync_daily_signal
from dashboard.google_sheet_service import connect_spot_sheet, sync_spot_signals
from dashboard.spot_flow_service import load_live_spot_flow


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
    sender = required_env("GMAIL_SENDER")
    recipients = required_env("EMAIL_RECIPIENTS")
    settings = EmailSettings(sender, recipients, required_env("GMAIL_APP_PASSWORD").replace(" ", ""))
    now = datetime.now(TAIPEI)
    signal_sheet = run_sheet = None
    data_date = ""
    try:
        import finlab

        finlab.login(required_env("FINLAB_API_TOKEN"))
        signal_sheet, run_sheet = connect_sheet(
            required_env("GOOGLE_SHEET_ID"),
            required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
        breadth = load_live_breadth()
        futures = load_live_futures()
        close = load_live_0050_close()
        spot = load_live_spot_flow()
        snapshot = build_snapshot(breadth, futures, close, now)
        data_date = str(snapshot["data_date"])
        sync_result = sync_daily_signal(signal_sheet, snapshot, close)
        spot_sheet = connect_spot_sheet(
            required_env("GOOGLE_SHEET_ID"),
            required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
        spot_rows = sync_spot_signals(spot_sheet, spot, now, VERSION, current_git_commit())
        subject, plain, html_body, aligned_today = build_daily_report(breadth, futures, now, spot)
        sheet_note = (
            f"Google Sheet：{sync_result.action}；"
            f"本次補登未來報酬 {sync_result.updated_outcomes} 格；"
            f"法人現貨證據同步 {spot_rows} 列。"
        )
        plain = plain + "\n" + sheet_note
        html_body = html_body.replace("</body>", f"<p>{sheet_note}</p></body>")
        send_gmail(settings, subject, plain, html_body)
        append_run_log(run_sheet, now, "success", data_date, sheet_note, VERSION)
        logging.info("Cloud daily completed; aligned_today=%s; %s", aligned_today, sheet_note)
        return 0
    except Exception as exc:
        logging.exception("Cloud daily failed")
        message = f"{type(exc).__name__}: {exc}"
        if run_sheet is not None:
            try:
                append_run_log(run_sheet, now, "failed", data_date, message, VERSION)
            except Exception:
                logging.exception("Could not append failure run log")
        title = f"【臺股市場溫度計】{now:%Y-%m-%d} 雲端更新失敗"
        body = f"雲端自動更新失敗：{message}\n請查看GitHub Actions執行紀錄。"
        try:
            send_gmail(settings, title, body, simple_html(title, body.splitlines()))
        except Exception:
            logging.exception("Failure notification email also failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
