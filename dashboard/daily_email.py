from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys

import pandas as pd

from dashboard.conclusion_engine import build_conclusion
from dashboard.data_service import load_live_breadth, load_live_futures
from dashboard.spot_flow_service import SpotFlowReport, load_live_spot_flow
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
        matched = [item for item in spot.evidence if item.a_grade_status == "matched"]
        lines.insert(-1, "法人現貨A級證據監測（research only；不提供操作建議）：")
        lines.insert(-1, f"偏多family {spot.bullish_family_count}個；偏空family {spot.bearish_family_count}個；混合family {spot.mixed_family_count}個；狀態 {spot.family_state}。")
        if matched:
            for item in matched:
                lines.insert(-1, f"- {item.label}：{item.evidence_statement}")
        else:
            lines.insert(-1, "- 今日沒有符合Phase 2 A級法人現貨條件；不等於市場中性。")
    plain = "\n".join(lines)
    html_body = simple_html(subject, lines)
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
