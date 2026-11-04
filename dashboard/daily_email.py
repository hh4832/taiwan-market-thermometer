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
    build_live_track_record,
    formal_hit_records,
    load_mechanism_groups,
    market_position,
    status_changes,
)
from dashboard.signal_presentation import signal_display_name

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


def _report_pct(value: object, *, points: bool = False) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number * 100:+.1f}{'pp' if points else '%'}"


def _direction(direction: object) -> str:
    return {"bullish": "偏多", "bearish": "偏空"}.get(str(direction), "方向未確認")


def _hit_summary(records: pd.DataFrame, limit: int | None = None) -> str:
    if records.empty:
        return "—（n=0）"
    selected = records.sort_values(
        ["target_date", "signal_date", "signal_id"], ascending=False
    )
    if limit is not None:
        selected = selected.head(limit)
    n = len(selected)
    hits = int(selected["hit"].sum())
    return f"{hits / n:.1%}（{hits}/{n}，n={n}）"


def _observation_rows(
    current_events: tuple[SignalEvent, ...],
    ledger_events: tuple[SignalEvent, ...],
    trading_sessions: pd.Index | list[object] | None,
    track_record: pd.DataFrame,
) -> tuple[list[str], list[str]]:
    groups = load_mechanism_groups()
    retest = [
        event for event in current_events
        if event.research_status == "RETEST" and event.matched
    ]
    grouped: dict[str, dict[str, list[SignalEvent]]] = {
        name: {} for name in ("宣洩/反向", "趨勢/參與", "其他", "未分類")
    }
    for event in retest:
        mechanism = groups.get(event.economic_signal_id, {}).get("group", "未分類")
        grouped.setdefault(mechanism, {}).setdefault(event.economic_signal_id, []).append(event)

    episode_counts: dict[str, int] = {}
    if trading_sessions is not None:
        episode_frame, event_episode = build_episode_index(ledger_events, trading_sessions)
        if not episode_frame.empty:
            episode_counts = episode_frame.groupby("signal_id").size().to_dict()
    else:
        event_episode = {}

    track_lookup: dict[tuple[str, str], dict[str, object]] = {}
    for row in track_record.to_dict("records"):
        track_lookup[(str(row.get("signal_id")), str(row.get("episode_start")))] = row

    lines: list[str] = []
    alerts: list[str] = []
    for mechanism in ("宣洩/反向", "趨勢/參與", "其他", "未分類"):
        signals = grouped.get(mechanism, {})
        lines.append(f"{mechanism}｜{len(signals)} 項觀察")
        for economic_id, items in sorted(signals.items()):
            items = sorted(items, key=lambda event: (event.horizon, event.signal_id))
            directions = {event.direction for event in items}
            direction_text = (
                f"方向分歧（{'/'.join(str(event.horizon) for event in items)} 日）" if len(directions) != 1 else
                "/".join(str(event.horizon) for event in items) + " 日一致" + _direction(items[0].direction)
            )
            config = groups.get(economic_id, {})
            name = config.get("name") or signal_display_name(items[0])
            history_parts = []
            grade_parts = []
            count_parts = []
            live_parts = []
            for event in items:
                history_parts.append(
                    f"C{event.horizon} {_report_pct(event.historical_mean_return)} / {_report_pct(event.relative_mean_return, points=True)}"
                )
                grade_parts.append(str(event.evidence_grade or "—"))
                count = episode_counts.get(event.signal_id)
                count_parts.append(f"C{event.horizon}:{count if count is not None else '—'}")
                start = event_episode.get((event.signal_id, event.signal_date))
                active = track_lookup.get((event.signal_id, start or ""))
                if active is None:
                    live_parts.append(f"C{event.horizon}:—")
                elif active.get("status") == "OPEN":
                    live_parts.append(
                        f"C{event.horizon}:進行中 {_report_pct(active.get('mark_to_market_return'))}"
                    )
                elif active.get("status") == "DUE":
                    if active.get("calculation_status") != "READY":
                        live_parts.append(f"C{event.horizon}:已到期 —")
                    else:
                        icon = "✅" if active.get("hit") is True else "❌"
                        live_parts.append(
                            f"C{event.horizon}:已到期{icon} {_report_pct(active.get('realized_return'))}"
                        )
                else:
                    live_parts.append(f"C{event.horizon}:—")
                if start == event.signal_date:
                    alert = f"首次觸發：{name}"
                    if alert not in alerts:
                        alerts.append(alert)
            lines.append(
                f"{name}｜{direction_text}｜平均/基準差 { '; '.join(history_parts) }｜"
                f"{','.join(sorted(set(grade_parts)))}｜事件 {','.join(count_parts)}｜實盤 {','.join(live_parts)}"
            )
    return lines, alerts


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
    """Render a concise decision-first daily report without changing signal rules."""
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
    validation_view = projection["current_day_validation"]
    prod_bull = int(next_view["production_bullish_count"])
    prod_bear = int(next_view["production_bearish_count"])
    net_vote = prod_bull - prod_bear
    subject = f"【{pd.Timestamp(data_date):%m/%d} 台股溫度計】"
    prices = market_position(adjusted_close)
    historical = historical_validation if historical_validation is not None else empty_historical_validation()
    hit_records = formal_hit_records(historical, data_date)
    track = live_track_record
    if track is None:
        track, _ = build_live_track_record(
            target_events, adjusted_open, adjusted_close,
            trading_sessions if trading_sessions is not None else (),
            data_date,
        ) if trading_sessions is not None else (pd.DataFrame(), {})
    lines = [
        subject,
        "① 結論",
        f"{(snapshot or {}).get('overall_state', '—')}；正式淨票 {net_vote:+d}（{prod_bull} 多／{prod_bear} 空）",
        f"0050 距 60 日高點 {_report_pct(prices['distance_from_60d_high'])}；MA60 {prices['vs_ma60'] or '—'}／MA120 {prices['vs_ma120'] or '—'}",
        "② 昨日到期驗證",
    ]
    due = validation_view["signals"]
    if due:
        for item in due:
            outcome = item.get("outcome")
            icon = "✅" if outcome == "HIT" else "❌" if outcome in {"MISS", "FLAT"} else "—"
            lines.append(
                f"{_direction(item.get('direction'))}｜{item.get('signal_identity', '—')}｜"
                f"C{item.get('horizon', '—')}｜{_report_pct(item.get('actual_return'))}｜{icon}"
            )
    else:
        lines.append("今日無到期正式預測")
    lines.append(
        f"正式票方向命中率：近 20 次 {_hit_summary(hit_records, 20)}；全部 {_hit_summary(hit_records)}"
    )

    lines.append("③ 觀察名單（RETEST／非正式，不計票）")
    observations, trigger_alerts = _observation_rows(
        events, target_events or events, trading_sessions, track
    )
    lines.extend(observations)
    alerts = status_changes(previous_events, events) + trigger_alerts
    if publication_status != "SUCCESS":
        alerts.append(f"Publication Status={publication_status}")
    if alerts:
        lines.append("⚠️ " + "；".join(dict.fromkeys(alerts)))

    lines.append("④ 明細")
    if spreadsheet_url:
        lines.append(f"試算表：{spreadsheet_url}")
    else:
        lines.append("試算表：—")
    available_dates = sorted({
        str(pd.Timestamp(event.signal_date).date())
        for event in (target_events if target_events is not None else events)
        if event.signal_date
    })
    if available_dates:
        lines.append(f"episodes 統計範圍：{available_dates[0]}–{data_date}（signal_events 可用日期）")
    else:
        lines.append("episodes 統計範圍：—")
    lines.append("完整 variants、FDR、threshold 見 signal_events 分頁")
    if sheet_write_note:
        lines.append(f"寫入統計：{sheet_write_note}")
    if dashboard_url:
        lines.append(f"儀表板：{dashboard_url}")
    lines.append("多空票數不是上漲機率；歷史勝率不是本次命中機率。")
    plain = "\n".join(lines)
    html_body = simple_html(subject, lines)
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
