from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .spot_flow_service import SpotFlowReport


@dataclass(frozen=True)
class DailyEvidence:
    source: str
    economic_signal_id: str
    label: str
    direction: str
    horizon: str
    level: str
    matched: bool
    status: str
    raw_label: str
    raw_value: str
    normalized_value: str
    threshold: str
    historical_result: str
    plain_explanation: str
    evidence_scope: str = "absolute"


@dataclass(frozen=True)
class HorizonSummary:
    horizon: str
    bullish_a: int
    bearish_a: int
    bullish_b: int
    bearish_b: int

    @property
    def total_bullish(self) -> int:
        return self.bullish_a + self.bullish_b

    @property
    def total_bearish(self) -> int:
        return self.bearish_a + self.bearish_b


@dataclass(frozen=True)
class DailyEvidenceReport:
    data_date: str
    evidence: tuple[DailyEvidence, ...]
    summaries: tuple[HorizonSummary, ...]
    warnings: tuple[str, ...] = ()

    @property
    def matched(self) -> tuple[DailyEvidence, ...]:
        return tuple(item for item in self.evidence if item.matched)


def rolling_pr_inclusive(series: pd.Series, window: int = 252) -> pd.Series:
    """PR of x[t] within the trailing window ending at t, matching the research pipeline."""
    values = pd.to_numeric(series, errors="coerce")

    def last_rank(window_values: np.ndarray) -> float:
        current = window_values[-1]
        valid = window_values[np.isfinite(window_values)]
        if not np.isfinite(current) or len(valid) < window:
            return np.nan
        return float(np.mean(valid <= current) * 100.0)

    return values.rolling(window, min_periods=window).apply(last_rank, raw=True)


def _num(value: object, digits: int = 4, percent: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "資料不足"
    if not np.isfinite(number):
        return "資料不足"
    return f"{number:.{digits}%}" if percent else f"{number:,.{digits}f}"


def build_futures_evidence(futures: pd.DataFrame | None) -> list[DailyEvidence]:
    """DEPRECATED non-canonical futures presentation retained for legacy reports.

    Formal production futures evidence is defined only by research_registry and
    evaluated by signal_engine.  Do not use this builder for forecast voting.
    """
    if futures is None or futures.empty:
        return []
    required = {"foreign_oi_change_ratio", "foreign_oi_ratio", "foreign_long_oi", "foreign_short_oi", "foreign_net_oi"}
    if not required.issubset(futures.columns):
        return []
    frame = futures.sort_index()
    row = frame.iloc[-1]
    change_pr = rolling_pr_inclusive(frame["foreign_oi_change_ratio"], 252).iloc[-1]
    level_pr = rolling_pr_inclusive(frame["foreign_oi_ratio"], 252).iloc[-1]
    change = row.get("foreign_oi_change_ratio")
    level = row.get("foreign_oi_ratio")
    long_oi = row.get("foreign_long_oi")
    short_oi = row.get("foreign_short_oi")
    net_oi = row.get("foreign_net_oi")
    previous_net = frame["foreign_net_oi"].shift(1).iloc[-1]
    denominator = frame["foreign_long_oi"].shift(1).iloc[-1] + frame["foreign_short_oi"].shift(1).iloc[-1]
    raw_change = (
        f"今日淨部位 {_num(net_oi, 0)} 口；昨日 {_num(previous_net, 0)} 口；"
        f"昨日多空未平倉合計 {_num(denominator, 0)} 口"
    )
    unavailable_change = not np.isfinite(float(change_pr)) if change_pr is not None else True
    unavailable_level = not np.isfinite(float(level_pr)) if level_pr is not None else True
    return [
        DailyEvidence("期貨", "futures_oi_change_high", "外資期貨單日大幅往多方移動", "bullish", "O1→C1", "A",
            not unavailable_change and change_pr > 80, "資料不足" if unavailable_change else "可判讀", "外資淨未平倉變化", raw_change,
            f"OI Change Ratio {_num(change, 3, True)}；252日 PR {_num(change_pr, 2)}", "PR80–100",
            "歷史平均 +0.118%，勝率 51.12%，相對非訊號日 +0.129 個百分點；Global FDR 通過。",
            "白話說：外資今天的期貨部位明顯往多方移動，歷史上隔日開盤到收盤平均較強。"),
        DailyEvidence("期貨", "futures_oi_change_low", "外資期貨單日大幅往空方移動", "bearish", "O1→C1", "A",
            not unavailable_change and change_pr <= 20, "資料不足" if unavailable_change else "可判讀", "外資淨未平倉變化", raw_change,
            f"OI Change Ratio {_num(change, 3, True)}；252日 PR {_num(change_pr, 2)}", "PR0–20",
            "歷史平均 −0.121%，勝率 41.91%，相對非訊號日 −0.169 個百分點；Global FDR 通過。",
            "白話說：外資今天的期貨部位明顯往空方移動，歷史上隔日開盤到收盤較弱。"),
        DailyEvidence("期貨", "futures_oi_change_low", "外資期貨單日大幅往空方移動（5日）", "bearish", "O1→C5", "B",
            not unavailable_change and change_pr <= 20, "資料不足" if unavailable_change else "可判讀", "外資淨未平倉變化", raw_change,
            f"OI Change Ratio {_num(change, 3, True)}；252日 PR {_num(change_pr, 2)}", "PR0–20",
            "歷史平均 −0.016%，但相對非訊號日落後 0.355 個百分點；只通過相對比較。",
            "白話說：未來5日比較像『相對市場偏弱』，不是已證明一定下跌。", "relative"),
        DailyEvidence("期貨", "futures_oi_level_high", "外資累積期貨部位位於高檔", "bullish", "O1→C1", "A",
            not unavailable_level and level_pr > 80, "資料不足" if unavailable_level else "可判讀", "外資期貨累積部位",
            f"多方 {_num(long_oi, 0)} 口；空方 {_num(short_oi, 0)} 口；淨部位 {_num(net_oi, 0)} 口",
            f"OI Ratio {_num(level, 3, True)}；252日 PR {_num(level_pr, 2)}", "PR80–100",
            "歷史平均 +0.0895%，相對非訊號日 +0.0986 個百分點；Global FDR 通過。",
            "白話說：外資目前累積部位相對偏多，歷史上隔日盤中平均略偏強；優先度低於單日變化。"),
    ]


def build_breadth_evidence(breadth: pd.DataFrame) -> list[DailyEvidence]:
    if breadth.empty:
        return []
    frame = breadth.sort_index()
    row = frame.iloc[-1]
    quality_ok = bool(row.get("breadth_quality_ok", False))
    specs = [
        ("breadth_bull_delta_down", "多頭市場廣度突然惡化", "bullish", "O1→C3", "delta_down_ratio_1d", 80,
         "歷史平均 +0.307%，中位數 +0.317%，勝率 58.47%；Level B（Family FDR）。",
         "多頭趨勢中，下跌家數比例突然大增；歷史上較像短線恐慌後反彈。"),
        ("breadth_limit_down_high", "跌停廣度達高檔", "bullish", "O1→C2", "limit_down_ratio", 80,
         "歷史平均 +0.194%，中位數 +0.193%，勝率 55.51%；Level B（Family FDR）。",
         "跌停股票占比位於高檔；歷史上較像恐慌後兩日反彈。"),
        ("breadth_limit_up_high", "高漲停廣度的三日延續", "bullish", "O1→C3", "limit_up_ratio", 80,
         "歷史平均 +0.222%，中位數 +0.179%，勝率 53.92%；只證明條件平均大於0。",
         "漲停股票占比偏高，歷史上三日內可能保留動能，但未證明優於其他日期。"),
        ("breadth_up_ratio_extreme", "普漲極端過熱", "bearish", "O1→C1", "up_ratio", 95,
         "歷史平均 −0.095%，中位數 −0.159%，勝率 37.81%；Level B主要來自勝率。",
         "上漲家數占比極高，歷史上隔日比較常回吐；不是已證明平均超額報酬顯著為負。"),
    ]
    evidence: list[DailyEvidence] = []
    regime = str(row.get("market_regime", "資料不足"))
    for signal_id, label, direction, horizon, metric, cutoff, history, explanation in specs:
        raw = row.get(metric, np.nan)
        pr_column = f"{metric}_pr252"
        pr = row.get(pr_column, np.nan)
        required_regime = signal_id == "breadth_bull_delta_down"
        available = quality_ok and np.isfinite(float(raw)) and np.isfinite(float(pr))
        matched = available and pr >= cutoff and (not required_regime or regime == "BULL")
        count_name = metric.replace("_ratio", "_count")
        count = row.get(count_name, np.nan)
        raw_text = f"{metric}={_num(raw, 3, True)}"
        if np.isfinite(float(count)):
            raw_text += f"；家數={_num(count, 0)}"
        if required_regime:
            raw_text += f"；市場狀態={regime}（0050相對MA60）"
        evidence.append(DailyEvidence("市場廣度", signal_id, label, direction, horizon, "B", matched,
            "可判讀" if available else "資料不足／品質未通過", metric, raw_text,
            f"252日 PR {_num(pr, 2)}", f"PR≥{cutoff}" + (" 且 BULL" if required_regime else ""),
            history, "白話說：" + explanation))
    return evidence


def build_spot_evidence(report: SpotFlowReport | None) -> list[DailyEvidence]:
    if report is None:
        return []
    evidence: list[DailyEvidence] = []
    for item in report.evidence:
        normalized = item.normalized_value
        norm_text = f"{item.normalization.upper()} {_num(normalized, 2)}（{item.reference_window}日）"
        raw_text = (
            f"累積買進 {_num(item.raw_buy_amount, 0)}；累積賣出 {_num(item.raw_sell_amount, 0)}；"
            f"同期間市場成交 {_num(item.market_turnover, 0)}；正式比例 {_num(item.current_value, 6)}"
        )
        evidence.append(DailyEvidence("法人現貨", item.economic_signal_id, item.label, item.direction,
            item.horizon, item.evidence_grade, item.a_grade_status == "matched", item.a_grade_status,
            f"{item.accumulation_days}日原始金額", raw_text, norm_text, item.threshold_label,
            item.historical_result, item.plain_explanation, item.evidence_scope))
    return evidence


def _deduplicate(items: Iterable[DailyEvidence]) -> list[DailyEvidence]:
    """One vote per source, economic signal, horizon, direction and level."""
    groups: dict[tuple[str, str, str, str, str], list[DailyEvidence]] = {}
    for item in items:
        key = (item.source, item.economic_signal_id, item.horizon, item.direction, item.level)
        groups.setdefault(key, []).append(item)
    selected: list[DailyEvidence] = []
    for versions in groups.values():
        matched = [item for item in versions if item.matched]
        selected.append(matched[0] if matched else versions[0])
    return selected


def build_daily_evidence_report(
    breadth: pd.DataFrame,
    futures: pd.DataFrame | None,
    spot: SpotFlowReport | None,
) -> DailyEvidenceReport:
    """Build the legacy research-only report; not a canonical forecast source."""
    all_items = build_futures_evidence(futures) + build_breadth_evidence(breadth) + build_spot_evidence(spot)
    deduped = _deduplicate(all_items)
    horizons = ("O1→C1", "O1→C2", "O1→C3", "O1→C5", "O1→C10", "O1→C20")
    summaries = []
    for horizon in horizons:
        matched = [item for item in deduped if item.matched and item.horizon == horizon]
        summaries.append(HorizonSummary(
            horizon,
            sum(item.direction == "bullish" and item.level == "A" for item in matched),
            sum(item.direction == "bearish" and item.level == "A" for item in matched),
            sum(item.direction == "bullish" and item.level == "B" for item in matched),
            sum(item.direction == "bearish" and item.level == "B" for item in matched),
        ))
    dates = [pd.Timestamp(frame.index[-1]).date() for frame in (breadth, futures) if frame is not None and not frame.empty]
    if spot is not None:
        dates.append(pd.Timestamp(spot.data_date).date())
    data_date = min(dates).isoformat() if dates else "資料不足"
    warnings = []
    if dates and len(set(dates)) > 1:
        warnings.append("三類資料日期尚未完全對齊；各訊號仍依自己的最新資料日判讀。")
    return DailyEvidenceReport(data_date, tuple(deduped), tuple(summaries), tuple(warnings))
