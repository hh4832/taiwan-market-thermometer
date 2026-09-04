from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

# Streamlit在Windows上可能只把dashboard/加入模組搜尋路徑。
# 主動加入專案根目錄，讓以下dashboard.*匯入不受啟動方式或目前目錄影響。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APP_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()

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
.tm-gauge {margin:.8rem 0 1rem}
.tm-gauge-track {height:12px;border-radius:999px;background:linear-gradient(90deg,#285b73 0%,#3b82a0 40%,#d4a843 80%,#d75b4f 100%);position:relative}
.tm-gauge-marker {position:absolute;top:-5px;width:4px;height:22px;background:#f4fbfa;border:1px solid #071113;box-shadow:0 0 0 2px rgba(67,224,196,.35)}
.tm-gauge-labels {display:flex;justify-content:space-between;color:#839695;font-size:.75rem;margin-top:.4rem}
.tm-gauge-note {color:#9eb0af;font-size:.8rem;margin-top:.5rem}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_data(use_finlab: bool):
    return load_dashboard_data(use_finlab=use_finlab)


def render_percentile_gauge(score: float, labels: tuple[str, str, str]) -> None:
    """百分位位置刻度；不是載入進度或勝率。"""
    bounded = max(0.0, min(100.0, float(score)))
    st.markdown(
        f"""<div class="tm-gauge" aria-label="歷史百分位 {bounded:.0f} 分">
        <div class="tm-gauge-track"><span class="tm-gauge-marker" style="left:calc({bounded:.2f}% - 2px)"></span></div>
        <div class="tm-gauge-labels"><span>0｜{labels[0]}</span><span>50｜{labels[1]}</span><span>100｜{labels[2]}</span></div>
        <div class="tm-gauge-note">標記位置＝歷史百分位 {bounded:.0f}；不是完成進度，也不是預測勝率。</div>
        </div>""",
        unsafe_allow_html=True,
    )


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
spot = data.spot

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
        render_percentile_gauge(float(brow["breadth_rebound_score"]), ("下跌程度低", "一般", "極端普跌"))
    if breadth_quality_ok and brow["down_ratio"] >= 0.845405:
        st.error("極端普跌／完整樣本最差5%")
    elif breadth_quality_ok and brow["down_ratio"] >= 0.685541:
        st.warning("明顯普跌／完整樣本最差20%")
    elif breadth_quality_ok:
        st.success("一般市場廣度")
    st.line_chart(breadth[["breadth_rebound_score"]].tail(60), height=220)

with right:
    st.subheader("外資期貨方向溫度")
    st.caption("主要觀察期：隔日為主；極端往空方時可延伸至20日")
    if frow is None:
        st.metric("252日無前視百分位", "—")
        st.info("按下更新並完成FinLab登入後，才會產生外資期貨方向溫度。")
    else:
        st.metric("252日無前視百分位", f"{frow['foreign_direction_score']:.0f} / 100", f"OI Change Ratio {frow['foreign_oi_change_ratio']:.3%}")
        render_percentile_gauge(float(frow["foreign_direction_score"]), ("往空方調整", "中性", "往多方調整"))
        net_oi = float(frow["foreign_net_oi"])
        net_state = "淨多" if net_oi >= 0 else "淨空"
        st.caption(f"方向分數衡量今日部位變化，不代表目前已是淨多；目前累積部位為{net_state} {abs(net_oi):,.0f}口。")
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
st.subheader("法人現貨A級證據監測")
st.caption("只顯示原始數據、正式定義及A級條件是否命中；研究監測用途，不提供操作建議，也不影響上方綜合判讀。")
if spot is None:
    st.info("按下「更新 FinLab 資料」後才會計算法人現貨A級條件；研究快照不產生現貨判讀。")
else:
    state_label = {
        "mixed": "證據方向不一致",
        "bullish_evidence": "僅出現偏多A級證據",
        "bearish_evidence": "僅出現偏空A級證據",
        "no_a_grade_match": "今日沒有符合A級條件",
    }.get(spot.family_state, "資料不足")
    summary = st.columns(5)
    summary[0].metric("資料日期", spot.data_date)
    summary[1].metric("A級偏多 family", str(spot.bullish_family_count))
    summary[2].metric("A級偏空 family", str(spot.bearish_family_count))
    summary[3].metric("A級混合 family", str(spot.mixed_family_count))
    summary[4].metric("證據狀態", state_label)
    if spot.family_state == "mixed":
        st.warning("今日A級法人現貨證據方向不一致；不同family不互相抵銷，也不合成單一方向。")
    elif spot.family_state == "no_a_grade_match":
        st.info("今日沒有符合Phase 2 A級法人現貨條件；這不等於市場中性，也不代表未來不會漲跌。")

    status_labels = {
        "matched": "符合",
        "not_matched": "不符合",
        "insufficient_data": "資料不足",
        "suspended": "暫停判讀",
    }
    direction_labels = {"bullish": "偏多證據", "bearish": "偏空證據"}
    records = []
    for item in spot.evidence:
        records.append({
            "Signal family": item.family,
            "正式定義": item.label,
            "A級條件": status_labels.get(item.a_grade_status, item.a_grade_status),
            "方向": direction_labels.get(item.direction, item.direction),
            "觀察期": item.horizon,
            "累積日數": item.accumulation_days,
            "參考視窗": item.reference_window,
            "目前比例": item.current_value,
            "無前視PR": item.percentile,
            "資料品質": item.data_quality,
        })
    st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
    matched = [item for item in spot.evidence if item.a_grade_status == "matched"]
    if matched:
        for item in matched:
            st.markdown(f"**{item.label}｜{direction_labels[item.direction]}｜{item.horizon}**")
            st.write(item.evidence_statement)
    with st.expander("法人現貨 Raw 資訊", expanded=False):
        raw_records = []
        for item in spot.evidence:
            raw_records.append({
                "定義": item.label,
                "BuyAmount（累積）": item.raw_buy_amount,
                "SellAmount（累積）": item.raw_sell_amount,
                "MarketTurnover（累積）": item.market_turnover,
                "正式比例": item.current_value,
                "quality_flags": ";".join(item.quality_flags),
                "research_only": item.research_only,
            })
        st.dataframe(pd.DataFrame(raw_records), hide_index=True, use_container_width=True)
    st.caption("同一family即使多個視窗同時命中，也只計為一個family；不建立法人現貨溫度分數、權重、預期報酬或交易行動。")

st.divider()
st.subheader("市場廣度研究錨點")
st.caption("固定條件：下跌比例 ≥ 84.54%（完整樣本最極端5%）；只看市場廣度，與外資期貨分數無關。")
if not breadth_quality_ok:
    st.error("今日市場廣度資料品質不足，無法判斷是否觸發研究錨點。")
elif float(brow["down_ratio"]) >= 0.845405:
    st.warning(f"今日已觸發極端5%條件：下跌比例 {brow['down_ratio']:.2%}。以下歷史統計可作為反彈環境參考。")
else:
    st.info(f"今日未觸發極端5%條件：下跌比例 {brow['down_ratio']:.2%} < 84.54%。以下數字僅為歷史研究背景，不套用為今日預期報酬。")
research = st.columns(4)
research[0].metric("極端5%樣本", "189次")
research[1].metric("隔日開盤→第5日", "+0.57%", "勝率56.1%")
research[2].metric("隔日開盤→第10日", "+0.88%", "勝率63.1%")
research[3].metric("證據定位", "臨界", "非單獨買進訊號")

st.subheader("外資期貨研究錨點")
st.caption("固定條件：OI Change Ratio的252日無前視百分位 ≤ 5%；只適用於極端往空方調整，不代表一般低分或往多方移動具有相同的5～20日效果。")
if frow is None:
    st.info("尚無同日外資期貨資料，無法判斷是否觸發研究錨點。")
elif float(frow["foreign_direction_score"]) <= 5:
    st.error(
        f"今日已觸發極端往空方條件：百分位 {frow['foreign_direction_score']:.1f}。"
        "歷史上負向差異可延續至5～20日，但仍須等待可交易時間驗證。"
    )
else:
    st.info(
        f"今日未觸發極端往空方條件：百分位 {frow['foreign_direction_score']:.1f} > 5。"
        "以下數字僅為歷史研究背景，不套用為今日預期報酬。"
    )
foreign_research = st.columns(5)
foreign_research[0].metric("極端空方樣本", "239次", "PR 0～5")
foreign_research[1].metric("未來1日", "−0.33%", "相對其他日 −0.41%")
foreign_research[2].metric("未來5日", "−0.41%", "相對其他日 −0.77%")
foreign_research[3].metric("未來10日", "−0.66%", "相對其他日 −1.36%")
foreign_research[4].metric("未來20日", "−0.30%", "相對其他日 −1.67%")
st.caption("HAC與FDR校正後，1、5、10、20日的負向差異仍具統計證據。上述為d0收盤至未來收盤的統計報酬；法人資料於d0盤後公布，不等同可實現策略報酬。")

st.subheader("外資多空部位變化拆解")
st.caption("此處把淨部位變化拆成Long Change Ratio與Short Change Ratio。FDR校正後的結果作為主要判定；未通過者不視為已確認的預測訊號。")
decomposition = pd.DataFrame(
    [
        {
            "觀察期": "未來1日",
            "多單變化": "多單增加具正向證據",
            "關鍵結果": "PR 80～95：平均 +0.23%；相對其他日 +0.19%；FDR p=0.030",
            "空單變化": "FDR後未確認",
        },
        {
            "觀察期": "未來5日",
            "多單變化": "FDR後未確認",
            "關鍵結果": "部分原始HAC顯著，但多單與空單皆未通過FDR",
            "空單變化": "FDR後未確認",
        },
        {
            "觀察期": "未來10日",
            "多單變化": "多單減少後相對弱勢",
            "關鍵結果": "PR 5～20：相對其他日 −0.63%；FDR p=0.005",
            "空單變化": "FDR後未確認",
        },
        {
            "觀察期": "未來20日",
            "多單變化": "FDR後未確認",
            "關鍵結果": "多單與空單的個別變化皆未通過FDR",
            "空單變化": "FDR後未確認",
        },
    ]
)
st.dataframe(decomposition, hide_index=True, use_container_width=True)
st.info("判讀重點：整體OI Change Ratio最低5%的1、5、10、20日負向證據仍成立；但不能把它等同於『新增空單本身』。拆解後較穩健的來源是多單變化：多單增加對隔日偏正向，多單減少對10日相對偏弱；空單變化單獨看尚未通過FDR。")
st.caption("上述拆解屬研究解釋層，不另外改寫外資方向溫度分數，也不構成可交易訊號。")
st.caption(f"版本：v{APP_VERSION}｜資料來源：{data.source}｜市場廣度日期：{bdate}｜外資期貨日期：{fdate or '等待更新'}｜頁面產生：{datetime.now():%Y-%m-%d %H:%M}")
