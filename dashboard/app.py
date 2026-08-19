from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

# Streamlit在Windows上可能只把dashboard/加入模組搜尋路徑。
# 主動加入專案根目錄，讓以下dashboard.*匯入不受啟動方式或目前目錄影響。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from dashboard.conclusion_engine import build_conclusion
from dashboard.data_service import load_dashboard_data


st.set_page_config(page_title="臺股市場溫度計", page_icon="🌡️", layout="wide")
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {background:#081113;color:#dce9e8}
[data-testid="stHeader"], [data-testid="stToolbar"], .stDeployButton {display:none !important}
[data-testid="stDecoration"], [data-testid="stStatusWidget"] {display:none !important}
.block-container {max-width:1400px;padding-top:1.2rem}
.tm-card {border:1px solid #26383a;background:#101b1d;padding:1.2rem;margin:.4rem 0}
.tm-kicker {color:#43e0c4;font:700 .68rem monospace;letter-spacing:.16em}
.tm-title {font-size:1.55rem;font-weight:700;margin:.3rem 0}
.tm-muted {color:#839695}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_data(use_finlab: bool):
    return load_dashboard_data(use_finlab=use_finlab)


header_left, header_right = st.columns([3, 1])
with header_left:
    st.caption("QUANT RESEARCH DESK")
    st.title("臺股市場溫度計")
with header_right:
    refresh = st.button("更新 FinLab 資料", use_container_width=True, type="primary")

if refresh:
    cached_data.clear()
data = cached_data(use_finlab=refresh)
if data.error:
    st.warning(f"FinLab更新失敗，已保留研究快照：{data.error}")

breadth = data.breadth.sort_index()
brow = breadth.iloc[-1]
bdate = pd.Timestamp(breadth.index[-1]).date()
breadth_quality_ok = bool(brow.get("breadth_quality_ok", False))
futures = None if data.futures is None else data.futures.dropna(subset=["foreign_direction_score"]).sort_index()
frow = None if futures is None or futures.empty else futures.iloc[-1]
fdate = None if futures is None or futures.empty else pd.Timestamp(futures.index[-1]).date()

conclusion = build_conclusion(
    float(brow.get("breadth_rebound_score", float("nan"))),
    None if frow is None else float(frow["foreign_direction_score"]),
    bdate,
    fdate,
    float(brow.get("coverage_ratio", float("nan"))),
)

st.markdown(f"""<div class="tm-card"><div class="tm-kicker">COMBINED VIEW</div><div class="tm-title">{conclusion.headline}</div><div>{conclusion.overall_state}</div><p class="tm-muted">{conclusion.breadth_summary} {conclusion.foreign_summary}</p><strong>{conclusion.reference_action}</strong><p class="tm-muted">{conclusion.risk_warning}</p></div>""", unsafe_allow_html=True)

left, right = st.columns(2)
with left:
    st.subheader("超跌反彈溫度")
    st.caption("主要觀察期：未來5～10日")
    if not breadth_quality_ok or pd.isna(brow.get("breadth_rebound_score")):
        st.metric("即時無前視百分位", "資料不足", f"原始下跌比例 {brow['down_ratio']:.2%}")
        st.error("當日股票分類或覆蓋率不足，停止市場廣度與綜合結論判讀。")
    else:
        st.metric("即時無前視百分位", f"{brow['breadth_rebound_score']:.0f} / 100", f"下跌比例 {brow['down_ratio']:.2%}")
        st.progress(max(0.0, min(1.0, float(brow["breadth_rebound_score"]) / 100)))
    if breadth_quality_ok and brow["down_ratio"] >= 0.845405:
        st.error("極端普跌／完整樣本最差5%")
    elif breadth_quality_ok and brow["down_ratio"] >= 0.685541:
        st.warning("明顯普跌／完整樣本最差20%")
    elif breadth_quality_ok:
        st.success("一般市場廣度")
    st.line_chart(breadth[["breadth_rebound_score"]].tail(60), height=220)

with right:
    st.subheader("外資期貨方向溫度")
    st.caption("主要觀察期：隔日")
    if frow is None:
        st.metric("252日無前視百分位", "—")
        st.info("按下更新並完成FinLab登入後，才會產生外資期貨方向溫度。")
    else:
        st.metric("252日無前視百分位", f"{frow['foreign_direction_score']:.0f} / 100", f"OI Change Ratio {frow['foreign_oi_change_ratio']:.3%}")
        st.progress(max(0.0, min(1.0, float(frow["foreign_direction_score"]) / 100)))
        st.line_chart(futures[["foreign_direction_score"]].tail(60), height=220)

with st.expander("市場廣度 Raw 資訊", expanded=False):
    metrics = st.columns(4)
    rows = [
        ("上漲家數", f"{int(brow['up_count']):,}"), ("下跌家數", f"{int(brow['down_count']):,}"),
        ("平盤家數", f"{int(brow['flat_count']):,}"), ("有效股票數", f"{int(brow['valid_count']):,}"),
        ("資料覆蓋率", f"{brow['coverage_ratio']:.2%}"), ("上漲比例", f"{brow['up_ratio']:.2%}"),
        ("下跌比例", f"{brow['down_ratio']:.2%}"), ("Breadth Net Ratio", f"{brow['breadth_net_ratio']:+.4f}"),
        ("待分類家數", f"{int(brow.get('unclassified_count', 0)):,}"),
        ("分類完整率", f"{brow.get('classification_ratio', float('nan')):.2%}"),
    ]
    for index, (label, value) in enumerate(rows):
        metrics[index % 4].metric(label, value)
    if not breadth_quality_ok:
        st.error("資料品質檢查未通過：有效覆蓋率需≥80%，且分類完整率需≥99%。本日不產生市場廣度訊號。")
    st.dataframe(breadth.tail(20), use_container_width=True)

with st.expander("外資期貨 Raw 資訊", expanded=False):
    if frow is None:
        st.info("尚無同日FinLab原始資料。")
    else:
        wanted = [column for column in futures.columns if column.startswith("foreign_")]
        st.dataframe(futures[wanted].tail(20), use_container_width=True)

st.divider()
st.subheader("研究錨點")
research = st.columns(4)
research[0].metric("極端5%樣本", "189次")
research[1].metric("隔日開盤→第5日", "+0.57%", "勝率56.1%")
research[2].metric("隔日開盤→第10日", "+0.88%", "勝率63.1%")
research[3].metric("證據定位", "臨界", "非單獨買進訊號")
st.caption(f"資料來源：{data.source}｜市場廣度日期：{bdate}｜外資期貨日期：{fdate or '等待更新'}｜頁面產生：{datetime.now():%Y-%m-%d %H:%M}")
