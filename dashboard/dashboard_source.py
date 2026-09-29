"""Validated dashboard data-source selection and current-run artifact handoff."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

import pandas as pd

from .canonical_pipeline import build_canonical_events
from .data_service import load_breadth_snapshot
from .forecast_calendar import build_forecast_calendar
from .finlab_auth import (
    FinLabAuthFailed,
    FinLabAuthUnavailable,
    authenticate_finlab_headless,
    headless_credentials_available,
)
from .signal_engine import SignalEvent, events_frame
from .trading_calendar import expected_latest_trading_date, extend_future_sessions


ROOT = Path(__file__).resolve().parents[1]
CURRENT_RUN_DIR = ROOT / "outputs" / "current"
ARTIFACT_FILES = ("signal_events.csv", "forecast_calendar.csv", "latest_signal_summary.csv")
EVENT_COLUMNS = [field.name for field in fields(SignalEvent)]
CALENDAR_COLUMNS = ["target_date", "bullish_count", "bearish_count", "net_vote", "active_signals"]
SUMMARY_COLUMNS = [
    "source", "plain_definition", "direction", "horizon", "matched",
    "evaluation_status", "research_status", "target_date",
]


@dataclass(frozen=True)
class DashboardSource:
    source_type: str
    data_date: str | None
    calculated_at: str | None
    run_id: str | None
    git_commit: str | None
    status: str
    evaluation_result: str
    freshness: str
    is_production: bool
    warning: str | None
    events: tuple[SignalEvent, ...]
    calendar: pd.DataFrame
    latest_summary: pd.DataFrame
    artifact_dir: Path | None = None


def current_git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return "unavailable"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_with_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result:
            result[column] = pd.Series(dtype="object")
    return result.loc[:, columns]


def latest_signal_summary_frame(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    return _frame_with_columns(events_frame(events), SUMMARY_COLUMNS)


def write_current_run_artifacts(
    output_dir: str | Path,
    events: tuple[SignalEvent, ...],
    calendar: pd.DataFrame,
    *,
    run_id: str,
    git_commit: str,
    calculated_at: str,
    actual_data_date: str,
    pipeline_status: str = "SUCCESS",
    run_mode: str = "preview",
    evaluation_result: str | None = None,
) -> Path:
    """Atomically publish one internally consistent dashboard run."""
    if pipeline_status != "SUCCESS":
        raise ValueError("Only a successful calculation may become the current dashboard run")
    target = Path(output_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        event_table = _frame_with_columns(events_frame(events), EVENT_COLUMNS)
        calendar_table = _frame_with_columns(calendar, CALENDAR_COLUMNS)
        summary_table = latest_signal_summary_frame(events)
        event_table.to_csv(temporary / "signal_events.csv", index=False)
        calendar_table.to_csv(temporary / "forecast_calendar.csv", index=False)
        summary_table.to_csv(temporary / "latest_signal_summary.csv", index=False)
        checksums = {name: _sha256(temporary / name) for name in ARTIFACT_FILES}
        if evaluation_result is None:
            statuses = {event.evaluation_status for event in events}
            if "DATA_UNAVAILABLE" in statuses:
                evaluation_result = "DATA_UNAVAILABLE"
            elif any(event.matched and event.research_status == "RETAINED" for event in events):
                evaluation_result = "SIGNALS_PRESENT"
            else:
                evaluation_result = "VALID_NO_SIGNAL"
        manifest = {
            "run_id": run_id,
            "run_timestamp": calculated_at,
            "calculation_timestamp": calculated_at,
            "git_commit": git_commit,
            "actual_data_date": actual_data_date,
            "overall_status": "SUCCESS",
            "pipeline_status": pipeline_status,
            "evaluation_result": evaluation_result,
            "run_mode": run_mode,
            "signal_event_count": len(event_table),
            "calendar_row_count": len(calendar_table),
            "artifact_checksums": checksums,
        }
        (temporary / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        previous = target.with_name(f".{target.name}-previous")
        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.replace(previous)
        temporary.replace(target)
        if previous.exists():
            shutil.rmtree(previous)
        return target
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _optional(value: object) -> object | None:
    return None if pd.isna(value) else value


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _events_from_frame(frame: pd.DataFrame) -> tuple[SignalEvent, ...]:
    missing = set(EVENT_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("signal_events schema missing: " + ", ".join(sorted(missing)))
    events: list[SignalEvent] = []
    for row in frame.to_dict("records"):
        values = {name: _optional(row[name]) for name in EVENT_COLUMNS}
        values["matched"] = _as_bool(values["matched"])
        values["horizon"] = int(values["horizon"])
        values["sample_size"] = None if values["sample_size"] is None else int(values["sample_size"])
        for name in (
            "raw_value", "normalized_value", "historical_mean_return",
            "historical_median_return", "historical_win_rate", "relative_mean_return",
            "global_fdr", "family_fdr",
        ):
            values[name] = None if values[name] is None else float(values[name])
        events.append(SignalEvent(**values))
    return tuple(events)


def _freshness(data_date: str, now: object) -> str:
    expected = expected_latest_trading_date(now, [pd.Timestamp(data_date)])
    return "FRESH" if pd.Timestamp(data_date).normalize() >= expected else "STALE"


def load_current_run_artifacts(
    artifact_dir: str | Path = CURRENT_RUN_DIR,
    now: object | None = None,
) -> DashboardSource:
    folder = Path(artifact_dir)
    manifest_path = folder / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"current-run manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("overall_status") != "SUCCESS" or manifest.get("pipeline_status") != "SUCCESS":
        raise ValueError(f"current-run manifest is not successful: {manifest.get('overall_status')}")
    required = ("actual_data_date", "calculation_timestamp", "git_commit", "run_id")
    missing_meta = [name for name in required if not manifest.get(name)]
    if missing_meta:
        raise ValueError("current-run manifest missing: " + ", ".join(missing_meta))
    checksums = manifest.get("artifact_checksums")
    if not isinstance(checksums, dict):
        raise ValueError("current-run manifest has no artifact checksums")
    for name in ARTIFACT_FILES:
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f"current-run artifact missing: {path}")
        if checksums.get(name) != _sha256(path):
            raise ValueError(f"current-run artifact checksum mismatch: {name}")
    event_table = pd.read_csv(folder / "signal_events.csv")
    calendar = pd.read_csv(folder / "forecast_calendar.csv")
    summary = pd.read_csv(folder / "latest_signal_summary.csv")
    events = _events_from_frame(event_table)
    if set(CALENDAR_COLUMNS) - set(calendar.columns):
        raise ValueError("forecast_calendar schema invalid")
    if set(SUMMARY_COLUMNS) - set(summary.columns):
        raise ValueError("latest_signal_summary schema invalid")
    expected_calendar = _frame_with_columns(build_forecast_calendar(events), CALENDAR_COLUMNS)
    actual_calendar = _frame_with_columns(calendar, CALENDAR_COLUMNS)
    if expected_calendar.fillna("").astype(str).to_dict("records") != actual_calendar.fillna("").astype(str).to_dict("records"):
        raise ValueError("forecast_calendar does not match current-run signal_events")
    freshness = _freshness(str(manifest["actual_data_date"]), now or pd.Timestamp.now(tz="Asia/Taipei"))
    warning = None if freshness == "FRESH" else "Current-run artifact 已過期，正在嘗試取得 Live FinLab。"
    evaluation_result = str(manifest.get("evaluation_result", "UNKNOWN"))
    return DashboardSource(
        source_type="CURRENT_RUN", data_date=str(manifest["actual_data_date"]),
        calculated_at=str(manifest["calculation_timestamp"]), run_id=str(manifest["run_id"]),
        git_commit=str(manifest["git_commit"]), status="SUCCESS",
        evaluation_result=evaluation_result, freshness=freshness,
        is_production=freshness == "FRESH" and evaluation_result != "DATA_UNAVAILABLE",
        warning=warning, events=events,
        calendar=actual_calendar, latest_summary=summary, artifact_dir=folder,
    )


def calculate_live_source(
    artifact_dir: str | Path = CURRENT_RUN_DIR,
    now: datetime | pd.Timestamp | None = None,
) -> DashboardSource:
    from .data_service import load_live_0050_prices, load_live_breadth, load_live_futures
    from .spot_flow_service import load_live_spot_flow

    authenticate_finlab_headless()
    timestamp = pd.Timestamp(now or pd.Timestamp.now(tz="Asia/Taipei"))
    breadth = load_live_breadth()
    futures = load_live_futures()
    spot = load_live_spot_flow()
    _, adjusted_close = load_live_0050_prices()
    data_dates = [pd.Timestamp(frame.index[-1]).normalize() for frame in (breadth, futures)]
    data_dates.append(pd.Timestamp(adjusted_close.index[-1]).normalize())
    if len(set(data_dates)) != 1:
        raise RuntimeError("Live FinLab 資料日期不一致：" + ", ".join(map(str, data_dates)))
    data_date = str(data_dates[0].date())
    if _freshness(data_date, timestamp) != "FRESH":
        raise RuntimeError(f"Live FinLab 資料仍過期：actual={data_date}")
    events = build_canonical_events(
        breadth, futures, spot, extend_future_sessions(adjusted_close.index), timestamp
    )
    calendar = build_forecast_calendar(events)
    run_id = f"{timestamp.strftime('%Y%m%dT%H%M%S%z')}_streamlit"
    write_current_run_artifacts(
        artifact_dir, events, calendar, run_id=run_id, git_commit=current_git_commit(),
        calculated_at=timestamp.isoformat(), actual_data_date=data_date,
        pipeline_status="SUCCESS", run_mode="streamlit_refresh",
    )
    loaded = load_current_run_artifacts(artifact_dir, timestamp)
    return DashboardSource(**{**loaded.__dict__, "source_type": "LIVE_FINLAB"})


def build_snapshot_source(now: object | None = None) -> DashboardSource:
    timestamp = pd.Timestamp(now or pd.Timestamp.now(tz="Asia/Taipei"))
    breadth = load_breadth_snapshot()
    data_date = str(pd.Timestamp(breadth.index[-1]).date())
    events = build_canonical_events(
        breadth, pd.DataFrame(index=breadth.index), None,
        extend_future_sessions(breadth.index), timestamp,
    )
    calendar = build_forecast_calendar(events)
    return DashboardSource(
        source_type="RESEARCH_SNAPSHOT", data_date=data_date,
        calculated_at=timestamp.isoformat(), run_id=None, git_commit=current_git_commit(),
        status="PREVIEW_ONLY", evaluation_result=(
            "DATA_UNAVAILABLE" if any(event.evaluation_status == "DATA_UNAVAILABLE" for event in events)
            else "VALID_NO_SIGNAL"
        ), freshness="STALE", is_production=False,
        warning="目前顯示 Research Snapshot，不是最新市場資料，不應視為今日正式 Forecast。",
        events=events, calendar=calendar, latest_summary=latest_signal_summary_frame(events),
    )


def select_dashboard_source(
    artifact_dir: str | Path = CURRENT_RUN_DIR,
    *,
    now: object | None = None,
    live_available: bool | None = None,
    live_loader: Callable[[], DashboardSource] | None = None,
    snapshot_loader: Callable[[], DashboardSource] | None = None,
) -> DashboardSource:
    """Select CURRENT_RUN → LIVE_FINLAB → RESEARCH_SNAPSHOT without hiding failures."""
    current_issue: str | None = None
    try:
        current = load_current_run_artifacts(artifact_dir, now)
        if current.freshness == "FRESH":
            return current
        current_issue = current.warning
    except Exception as exc:
        current_issue = f"Current-run artifact 不可用：{type(exc).__name__}: {exc}"
    fallback_status = "AUTH_UNAVAILABLE"
    if live_available is None:
        live_available = headless_credentials_available()
    if live_available:
        try:
            return (live_loader or (lambda: calculate_live_source(artifact_dir, now)))()
        except FinLabAuthUnavailable as exc:
            fallback_status = "AUTH_UNAVAILABLE"
            current_issue = f"{current_issue or ''} {exc}".strip()
        except FinLabAuthFailed as exc:
            fallback_status = "AUTH_FAILED"
            current_issue = f"{current_issue or ''} {exc}".strip()
        except Exception as exc:
            fallback_status = "DATA_UNAVAILABLE"
            current_issue = f"{current_issue or ''} Live FinLab 更新失敗：{type(exc).__name__}: {exc}".strip()
    else:
        current_issue = (
            f"{current_issue or ''} AUTH_UNAVAILABLE：未設定完整 FinLab headless credentials；"
            "未嘗試瀏覽器登入。"
        ).strip()
    snapshot = (snapshot_loader or (lambda: build_snapshot_source(now)))()
    warning = " ".join(part for part in (snapshot.warning, current_issue) if part)
    return DashboardSource(**{**snapshot.__dict__, "status": fallback_status, "warning": warning})
