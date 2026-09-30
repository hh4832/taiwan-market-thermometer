"""Streamlit vNext — canonical forecast calendar and research evidence."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pandas as pd
import streamlit as st

from dashboard.dashboard_source import (
    CURRENT_RUN_DIR,
    DashboardSource,
    calculate_live_source,
    select_dashboard_source,
)
from dashboard.forecast_calendar import calendar_matrix, contributing_events
from dashboard.finlab_auth import headless_credentials_available
from dashboard.research_registry import CANONICAL_SIGNALS
from dashboard.signal_engine import events_frame, production_events
from dashboard.signal_presentation import event_audit_record, signal_summary_frame

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
def _load_best_source() -> DashboardSource:
    return select_dashboard_source(CURRENT_RUN_DIR)


def latest_signal_summary(events) -> pd.DataFrame:
    return signal_summary_frame(events)


def render_signal_details(events) -> None:
    """Render the same plain-language audit trail in both calendar views."""
    if not events:
        st.info("這個目標日目前沒有符合正式計票條件的訊號。")
        return
    for event in events:
        audit = event_audit_record(event)
        label = f"{audit['direction']}｜{audit['plain_definition']}｜{audit['signal_date']} → {audit['target_date']}"
        with st.expander(label):
            st.write(audit["plain_definition"])
            st.write(f"原始資料：{audit['指標原始值']}")
            st.write(f"標準化指標：{audit['normalized_value']}｜門檻：{audit['threshold']}")
            st.write(
                f"證據：平均 {audit['historical_mean_return']}、中位數 {audit['historical_median_return']}、"
                f"勝率 {audit['historical_win_rate']}、樣本 {audit['sample_size']}、Global FDR {audit['global_fdr']}"
            )
            st.caption(
                f"Evidence {audit['evidence_grade']}｜Research {audit['research_status']}｜"
                f"Origin {audit['event_origin']}｜計算時間 {audit['calculation_timestamp']}"
            )


def recent_target_calendar(calendar: pd.DataFrame, data_date: str | None) -> pd.DataFrame:
    if calendar.empty or not data_date:
        return calendar
    dates = pd.to_datetime(calendar["target_date"], errors="coerce")
    cutoff = pd.Timestamp(data_date)
    past = calendar.loc[dates <= cutoff].tail(5)
    future = calendar.loc[dates > cutoff]
    return pd.concat([past, future]).drop_duplicates("target_date")


def historical_validation_view(events, adjusted_open=None, adjusted_close=None) -> pd.DataFrame:
    frame = events_frame(production_events(events))
    if frame.empty:
        return pd.DataFrame(columns=["signal_date", "signal_id", "direction", "horizon", "target_date", "actual_return", "maturity"])
    frame["actual_return"] = None
    frame["maturity"] = "PENDING"
    return frame[["signal_date", "signal_id", "direction", "horizon", "target_date", "actual_return", "maturity"]]


def system_health_view(source: DashboardSource, events) -> pd.DataFrame:
    sources = {event.source for event in events if event.evaluation_status not in {"DATA_UNAVAILABLE", "UNVALIDATED_ACTIVATION_RULE"}}
    return pd.DataFrame([
        {"項目": "Market Breadth", "狀態": "READY" if "market_breadth" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Futures", "狀態": "READY" if "futures" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Spot Institutional", "狀態": "READY" if "spot_flow" in sources else "DATA_UNAVAILABLE"},
        {"項目": "Margin / Short", "狀態": "UNVALIDATED_ACTIVATION_RULE"},
        {"項目": "資料來源", "狀態": source.source_type},
        {"項目": "Pipeline", "狀態": source.status},
        {"項目": "評估結果", "狀態": source.evaluation_result},
        {"項目": "Freshness", "狀態": source.freshness},
        {"項目": "FinLab credential", "狀態": "AVAILABLE" if headless_credentials_available() else "UNAVAILABLE"},
    ])


st.set_page_config(page_title="臺股市場溫度計 vNext", page_icon="🌡️", layout="wide")
st.title("臺股市場溫度計 vNext")
st.caption("研究證據行事曆；只呈現 RETAINED 正式票數，不提供操作建議。")

source = _load_best_source()
if st.button("重新從 FinLab 更新", type="primary", help="只有手動要求時才重新下載；平時直接使用同一次 run 的 artifacts。"):
    try:
        with st.spinner("正在取得 FinLab 並重建 current-run artifacts…"):
            source = calculate_live_source(CURRENT_RUN_DIR)
            _load_best_source.clear()
        st.success("Live FinLab 更新成功，已切換至新的 current run。")
    except Exception as exc:
        st.error(f"Refresh failed：{type(exc).__name__}: {exc}。目前畫面仍顯示原資料來源 {source.source_type}。")

source_names = {
    "CURRENT_RUN": "Current Colab / Production Run",
    "LIVE_FINLAB": "Live FinLab",
    "RESEARCH_SNAPSHOT": "Research Snapshot",
}
st.subheader("資料來源與執行狀態")
provenance = st.columns(3)
provenance[0].metric("資料來源", source_names.get(source.source_type, source.source_type))
provenance[1].metric("資料日期 / Signal Date", source.data_date or "無法判定")
provenance[2].metric("Freshness", source.freshness)
st.caption(
    f"計算時間：{source.calculated_at or '無法判定'} ｜ Run ID：{source.run_id or '無'} ｜ "
    f"Git commit：{source.git_commit or '無法判定'} ｜ Pipeline：{source.status}"
)
if source.warning:
    st.warning(source.warning)

events = source.events
calendar = source.calendar
ledger_events = source.ledger_events or events
target_calendar = recent_target_calendar(source.target_calendar, source.data_date)

tabs = st.tabs(["Forecast Calendar", "Today / Latest Signals", "Historical Validation", "Research Evidence", "System / Run Health"])

with tabs[0]:
    view = st.radio("檢視方式", ["Target-Date View", "Forward View"], horizontal=True)
    shown_calendar = target_calendar if view == "Target-Date View" else calendar
    st.subheader("目標日證據行事曆" if view == "Target-Date View" else "當次訊號未來行事曆")
    if view == "Target-Date View":
        st.caption("同一目標日會彙整過去不同訊號日、不同 horizon 的正式 RETAINED 證據；不同 signal vintage 不會互相去重。")
    else:
        st.caption(f"Signal Date：{source.data_date or '無法判定'}；下列日期是各 O1→Cn 的 Target Trading Date，不是資料日期。")
    if shown_calendar.empty:
        if source.evaluation_result == "VALID_NO_SIGNAL":
            st.info("VALID_NO_SIGNAL：計算已成功完成，今日為 0 多 / 0 空；這不代表市場中性。")
        else:
            st.error(f"{source.evaluation_result}：必要資料不足，不能解讀為 0 多 / 0 空。")
    else:
        cards = st.columns(min(5, len(shown_calendar)))
        for index, row in enumerate(shown_calendar.tail(20).to_dict("records")):
            cards[index % len(cards)].metric(str(row["target_date"]), f"{row['bullish_count']} 多 / {row['bearish_count']} 空", f"淨票 {row['net_vote']:+d}")
        if view == "Target-Date View":
            st.dataframe(shown_calendar, hide_index=True, use_container_width=True)
            selected_date = st.selectbox("查看目標日證據", shown_calendar["target_date"].astype(str).tolist(), index=len(shown_calendar) - 1)
            render_signal_details(contributing_events(ledger_events, selected_date))
        else:
            st.dataframe(calendar_matrix(events), use_container_width=True)
            selected_date = st.selectbox("查看目標日證據", calendar["target_date"].astype(str).tolist())
            render_signal_details(contributing_events(events, selected_date))
        st.caption("淨票只是多空條件數量差，不是報酬、機率或信心百分比。")

with tabs[1]:
    st.subheader("最新訊號")
    st.dataframe(source.latest_summary, hide_index=True, use_container_width=True)
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
    st.dataframe(system_health_view(source, events), hide_index=True, use_container_width=True)
    st.code(
        f"version={VERSION}\ngit_commit={source.git_commit or _commit()}\n"
        f"run_id={source.run_id or 'none'}\ndata_date={source.data_date or 'unknown'}\n"
        f"calculation_timestamp={source.calculated_at or 'unknown'}\n"
        f"source={source.source_type}\nfreshness={source.freshness}\npipeline_status={source.status}"
        f"\nevaluation_result={source.evaluation_result}"
    )
    st.caption("Margin / Short 已納入研究 registry；production hard cutoff 尚未驗證，因此不投票。")
