from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
import html
from typing import Mapping

import pandas as pd

from dashboard.data_service import load_live_0050_prices, load_live_breadth, load_live_futures
from dashboard.canonical_pipeline import build_canonical_events
from dashboard.forecast_calendar import build_forecast_calendar
from dashboard.forecast_presentation import build_forecast_presentation
from dashboard.historical_validation import empty_historical_validation
from dashboard.options_service import load_live_options
from dashboard.trading_calendar import extend_future_sessions
from dashboard.spot_flow_service import SpotFlowReport, load_live_spot_flow
from dashboard.email_service import EmailSettings, send_gmail, simple_html
from dashboard.signal_engine import SignalEvent
from dashboard.report_analytics import (
    build_episode_index,
    formal_hit_records,
    market_position,
    status_changes,
)
from dashboard.signal_presentation import event_audit_record, research_direction_label

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "email_notification.json"
LOG_DIR = ROOT / "logs"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
KEYRING_SERVICE = "taiwan-market-thermometer"
TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")


def _value(value: object, percent: bool = False) -> str:
    if value is None or value == "":
        return "無法判定"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    return f"{number:.2%}" if percent else f"{number:g}"


def _report_pct(value: object, *, points: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number * 100:+.{digits}f}{' pp' if points else '%'}"


def _direction(direction: object) -> str:
    return {"bullish": "偏多", "bearish": "偏空"}.get(str(direction), "方向未確認")


def _clean_text(value: object) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "—" if text in {"", "無法判定", "DEFINITION_UNAVAILABLE", "nan", "None"} else text


def _short_date(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return pd.Timestamp(value).strftime("%m/%d")
    except (TypeError, ValueError):
        return "—"


def _event_key(record: Mapping[str, object]) -> tuple[str, str, int, str]:
    return (
        str(record.get("signal_date", "")),
        str(record.get("signal_id", "")),
        int(record.get("horizon", 0) or 0),
        str(record.get("target_date", "")),
    )


def _canonical_direction(record: Mapping[str, object]) -> str:
    label = research_direction_label(
        str(record.get("signal_id", "")),
        str(record.get("canonical_direction") or ""),
        str(record.get("evidence_scope") or "absolute"),
    )
    return {"多": "偏多", "空": "偏空"}.get(label, label)


def _event_stats(
    record: Mapping[str, object], event_lookup: Mapping[tuple[str, str, int, str], SignalEvent]
) -> tuple[str, str, str, str, str]:
    event = event_lookup.get(_event_key(record))
    if event is None:
        return "—", "—", "—", "—", "—"
    return (
        _report_pct(event.historical_mean_return),
        _report_pct(event.historical_median_return),
        _report_pct(event.relative_mean_return, points=True),
        _report_pct(event.historical_win_rate),
        "—" if event.sample_size is None else str(event.sample_size),
    )


def _raw_definition(record: Mapping[str, object]) -> str:
    formula = _clean_text(record.get("raw_metric_formula"))
    definition = _clean_text(record.get("metric_definition"))
    lookback = record.get("lookback_days")
    period = (
        "當日" if lookback is not None and pd.notna(lookback) and int(lookback) == 0
        else f"{int(lookback)}交易日" if lookback is not None and pd.notna(lookback)
        else "—"
    )
    window = record.get("rolling_window")
    normalization = _clean_text(record.get("normalization_type"))
    norm_window = f"W{int(window)} {normalization}" if window is not None and pd.notna(window) else normalization
    return (
        f"定義：{definition}\n公式：{formula}｜計算期間 {period}｜標準化視窗 {norm_window}｜"
        f"Raw {_clean_text(record.get('raw_value_display'))}｜標準化 {_clean_text(record.get('normalized_value'))}｜"
        f"Threshold {_clean_text(record.get('threshold'))}｜"
        f"{'命中' if record.get('matched') is True else '未命中' if record.get('matched') is False else '—'}"
    )


def _interpretation(records: list[Mapping[str, object]], events: Mapping[tuple[str, str, int, str], SignalEvent]) -> list[str]:
    numeric: list[tuple[float | None, float | None, float | None]] = []
    scopes: set[str] = set()
    flags: set[str] = set()
    risks: set[str] = set()
    for record in records:
        event = events.get(_event_key(record))
        if event is None:
            continue
        numeric.append((event.historical_mean_return, event.historical_median_return, event.relative_mean_return))
        scopes.add(event.evidence_scope)
        audit = event_audit_record(event)
        if audit.get("research_flag"):
            flags.add(str(audit["research_flag"]))
        risk = _clean_text(audit.get("risks"))
        if risk != "—":
            risks.add(risk)
    lines: list[str] = []
    available = [item for item in numeric if item[0] is not None and item[1] is not None]
    if available:
        means = [item[0] for item in available]
        medians = [item[1] for item in available]
        if all(value is not None and value > 0 for value in means + medians):
            lines.append("歷史 Mean、Median 均正，代表樣本中的絕對報酬平均與中位數為正；不代表未來必然上漲。")
        elif any(value is not None and value > 0 for value in means + medians) and any(value is not None and value < 0 for value in means + medians):
            lines.append("歷史 Mean、Median 正負相反，提示報酬分布可能偏斜；不代表未來方向。")
        elif all(value is not None and value < 0 for value in means + medians):
            lines.append("歷史 Mean、Median 均負，代表樣本中的絕對報酬平均與中位數為負；不等同本次預測跌幅。")
    rel = [item[2] for item in numeric if item[2] is not None]
    if rel and all(value < 0 for value in rel):
        lines.append("Relative Mean 為負：歷史表現弱於對照基準，不代表絕對報酬必然為負。")
    elif rel and all(value > 0 for value in rel):
        lines.append("Relative Mean 為正：歷史表現優於對照基準，不代表未來必然上漲。")
    if "PARAMETER_ISLAND" in flags:
        lines.append("風險：PARAMETER_ISLAND，鄰近參數支持不一致。")
    if risks:
        # Variants commonly share one canonical warning; keep it once per group.
        lines.append("風險：" + "；".join(sorted(risks)))
    return lines


def _formal_records(historical: pd.DataFrame, data_date: str) -> pd.DataFrame:
    records = formal_hit_records(historical, data_date)
    if records.empty:
        return records
    from dashboard.signal_presentation import historical_validation_results

    projected = historical_validation_results(historical)
    valid = projected.loc[
        projected["maturity"].eq("MATURED")
        & projected["actual_return"].notna()
        & projected["outcome"].isin({"HIT", "MISS", "FLAT"})
        & projected["target_date"].astype(str).le(data_date)
    ].copy()
    if valid.empty:
        return valid
    keys = ["signal_date", "economic_signal_id", "direction", "target_date"]
    valid = valid.sort_values(keys + ["signal_id"])
    valid = valid.drop_duplicates(keys, keep="first")
    return valid


def _table_html(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(value)}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:13px'>"
        f"<thead><tr style='background:#eef4f2'>{th}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _html_paragraph(value: str) -> str:
    return "<p style='margin:6px 0;white-space:pre-line'>" + html.escape(value) + "</p>"


def build_vnext_report(
    events: tuple[SignalEvent, ...],
    calendar: pd.DataFrame,
    data_date: str,
    run_id: str,
    git_commit: str,
    pipeline_status: str,
    dashboard_url: str = "",
    target_events: tuple[SignalEvent, ...] | None = None,
    target_calendar: pd.DataFrame | None = None,
    historical_validation: pd.DataFrame | None = None,
    publication_status: str = "UNKNOWN",
    snapshot: Mapping[str, object] | None = None,
    adjusted_open: pd.Series | None = None,
    adjusted_close: pd.Series | None = None,
    trading_sessions: pd.Index | list[object] | None = None,
    live_track_record: pd.DataFrame | None = None,
    previous_events: tuple[SignalEvent, ...] = (),
    spreadsheet_url: str = "",
    sheet_write_note: str = "",
) -> tuple[str, str, str]:
    """Render the four-section email from canonical presentation projections."""
    if pipeline_status != "SUCCESS":
        raise RuntimeError("DATA_UNAVAILABLE：非SUCCESS pipeline不得寄送正常市場預測信")
    ledger = target_events if target_events is not None else events
    projection = build_forecast_presentation(
        events,
        ledger,
        historical_validation if historical_validation is not None else empty_historical_validation(),
        data_date,
    )
    next_view = projection["next_trading_day_forecast"]
    post_close = projection["post_close_forward_signals"]
    subject = f"【{pd.Timestamp(data_date):%m/%d} 台股市場溫度計】"
    prices = market_position(adjusted_close)
    historical = historical_validation if historical_validation is not None else empty_historical_validation()
    formal = _formal_records(historical, data_date)
    event_lookup = {
        (event.signal_date, event.signal_id, int(event.horizon), event.target_date or ""): event
        for event in tuple(ledger) + tuple(events)
    }
    target_date = next_view.get("target_date")
    production_bull = next_view.get("production_bullish_count")
    production_bear = next_view.get("production_bearish_count")
    projection_available = next_view.get("status") != "DATA_UNAVAILABLE"
    ledger_complete = next_view.get("status") != "LEDGER_INCOMPLETE"
    research_today = [
        group for group in post_close.get("signal_groups", [])
        if not group.get("production_eligible")
    ]
    research_class_count = len({str(group.get("economic_signal_id")) for group in research_today})
    today_rows = [event for event in events if event.signal_date == data_date]
    today_incomplete = not today_rows or any(
        event.matched and (
            event.availability_status != "KNOWN"
            or event.evaluation_status in {"DATA_UNAVAILABLE", "ROLLING_WARMUP"}
        )
        for event in today_rows
    )

    section1 = [f"資料日：{_short_date(data_date)}｜下一交易日 Target Date：{_short_date(target_date)}"]
    state = _clean_text((snapshot or {}).get("overall_state"))
    if projection_available and production_bull is not None and production_bear is not None:
        bull, bear = int(production_bull), int(production_bear)
        vote_line = f"正式票：{bull} 多／{bear} 空｜淨票 {bull - bear:+d}"
        if bull == 0 and bear == 0 and research_class_count:
            vote_line += f"；另有研究訊號 {research_class_count} 類"
    else:
        reason = "LEDGER_INCOMPLETE" if projection_available and not ledger_complete else "資料不可用"
        vote_line = f"正式票：—（{reason}）"
    section1.extend([
        f"市場狀態：{state}",
        vote_line,
        f"0050：距 60 日高點 {_report_pct(prices['distance_from_60d_high'])}｜MA60 {_clean_text(prices['vs_ma60'])}｜MA120 {_clean_text(prices['vs_ma120'])}",
        f"今日新觸發研究訊號類別：{'—' if today_incomplete else research_class_count}",
    ])
    alerts = status_changes(previous_events, events)
    if publication_status != "SUCCESS":
        alerts.append(f"Publication Status={publication_status}")
    if trading_sessions is not None and ledger:
        _episode_frame, episode_lookup = build_episode_index(ledger, trading_sessions)
        first_trigger_ids = {
            str(event.economic_signal_id)
            for event in events
            if event.signal_date == data_date
            and event.matched
            and episode_lookup.get((event.signal_id, event.signal_date)) == data_date
        }
        alerts.extend(f"首次觸發：{signal_id}" for signal_id in sorted(first_trigger_ids))
    if alerts:
        section1.append("⚠️ " + "；".join(dict.fromkeys(alerts)))
    if next_view.get("status") == "LEDGER_INCOMPLETE":
        section1.append("⚠️ LEDGER_INCOMPLETE：目標日歷史 ledger 不完整，缺項不可視為沒有訊號。")

    section2_groups = []
    for group in post_close.get("signal_groups", []):
        records = [r for r in group.get("horizons", []) if r.get("matched") is True]
        if records:
            section2_groups.append({
                **group,
                "horizons": sorted(records, key=lambda r: (int(r.get("horizon", 0)), str(r.get("signal_id", "")))),
            })
    section2_note = (
        "今日訊號資料不完整；未將缺值視為無訊號。"
        if today_incomplete else "今日無新觸發訊號。" if not section2_groups else ""
    )

    target_records = list(next_view.get("signals", []))
    target_rows = []
    definition_notes = []
    for record in target_records:
        stats = _event_stats(record, event_lookup)
        label = _clean_text(record.get("signal_identity"))
        status = _clean_text(record.get("research_status"))
        vote_label = "正式票" if record.get("formal_vote_eligible") else f"研究／{status}（不計票）"
        target_rows.append([
            f"{_short_date(record.get('signal_date'))}｜{label}｜C{record.get('horizon', '—')}",
            "｜".join([
                _clean_text(record.get("raw_metric_formula")),
                f"Raw {_clean_text(record.get('raw_value_display'))}",
                _clean_text(record.get("normalized_value")),
                f"門檻 {_clean_text(record.get('threshold'))}",
            ]),
            stats[0], stats[1], stats[2], f"{vote_label}｜{_canonical_direction(record)}",
        ])
        definition_notes.append(
            f"{_short_date(record.get('signal_date'))} {label} C{record.get('horizon', '—')}：{_clean_text(record.get('metric_definition'))}"
        )
    formal_text = (
        "—／—" if not projection_available
        else f"{int(production_bull or 0)} 多／{int(production_bear or 0)} 空"
    )
    research_target_count = sum(not bool(record.get("formal_vote_eligible")) for record in target_records)
    if next_view.get("status") == "LEDGER_INCOMPLETE":
        target_summary = f"⚠️ LEDGER_INCOMPLETE｜正式：{formal_text}（目前 ledger 可確認票數）｜研究訊號：{research_target_count} 筆；以下僅列 ledger 內可確認項目。"
    elif not projection_available:
        target_summary = "正式：—｜研究訊號：—（資料不可用）"
    else:
        target_summary = f"正式：{formal_text}｜研究訊號：{research_target_count} 筆"

    due_all = formal.loc[formal["target_date"].astype(str).eq(data_date)] if not formal.empty else formal
    due_rows = []
    for row in due_all.to_dict("records"):
        due_rows.append([
            f"{_short_date(row.get('signal_date'))}｜{_clean_text(row.get('signal_id'))}｜C{row.get('horizon', '—')}",
            f"{_direction(row.get('direction'))}｜{_report_pct(row.get('actual_return'))}",
            str(row.get("outcome", "—")),
            _clean_text(row.get("event_origin")),
        ])
    due_unavailable = 0
    if not historical.empty and {"target_date", "maturity", "research_status"} <= set(historical.columns):
        due_unavailable = int((
            historical["target_date"].astype(str).eq(data_date)
            & historical["maturity"].eq("DATA_UNAVAILABLE")
            & historical["research_status"].eq("RETAINED")
        ).sum())
    valid_n = len(formal)
    hit_n = int(formal["outcome"].eq("HIT").sum()) if valid_n else 0
    recent20 = formal.sort_values(["target_date", "signal_date", "signal_id"], ascending=False).head(20) if valid_n else formal
    recent_hits = int(recent20["outcome"].eq("HIT").sum()) if len(recent20) else 0
    origin_counts = formal["event_origin"].astype(str).value_counts().to_dict() if valid_n and "event_origin" in formal else {}
    coverage = f"{_short_date(formal['target_date'].min())}–{_short_date(formal['target_date'].max())}" if valid_n else "—"
    if due_rows:
        due_summary = f"資料日到期正式預測：{len(due_rows)} 筆有效結果。"
    elif due_unavailable:
        due_summary = f"資料日到期正式預測：0 筆有效結果；另有 DATA_UNAVAILABLE {due_unavailable} 筆。"
    else:
        due_summary = "資料日到期正式預測：0 筆有效結果。"
    section4 = [
        due_summary,
        f"累積已成熟正式預測：{valid_n} 筆（PRODUCTION {int(origin_counts.get('PRODUCTION', 0))}｜BACKFILL {int(origin_counts.get('BACKFILL', 0))}）。",
        f"最近 20 筆方向命中率：{recent_hits}/{len(recent20)}（HIT/N）；全部：{hit_n}/{valid_n}（HIT/N）。",
        f"驗證涵蓋目標日期：{coverage}；BACKFILL 與 PRODUCTION 已分列。",
    ]

    lines = [subject, "① 今日市場結論", *section1, "", "② 今日 → 未來（當日新觸發訊號）"]
    html_parts = [f"<h2>{html.escape(subject)}</h2>", "<h3>① 今日市場結論</h3>"]
    html_parts.extend(_html_paragraph(line) for line in section1)
    html_parts.append("<h3>② 今日 → 未來（當日新觸發訊號）</h3>")
    if section2_note:
        lines.append(section2_note)
        html_parts.append(_html_paragraph(section2_note))
    for group in section2_groups:
        records = list(group["horizons"])
        first = records[0]
        name = _clean_text(first.get("signal_identity"))
        directions = {_canonical_direction(record) for record in records}
        direction = next(iter(directions)) if len(directions) == 1 else "方向分歧"
        header = f"{name}｜{direction}｜{_clean_text(first.get('research_status'))}"
        lines.extend([header, _raw_definition(first)])
        html_parts.append(_html_paragraph(header))
        html_parts.extend(_html_paragraph(part) for part in _raw_definition(first).splitlines())
        table_rows = []
        for record in records:
            mean, median, relative, win, n = _event_stats(record, event_lookup)
            table_rows.append([f"C{record.get('horizon', '—')}｜{_short_date(record.get('target_date'))}", mean, median, relative, win, n])
        headers = ["Horizon／目標日", "Mean", "Median", "Relative Mean", "Win Rate", "N"]
        lines.extend(["｜".join(headers), *["｜".join(row) for row in table_rows]])
        html_parts.append(_table_html(headers, table_rows))
        explanations = _interpretation(records, event_lookup) or ["歷史統計缺值以 — 顯示；方向沿用 canonical direction／evidence_scope。"]
        for explanation in explanations:
            lines.append(f"判讀：{explanation}")
            html_parts.append(_html_paragraph(f"判讀：{explanation}"))
        lines.append("")

    lines.extend(["③ 過去 → 下一交易日", f"下一交易日：{_short_date(target_date)}", target_summary])
    html_parts.append("<h3>③ 過去 → 下一交易日</h3>")
    html_parts.extend(_html_paragraph(line) for line in [f"下一交易日：{_short_date(target_date)}", target_summary])
    target_headers = ["Signal Date／訊號名稱／Horizon", "原始條件與當次數值", "Mean", "Median", "Relative Mean", "判斷"]
    lines.extend(["｜".join(target_headers), *["｜".join(row) for row in target_rows]])
    html_parts.append(_table_html(target_headers, target_rows))
    if definition_notes:
        lines.append("原始定義：")
        html_parts.append(_html_paragraph("原始定義："))
        for note in dict.fromkeys(definition_notes):
            lines.append(note)
            html_parts.append(_html_paragraph(note))
    if next_view.get("status") == "LEDGER_INCOMPLETE":
        warning = "LEDGER_INCOMPLETE：歷史 ledger 未涵蓋全部 canonical horizons；不以缺失推論無訊號。"
        lines.append(warning)
        html_parts.append(_html_paragraph(warning))

    lines.extend(["", "④ 過去結果驗證", *section4])
    html_parts.append("<h3>④ 過去結果驗證</h3>")
    html_parts.extend(_html_paragraph(line) for line in section4)
    due_headers = ["到期訊號", "方向／實際報酬", "HIT／MISS／FLAT", "來源"]
    if due_rows:
        lines.extend(["｜".join(due_headers), *["｜".join(row) for row in due_rows]])
        html_parts.append(_table_html(due_headers, due_rows))
    elif due_unavailable:
        unavailable = f"資料日到期但無有效報酬：{due_unavailable} 筆 DATA_UNAVAILABLE（不計為失敗或零報酬）。"
        lines.append(unavailable)
        html_parts.append(_html_paragraph(unavailable))
    lines.extend([
        "",
        f"試算表：{spreadsheet_url or '—'}",
        "完整 variants、FDR、threshold 見 signal_events 分頁。",
        "多空票數不是上漲機率；歷史勝率不是本次命中機率。",
    ])
    html_parts.extend([
        _html_paragraph(f"試算表：{spreadsheet_url or '—'}"),
        _html_paragraph("完整 variants、FDR、threshold 見 signal_events 分頁。"),
        _html_paragraph("多空票數不是上漲機率；歷史勝率不是本次命中機率。"),
    ])
    if dashboard_url:
        lines.insert(-1, f"儀表板：{dashboard_url}")
        html_parts.insert(-1, _html_paragraph(f"儀表板：{dashboard_url}"))
    plain = "\n".join(lines)
    html_body = (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;color:#17302f;max-width:900px;margin:24px auto;line-height:1.6'>"
        + "".join(html_parts) + "</body></html>"
    )
    return subject, plain, html_body


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "daily_email.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_settings() -> tuple[EmailSettings, str]:
    import keyring

    if not CONFIG_PATH.exists():
        raise RuntimeError("尚未完成Email設定；請先執行 setup_daily_email.bat。")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sender = str(config["sender_email"]).strip()
    recipient = str(config.get("recipient_email", sender)).strip()
    app_password = keyring.get_password(KEYRING_SERVICE, "gmail_app_password")
    finlab_token = keyring.get_password(KEYRING_SERVICE, "finlab_token")
    if not app_password:
        raise RuntimeError("Windows認證管理員中找不到Gmail應用程式密碼。")
    if not finlab_token:
        raise RuntimeError("Windows認證管理員中找不到FinLab Token。")
    return EmailSettings(sender, recipient, app_password), finlab_token


def _percent(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}%}"


def _spot_light(item: object) -> tuple[str, str, str]:
    status = getattr(item, "a_grade_status")
    direction = getattr(item, "direction")
    research_status = getattr(item, "research_status", "RETAINED")
    evidence_scope = getattr(item, "evidence_scope", "absolute")
    if research_status != "RETAINED" or evidence_scope != "absolute":
        return "⚪", f"{research_status}／{evidence_scope}（不計正式票）", "#5f6368"
    if status == "matched" and direction == "bullish":
        return "🟢", "A級偏多命中", "#137333"
    if status == "matched" and direction == "bearish":
        return "🔴", "A級偏空命中", "#b3261e"
    if status in {"insufficient_data", "suspended"}:
        return "🟡", "資料不足／暫停", "#8a5a00"
    return "⚪", "A級條件未命中", "#5f6368"


def _number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _spot_plain_lines(spot: SpotFlowReport) -> list[str]:
    lines = [
        "法人現貨 canonical 診斷（DEPRECATED legacy view；正式預測以 SignalEvent 為準）：",
        f"資料日 {spot.data_date}；偏多family {spot.bullish_family_count}個；偏空family {spot.bearish_family_count}個；混合family {spot.mixed_family_count}個；整體資料品質 {spot.data_quality}。",
        "燈號：🟢偏多A級命中；🔴偏空A級命中；⚪未命中；🟡資料不足或品質警告。",
    ]
    for item in spot.evidence:
        light, status, _ = _spot_light(item)
        lines.extend([
            f"{light} {item.label}｜{status}｜{item.research_status}｜family={item.family}｜觀察期={item.horizon}",
            f"  原始：Buy={_number(item.raw_buy_amount, 0)}；Sell={_number(item.raw_sell_amount, 0)}；Turnover={_number(item.market_turnover, 0)}",
            f"  指標：{item.accumulation_days}日正式比例={_number(item.current_value, 6)}；{item.normalization.upper()}={_number(item.normalized_value, 2)}；A級門檻={item.threshold_label}；視窗={item.reference_window}日",
            f"  品質：{item.data_quality}" + (f"（{'; '.join(item.quality_flags)}）" if item.quality_flags else ""),
            f"  證據：{item.evidence_statement}",
        ])
    return lines


def _spot_html(spot: SpotFlowReport) -> str:
    rows = []
    for item in spot.evidence:
        light, status, color = _spot_light(item)
        quality = item.data_quality + (f" ({'; '.join(item.quality_flags)})" if item.quality_flags else "")
        cells = [
            f"<span style='font-size:20px'>{light}</span><br><strong style='color:{color}'>{html.escape(status)}</strong>",
            f"<strong>{html.escape(item.label)}</strong><br><small>{html.escape(item.family)}｜{html.escape(item.horizon)}</small>",
            f"Buy {_number(item.raw_buy_amount, 0)}<br>Sell {_number(item.raw_sell_amount, 0)}<br>Turnover {_number(item.market_turnover, 0)}",
            f"比例 {_number(item.current_value, 6)}<br>{item.normalization.upper()} {_number(item.normalized_value, 2)}<br>門檻 {html.escape(item.threshold_label)}／{item.reference_window}日",
            f"{html.escape(quality)}<br><small>{html.escape(item.evidence_statement)}</small>",
        ]
        rows.append("<tr>" + "".join(f"<td style='padding:10px;border:1px solid #d7dedc;vertical-align:top'>{cell}</td>" for cell in cells) + "</tr>")
    return (
        "<h3>法人現貨 canonical 診斷（legacy view）</h3>"
        f"<p>資料日 {html.escape(spot.data_date)}；🟢偏多 family {spot.bullish_family_count}；"
        f"🔴偏空 family {spot.bearish_family_count}；混合 family {spot.mixed_family_count}。"
        "此區由 canonical registry / SignalEvent 投影；RETEST、REJECTED 與 relative 證據不計正式票。</p>"
        "<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#eef4f2'><th>燈號</th><th>定義</th><th>原始累積值</th><th>衍生指標</th><th>品質與證據</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def build_daily_report(
    breadth: pd.DataFrame,
    futures: pd.DataFrame,
    now: datetime,
    spot: SpotFlowReport | None = None,
) -> tuple[str, str, str, bool]:
    """Disabled legacy report; all user-facing email must use SignalEvent."""
    raise RuntimeError(
        "Legacy daily report is disabled; use build_vnext_report with canonical SignalEvent data"
    )


def run(send_test: bool = False) -> int:
    configure_logging()
    settings, finlab_token = load_settings()
    if send_test:
        title = "【臺股市場溫度計】Email設定測試成功"
        body = "Gmail寄送設定已完成。每日交易日20:00將由Windows工作排程執行。"
        send_gmail(settings, title, body, simple_html(title, [body]))
        logging.info("Test email sent to %s", settings.recipient_email)
        return 0

    try:
        from dashboard.finlab_auth import authenticate_finlab_headless

        authenticate_finlab_headless(legacy_token=finlab_token)
        now = datetime.now(TAIPEI)
        breadth, futures, spot = load_live_breadth(), load_live_futures(), load_live_spot_flow()
        try:
            options = load_live_options()
        except Exception as exc:
            options = None
            logging.warning("Optional options evidence unavailable: %s: %s", type(exc).__name__, exc)
        _, adjusted_close = load_live_0050_prices()
        data_dates = {str(pd.Timestamp(frame.index[-1]).date()) for frame in (breadth, futures, adjusted_close.to_frame())}
        if len(data_dates) != 1:
            raise RuntimeError("DATA_UNAVAILABLE：市場資料日期未對齊")
        data_date = data_dates.pop()
        if options is not None and str(pd.Timestamp(options.index[-1]).date()) != data_date:
            logging.warning(
                "Optional options evidence date mismatch: options=%s signal=%s",
                pd.Timestamp(options.index[-1]).date(), data_date,
            )
            options = None
        events = build_canonical_events(
            breadth, futures, spot, extend_future_sessions(adjusted_close.index), now,
            options=options,
        )
        subject, plain, html_body = build_vnext_report(
            events, build_forecast_calendar(events), data_date,
            f"local_{now:%Y%m%dT%H%M%S}", "local", "SUCCESS",
        )
        send_gmail(settings, subject, plain, html_body)
        logging.info("Daily vNext report sent; data_date=%s", data_date)
        return 0
    except Exception as exc:
        logging.exception("Daily update failed")
        title = f"【臺股市場溫度計】{datetime.now(TAIPEI):%Y-%m-%d} 更新失敗"
        body = f"自動更新失敗：{type(exc).__name__}: {exc}\n請開啟 logs\\daily_email.log 檢查。"
        try:
            send_gmail(settings, title, body, simple_html(title, body.splitlines()))
        except Exception:
            logging.exception("Failure notification email also failed")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="臺股市場溫度計每日Email")
    parser.add_argument("--send-test", action="store_true", help="只寄送設定測試信")
    args = parser.parse_args()
    return run(send_test=args.send_test)


if __name__ == "__main__":
    raise SystemExit(main())
