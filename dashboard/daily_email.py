from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
import html

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
) -> tuple[str, str, str]:
    """Render email from the same canonical events consumed by Streamlit/Sheet."""
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
    post_close = projection["post_close_forward_signals"]
    next_date = str(next_view.get("target_date") or "N/A")
    prod_bull = int(next_view["production_bullish_count"])
    prod_bear = int(next_view["production_bearish_count"])
    subject = (
        f"臺股市場溫度計｜資料日 {data_date.replace('-', '/')}｜"
        f"下一交易日 {next_date.replace('-', '/')}｜正式 {prod_bull}多 {prod_bear}空"
    )
    lines = [
        "01｜下一交易日預測",
        f"Target Date：{next_date}",
        f"狀態：{next_view['status']}",
        f"正式：{prod_bull} 多 / {prod_bear} 空",
        (
            f"研究：{next_view['research_bullish_count']} 多 / "
            f"{next_view['research_bearish_count']} 空（不計正式票）"
        ),
        f"方向未確認：{next_view['unconfirmed_direction_count']}",
    ]
    if next_view["ledger_completeness"]["warning"]:
        lines.append(f"完整性警示：{next_view['ledger_completeness']['warning']}")
    if not next_view["signals"]:
        lines.append("本目標日沒有命中的可見訊號；不代表市場中性或資料缺失。")
    for item in next_view["signals"]:
        lines.extend([
            f"[{item['direction_label']}] {item['signal_identity']}",
            f"  Signal Date {item['signal_date']}｜{item['horizon']}｜Target {item['target_date']}",
            f"  Raw {item['raw_value_display']}｜{item['normalized_value']}｜Threshold {item['threshold']}",
            f"  Mean {item['historical_mean_return']}｜Relative {item['relative_mean_return']}｜N {item['sample_size']}",
            f"  Evidence {item['evidence_grade']}｜{item['research_status']}｜Production Eligible {item['formal_vote_eligible']}",
        ])

    lines.extend(["", "02｜當個交易日驗證", f"Target Date：{data_date}", f"狀態：{validation_view['status']}"])
    if not validation_view["signals"]:
        lines.append("此交易日沒有需要驗證的正式預測，或 validation artifact 尚無對應事件。")
    for item in validation_view["signals"]:
        lines.extend([
            f"[{item['direction_label']}] {item['signal_identity']}｜Signal Date {item['signal_date']}｜O1→C{item['horizon']}",
            f"  Entry {item['entry_date']}｜Target {item['target_date']}｜{item['maturity']}｜{item['event_origin']}",
            f"  Actual {_value(item.get('actual_return'), True)}｜Directional {_value(item.get('directional_return'), True)}｜{item.get('outcome', 'N/A')}",
        ])

    lines.extend(["", "03｜當天收盤訊號指向未來", f"Signal Date：{post_close['signal_date']}"])
    if not post_close["targets"]:
        lines.append("有效資料已完成計算；本次沒有命中的正式或研究訊號（VALID_NO_SIGNAL）。")
    for group in post_close["signal_groups"]:
        lines.extend([
            f"{group['signal_identity']}｜Signal Date {group['signal_date']}｜{group['research_status']}",
            "  " + "；".join(
                f"O1→C{row['horizon']}→{row['target_date']}（{row['direction_label']}）"
                for row in group["horizons"]
            ),
        ])

    experimental = [
        item for target in post_close["targets"] for item in target["signals"]
        if not item["formal_vote_eligible"]
    ]
    lines.extend(["", "04｜Research / Experimental Details（不計正式票數）"])
    if not experimental:
        lines.append("本次沒有命中的 Research / Experimental 訊號。")
    for item in experimental:
        lines.extend([
            f"[{item['direction_label']}] {item['signal_identity']}｜{item['horizon']}｜{item['research_status']}",
            f"  定義：{item['metric_definition']}",
            f"  Raw {item['raw_value_display']}｜{item['normalized_value']}｜Threshold {item['threshold']}",
            f"  Mean {item['historical_mean_return']}｜Median {item['historical_median_return']}｜Win {item['historical_win_rate']}",
            f"  Relative {item['relative_mean_return']}｜Global FDR {item['global_fdr']}｜Family FDR {item['family_fdr']}｜Evidence {item['evidence_grade']}",
        ])

    calculation_timestamp = events[0].calculation_timestamp if events else "N/A"
    lines.extend([
        "", "05｜Run Information",
        f"Data Date：{data_date}",
        f"Calculation Timestamp：{calculation_timestamp}",
        f"Run ID：{run_id}",
        f"Git Commit：{git_commit}",
        f"Pipeline Status：{pipeline_status}",
        f"Publication Status：{publication_status}",
        "",
        "多空票數是研究條件計數，不是上漲機率、預測報酬或操作建議。",
        "Historical win rate 是研究樣本統計，不是本次預測正確機率；FDR 也不是命中率。",
    ])
    if dashboard_url:
        lines.append(f"Dashboard：{dashboard_url}")
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
