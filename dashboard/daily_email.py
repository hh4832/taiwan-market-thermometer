from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
import html

import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.data_service import load_live_breadth, load_live_futures
from dashboard.spot_flow_service import SpotFlowReport, load_live_spot_flow
from dashboard.research_evidence import build_daily_evidence_report
from dashboard.email_service import EmailSettings, send_gmail, simple_html

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "email_notification.json"
LOG_DIR = ROOT / "logs"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
KEYRING_SERVICE = "taiwan-market-thermometer"
TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")


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
        "法人現貨A級證據紅綠燈（research only；不提供操作建議）：",
        f"資料日 {spot.data_date}；偏多family {spot.bullish_family_count}個；偏空family {spot.bearish_family_count}個；混合family {spot.mixed_family_count}個；整體資料品質 {spot.data_quality}。",
        "燈號：🟢偏多A級命中；🔴偏空A級命中；⚪未命中；🟡資料不足或品質警告。",
    ]
    for item in spot.evidence:
        light, status, _ = _spot_light(item)
        lines.extend([
            f"{light} {item.label}｜{status}｜family={item.family}｜觀察期={item.horizon}",
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
        "<h3>法人現貨 A級證據紅綠燈</h3>"
        f"<p>資料日 {html.escape(spot.data_date)}；🟢偏多 family {spot.bullish_family_count}；"
        f"🔴偏空 family {spot.bearish_family_count}；混合 family {spot.mixed_family_count}。"
        "此區僅呈現研究證據，不影響既有綜合判讀。</p>"
        "<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#eef4f2'><th>燈號</th><th>定義</th><th>原始累積值</th><th>衍生指標</th><th>品質與證據</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _daily_evidence_sections(breadth: pd.DataFrame, futures: pd.DataFrame, spot: SpotFlowReport | None) -> tuple[list[str], str]:
    report = build_daily_evidence_report(breadth, futures, spot)
    lines = ["每日多空研究證據（A級正式、B級條件式；不提供操作建議）："]
    rows = []
    for summary in report.summaries:
        plain = f"今天 {summary.total_bullish} 多、{summary.total_bearish} 空"
        lines.append(f"{summary.horizon}｜A多 {summary.bullish_a}、A空 {summary.bearish_a}、B多 {summary.bullish_b}、B空 {summary.bearish_b}｜{plain}")
        rows.append("<tr>" + "".join(f"<td style='padding:8px;border:1px solid #d7dedc'>{html.escape(str(x))}</td>" for x in
            (summary.horizon, summary.bullish_a, summary.bearish_a, summary.bullish_b, summary.bearish_b, plain)) + "</tr>")
    cards = []
    for item in report.matched:
        light = "🟢" if item.direction == "bullish" else "🔴"
        lines.extend([f"{light} {item.level}級｜{item.horizon}｜{item.label}", f"  {item.plain_explanation}",
                      f"  原始資料：{item.raw_value}", f"  正式指標：{item.normalized_value}；門檻：{item.threshold}",
                      f"  歷史證據：{item.historical_result}"])
        scope = "<p><strong>注意：</strong>這是相對強弱證據，不等於絕對下跌。</p>" if item.evidence_scope == "relative" else ""
        cards.append(f"<div style='border:1px solid #d7dedc;padding:12px;margin:8px 0'><strong>{light} {item.level}級｜{html.escape(item.horizon)}｜{html.escape(item.label)}</strong><p>{html.escape(item.plain_explanation)}</p><p><b>原始資料：</b>{html.escape(item.raw_value)}</p><p><b>正式指標：</b>{html.escape(item.normalized_value)}；<b>門檻：</b>{html.escape(item.threshold)}</p><p><b>歷史證據：</b>{html.escape(item.historical_result)}</p>{scope}</div>")
    if not report.matched:
        lines.append("今天沒有命中研究表中的A／B級條件；這不代表市場中性。")
    for warning in report.warnings:
        lines.append("資料提醒：" + warning)
    table = ("<h3>每日多空研究證據</h3><p>A級＝正式證據；B級＝條件式證據。同一經濟訊號不同視窗只算一票。本區不提供操作建議。</p>"
             "<table style='border-collapse:collapse;width:100%'><tr><th>期間</th><th>A多</th><th>A空</th><th>B多</th><th>B空</th><th>白話統計</th></tr>" + "".join(rows) + "</table>" + "".join(cards))
    return lines, table


def build_daily_report(
    breadth: pd.DataFrame,
    futures: pd.DataFrame,
    now: datetime,
    spot: SpotFlowReport | None = None,
) -> tuple[str, str, str, bool]:
    breadth = breadth.sort_index()
    futures = futures.dropna(subset=["foreign_direction_score"]).sort_index()
    if breadth.empty or futures.empty:
        raise RuntimeError("今日市場廣度或外資期貨資料為空。")

    brow = breadth.iloc[-1]
    frow = futures.iloc[-1]
    bdate = pd.Timestamp(breadth.index[-1]).date()
    fdate = pd.Timestamp(futures.index[-1]).date()
    today = now.astimezone(TAIPEI).date()
    aligned_today = bdate == fdate == today
    quality_ok = bool(brow.get("breadth_quality_ok", False))
    conclusion = build_conclusion(
        float(brow.get("breadth_rebound_score", float("nan"))),
        float(frow["foreign_direction_score"]),
        bdate,
        fdate,
        float(brow.get("coverage_ratio", float("nan"))),
    )

    net_oi = float(frow["foreign_net_oi"])
    net_state = "淨多" if net_oi >= 0 else "淨空"
    breadth_anchor = "已觸發極端普跌5%條件" if quality_ok and float(brow["down_ratio"]) >= 0.845405 else "未觸發極端普跌5%條件"
    foreign_anchor = "已觸發外資極端往空方5%條件" if float(frow["foreign_direction_score"]) <= 5 else "未觸發外資極端往空方5%條件"
    status = "資料完成" if aligned_today else "資料日期未齊"
    subject = f"【臺股市場溫度計】{today}｜{status}｜{conclusion.overall_state}"

    lines = [
        f"資料狀態：{status}",
        f"市場廣度日期：{bdate}；外資期貨日期：{fdate}",
        f"綜合判讀：{conclusion.headline}（{conclusion.overall_state}）",
        f"參考行動：{conclusion.reference_action}",
        f"市場廣度：{float(brow.get('breadth_rebound_score', float('nan'))):.0f}/100；下跌比例 {_percent(float(brow['down_ratio']))}；{breadth_anchor}",
        f"外資方向：{float(frow['foreign_direction_score']):.1f}/100；OI Change Ratio {_percent(float(frow['foreign_oi_change_ratio']), 3)}；累積部位 {net_state} {abs(net_oi):,.0f}口；{foreign_anchor}",
        f"多單變化：{_percent(float(frow['foreign_long_change_ratio']), 3)}；空單變化：{_percent(float(frow['foreign_short_change_ratio']), 3)}",
        "研究提醒：整體OI Change Ratio最低5%的1、5、10、20日負向證據成立；多空拆解後，多單增加僅對隔日具正向證據，多單減少對10日相對偏弱，空單變化單獨看未通過FDR。",
        "法人資料於收盤後公布；統計報酬不等同可實現策略報酬，也不構成投資建議。",
        f"版本：v{VERSION}",
    ]
    if spot is None:
        lines.insert(-1, "法人現貨A級證據：未載入；不影響本次既有綜合判讀。")
    else:
        for line in reversed(_spot_plain_lines(spot)):
            lines.insert(-1, line)
    evidence_lines, evidence_html = _daily_evidence_sections(breadth, futures, spot)
    lines[-1:-1] = evidence_lines
    plain = "\n".join(lines)
    html_body = simple_html(subject, lines)
    if spot is not None:
        marker = f"<p>{html.escape('法人現貨A級證據紅綠燈（research only；不提供操作建議）：')}</p>"
        start = html_body.find(marker)
        if start >= 0:
            end = html_body.find(f"<p>{html.escape('版本：v' + VERSION)}</p>", start)
            if end >= 0:
                html_body = html_body[:start] + _spot_html(spot) + html_body[end:]
    version_marker = f"<p>{html.escape('版本：v' + VERSION)}</p>"
    html_body = html_body.replace(version_marker, evidence_html + version_marker)
    return subject, plain, html_body, aligned_today


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
        import finlab

        finlab.login(finlab_token)
        now = datetime.now(TAIPEI)
        subject, plain, html_body, aligned_today = build_daily_report(
            load_live_breadth(), load_live_futures(), now, load_live_spot_flow()
        )
        send_gmail(settings, subject, plain, html_body)
        logging.info("Daily report sent; aligned_today=%s", aligned_today)
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
