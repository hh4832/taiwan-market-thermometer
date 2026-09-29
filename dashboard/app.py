"""Streamlit vNext — canonical forecast calendar and research evidence."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess

import pandas as pd
import streamlit as st

from dashboard.canonical_pipeline import build_canonical_events
from dashboard.data_service import load_dashboard_data
from dashboard.forecast_calendar import build_forecast_calendar, calendar_matrix
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import events_frame, production_events
from dashboard.trading_calendar import extend_future_sessions

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unavailable"


def _unknown(value: object, percent: bool = False) -> str:
    if value is None or pd.isna(value):
        return "無法判定"
    return f"{float(value):.2%}" if percent else str(value)


@st.cache_data(ttl=1800, show_spinner=False)
def _load(use_finlab: bool):
    return load_dashboard_data(use_finlab=use_finlab)


def latest_signal_summary(events) -> pd.DataFrame:
    frame = events_frame(events)
    if frame.empty:
        return frame
    return frame[["source", "plain_definition", "direction", "horizon", "matched", "evaluation_status", "research_status", "target_date"]]


def historical_validation_view(events, adjusted_open=None, adjusted_close=None) -> pd.DataFrame:
    frame = events_frame(production_events(events))
    if frame.empty:
        return pd.DataFrame(columns=["signal_date", "signal_id", "direction", "horizon", "target_date", "actual_return", "maturity"])
    frame["actual_return"] = None
    frame["maturity"] = "PENDING"
    return frame[["signal_date", "signal_id", "direction", "horizon", "target_date", "actual_return", "maturity"]]


def system_health_view(data, events) -> pd.DataFrame:
    sources = {event.source for event in events if event.evaluation_status not in {"DATA_UNAVAILABLE", "UNVALIDATED_ACTIVATION_RULE"}}
    return pd.DataFrame([
        {"項目": "Market Breadth", "狀態": "READY" if "market_breadth" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Futures", "狀態": "READY" if "futures" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Spot Institutional", "狀態": "READY" if "spot_flow" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Margin / Short", "狀態": "UNVALIDATED_ACTIVATION_RULE"},
        {"項目": "資料來源", "狀態": data.source},
    ])


st.set_page_config(page_title="臺股市場溫度計 vNext", page_icon="🌡️", layout="wide")
st.title("臺股市場溫度計 vNext")
st.caption("研究證據行事曆；只呈現 RETAINED 正式票數，不提供操作建議。")

use_finlab = st.toggle("使用 FinLab 更新資料", value=False, help="需要已設定 FINLAB_API_TOKEN。研究快照僅供介面預覽。")
if st.button("更新資料", type="primary"):
    _load.clear()
data = _load(use_finlab)

if data.error:
    st.error(f"DATA_UNAVAILABLE：{data.error}")

breadth = data.breadth
futures = data.futures if data.futures is not None else pd.DataFrame(index=breadth.index)
sessions = extend_future_sessions(breadth.index)
events = build_canonical_events(breadth, futures, data.spot, sessions, pd.Timestamp.now(tz="Asia/Taipei"))
calendar = build_forecast_calendar(events)

tabs = st.tabs(["Forecast Calendar", "Today / Latest Signals", "Historical Validation", "Research Evidence", "System / Run Health"])

with tabs[0]:
    st.subheader("未來交易日預測行事曆")
    if calendar.empty:
        st.info("VALID_NO_SIGNAL：有效資料已完成評估，但沒有命中 RETAINED 訊號；這不代表市場中性。")
    else:
        cards = st.columns(min(5, len(calendar)))
        for index, row in enumerate(calendar.head(20).to_dict("records")):
            cards[index % len(cards)].metric(str(row["target_date"]), f"{row['bullish_count']} 多 / {row['bearish_count']} 空", f"淨票 {row['net_vote']:+d}")
        st.dataframe(calendar_matrix(events), use_container_width=True)
        st.caption("淨票只是多空條件數量差，不是報酬、機率或信心百分比。")

with tabs[1]:
    st.subheader("最新訊號")
    st.dataframe(latest_signal_summary(events), hide_index=True, use_container_width=True)
    st.caption("NO_SIGNAL 與 DATA_UNAVAILABLE 分開顯示；RETEST 不計正式票數。")

with tabs[2]:
    st.subheader("歷史驗證")
    st.dataframe(historical_validation_view(events), hide_index=True, use_container_width=True)
    st.info("Outcome 尚未成熟時顯示 PENDING，不會寫成 0。正式 outcome 定義為 adjusted O1→Cn。")

with tabs[3]:
    st.subheader("Canonical Research Registry")
    status = st.multiselect("Research status", ["RETAINED", "RETEST", "REJECTED"], default=["RETAINED"])
    registry = pd.DataFrame([signal.as_dict() for signal in CANONICAL_SIGNALS])
    st.dataframe(registry[registry["research_status"].isin(status)], hide_index=True, use_container_width=True)
    st.caption("無法由 canonical output 驗證的欄位保留空值；不以 0 取代。")

with tabs[4]:
    st.subheader("System / Run Health")
    st.dataframe(system_health_view(data, events), hide_index=True, use_container_width=True)
    st.code(f"version={VERSION}\ngit_commit={_commit()}\ncalculation_timestamp={datetime.now().astimezone().isoformat()}")
    st.caption("Margin / Short 已納入研究 registry；production hard cutoff 尚未驗證，因此不投票。")
