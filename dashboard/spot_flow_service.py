from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .scoring import safe_divide


TURNOVER_TABLE = "market_transaction_info:成交金額"
BUY_TABLE = "institutional_investors_trading_all_market_summary:買進金額"
SELL_TABLE = "institutional_investors_trading_all_market_summary:賣出金額"
NET_TABLE = "institutional_investors_trading_all_market_summary:買賣超"

LISTED_FOREIGN = "上市外資及陸資(不含外資自營商)"
LISTED_FOREIGN_FULLWIDTH = "上市外資及陸資（不含外資自營商）"
LISTED_DEALER_SELF = "上市自營商(自行買賣)"
LISTED_DEALER_HEDGE = "上市自營商(避險)"
OTC_TOTAL = "上櫃三大法人合計*"


@dataclass(frozen=True)
class SpotEvidence:
    family: str
    trigger_id: str
    label: str
    direction: str
    horizon: str
    a_grade_status: str
    current_value: float | None
    percentile: float | None
    reference_window: int
    accumulation_days: int
    raw_buy_amount: float | None
    raw_sell_amount: float | None
    market_turnover: float | None
    evidence_statement: str
    data_quality: str
    quality_flags: tuple[str, ...]
    research_only: bool = True

    def as_record(self, data_date: str, recorded_at_taipei: str = "", version: str = "", git_commit: str = "") -> dict[str, Any]:
        record = asdict(self)
        dimensions = {
            "listed_dealer_net": ("listed", "dealer", "net"),
            "otc_institutional_sell": ("otc", "total_institutional", "sell"),
            "listed_foreign_net": ("listed", "foreign", "net"),
        }
        market, institution, metric = dimensions[self.family]
        record.update(
            data_date=data_date,
            recorded_at_taipei=recorded_at_taipei,
            market=market,
            institution=institution,
            metric=metric,
            evidence_grade="A",
            quality_flags=";".join(self.quality_flags),
            version=version,
            git_commit=git_commit,
        )
        return record


@dataclass(frozen=True)
class SpotFlowReport:
    data_date: str
    evidence: tuple[SpotEvidence, ...]
    family_state: str
    bullish_family_count: int
    bearish_family_count: int
    mixed_family_count: int
    data_quality: str
    research_only: bool = True


def _daily_frame(source: pd.DataFrame, table_name: str) -> pd.DataFrame:
    frame = pd.DataFrame(source).copy()
    if frame.empty:
        raise RuntimeError(f"FinLab資料表為空：{table_name}")
    parsed = pd.to_datetime(frame.index, errors="coerce")
    valid = ~pd.isna(parsed)
    frame = frame.loc[valid].copy()
    if frame.empty:
        raise RuntimeError(f"FinLab資料表沒有有效日期：{table_name}")
    frame.index = parsed[valid]
    return frame.loc[~frame.index.duplicated(keep="last")].sort_index()


def _column(frame: pd.DataFrame, names: tuple[str, ...], table_name: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    expected = " 或 ".join(f"「{name}」" for name in names)
    raise RuntimeError(f"{table_name}缺少精確欄位{expected}")


def _rolling_sum_ratio(amount: pd.Series, turnover: pd.Series, days: int) -> pd.Series:
    return safe_divide(amount.rolling(days, min_periods=days).sum(), turnover.rolling(days, min_periods=days).sum())


def nonoverlapping_flow_change(level: pd.Series, days: int) -> pd.Series:
    """目前k日level減去前一個不重疊k日level。"""
    return pd.to_numeric(level, errors="coerce") - pd.to_numeric(level, errors="coerce").shift(days)


def rolling_percentile_midrank(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """以歷史小於值＋一半同值計算PR；排除當日，避免常數序列被誤判為PR 100。"""
    values = pd.to_numeric(series, errors="coerce")

    def rank_at(i: int) -> float:
        current = values.iloc[i]
        history = values.iloc[max(0, i - window):i].dropna()
        if pd.isna(current) or len(history) < min_periods:
            return np.nan
        return float((history.lt(current).sum() + 0.5 * history.eq(current).sum()) / len(history) * 100)

    return pd.Series((rank_at(i) for i in range(len(values))), index=values.index, dtype=float)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _status(percentile: float | None, lower: float, upper: float) -> str:
    if percentile is None:
        return "insufficient_data"
    return "matched" if lower < percentile <= upper else "not_matched"


def _formula_flags(rebuilt: pd.Series, official: pd.Series, family: str) -> tuple[str, ...]:
    left, right = _finite(rebuilt.iloc[-1]), _finite(official.iloc[-1])
    if left is None or right is None:
        return (f"{family}:latest_net_formula_unavailable",)
    tolerance = max(1.0, abs(right) * 1e-9)
    return () if abs(left - right) <= tolerance else (f"{family}:latest_buy_minus_sell_mismatch",)


def _evidence(
    *, family: str, trigger_id: str, label: str, direction: str, horizon: str,
    ratio: pd.Series, percentile: pd.Series, reference_window: int, accumulation_days: int,
    buy_amount: pd.Series, sell_amount: pd.Series, turnover: pd.Series,
    lower: float, upper: float, statement: str, flags: tuple[str, ...] = (),
) -> SpotEvidence:
    pr = _finite(percentile.iloc[-1])
    quality = "warning" if flags else "pass"
    status = "suspended" if flags else _status(pr, lower, upper)
    return SpotEvidence(
        family=family, trigger_id=trigger_id, label=label, direction=direction,
        horizon=horizon, a_grade_status=status, current_value=_finite(ratio.iloc[-1]),
        percentile=pr, reference_window=reference_window, accumulation_days=accumulation_days,
        raw_buy_amount=_finite(buy_amount.rolling(accumulation_days, min_periods=accumulation_days).sum().iloc[-1]),
        raw_sell_amount=_finite(sell_amount.rolling(accumulation_days, min_periods=accumulation_days).sum().iloc[-1]),
        market_turnover=_finite(turnover.rolling(accumulation_days, min_periods=accumulation_days).sum().iloc[-1]),
        evidence_statement=statement, data_quality=quality, quality_flags=flags,
    )


def build_spot_flow_report(
    inst_buy: pd.DataFrame,
    inst_sell: pd.DataFrame,
    inst_net: pd.DataFrame,
    market_amount: pd.DataFrame,
) -> SpotFlowReport:
    """建立A級法人現貨證據；不產生溫度、權重、預期報酬或操作建議。"""
    buy = _daily_frame(inst_buy, BUY_TABLE)
    sell = _daily_frame(inst_sell, SELL_TABLE)
    net = _daily_frame(inst_net, NET_TABLE)
    turnover_frame = _daily_frame(market_amount, TURNOVER_TABLE)
    common = buy.index.intersection(sell.index).intersection(net.index).intersection(turnover_frame.index)
    if common.empty:
        raise RuntimeError("法人現貨與市場成交金額沒有共同交易日")
    buy, sell, net, turnover_frame = buy.loc[common], sell.loc[common], net.loc[common], turnover_frame.loc[common]

    listed_turnover = _column(turnover_frame, ("TAIEX",), TURNOVER_TABLE)
    otc_turnover = _column(turnover_frame, ("OTC",), TURNOVER_TABLE)
    listed_foreign_buy = _column(buy, (LISTED_FOREIGN, LISTED_FOREIGN_FULLWIDTH), BUY_TABLE)
    listed_foreign_sell = _column(sell, (LISTED_FOREIGN, LISTED_FOREIGN_FULLWIDTH), SELL_TABLE)
    # 禁止fallback至只有單筆資料的「上市外資」。Net一律由正式Buy/Sell重建。
    listed_foreign_net = listed_foreign_buy - listed_foreign_sell
    listed_foreign_official_net = _column(net, (LISTED_FOREIGN, LISTED_FOREIGN_FULLWIDTH), NET_TABLE)
    listed_dealer_buy = _column(buy, (LISTED_DEALER_SELF,), BUY_TABLE) + _column(buy, (LISTED_DEALER_HEDGE,), BUY_TABLE)
    listed_dealer_sell = _column(sell, (LISTED_DEALER_SELF,), SELL_TABLE) + _column(sell, (LISTED_DEALER_HEDGE,), SELL_TABLE)
    listed_dealer_net = listed_dealer_buy - listed_dealer_sell
    listed_dealer_official_net = _column(net, (LISTED_DEALER_SELF,), NET_TABLE) + _column(net, (LISTED_DEALER_HEDGE,), NET_TABLE)
    otc_total_buy = _column(buy, (OTC_TOTAL,), BUY_TABLE)
    otc_total_sell = _column(sell, (OTC_TOTAL,), SELL_TABLE)
    otc_total_official_net = _column(net, (OTC_TOTAL,), NET_TABLE)

    family_flags = {
        "listed_dealer": _formula_flags(listed_dealer_net, listed_dealer_official_net, "listed_dealer"),
        "listed_foreign": _formula_flags(listed_foreign_net, listed_foreign_official_net, "listed_foreign"),
        "otc_total": _formula_flags(otc_total_buy - otc_total_sell, otc_total_official_net, "otc_total"),
    }

    ratios: dict[str, pd.Series] = {}
    for days in (1, 5, 10):
        ratios[f"listed_dealer_net_{days}"] = _rolling_sum_ratio(listed_dealer_net, listed_turnover, days)
        ratios[f"listed_foreign_net_{days}"] = _rolling_sum_ratio(listed_foreign_net, listed_turnover, days)
        ratios[f"otc_total_sell_{days}"] = _rolling_sum_ratio(otc_total_sell, otc_turnover, days)

    percentiles = {
        (name, window): rolling_percentile_midrank(series, window=window, min_periods=window)
        for name, series in ratios.items() for window in (504, 756)
    }
    common_args = {
        "listed_dealer": (listed_dealer_buy, listed_dealer_sell, listed_turnover),
        "listed_foreign": (listed_foreign_buy, listed_foreign_sell, listed_turnover),
        "otc_total": (otc_total_buy, otc_total_sell, otc_turnover),
    }
    specs = [
        ("listed_dealer_net", "listed_dealer_net_5d_high_pr504", "上市自營商Net 5日偏高", "bullish", "10d", "listed_dealer_net_5", 504, 5, 80, 95,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來10日報酬偏高相關。目前僅代表統計條件命中，不代表未來必然上漲。", "listed_dealer"),
        ("listed_dealer_net", "listed_dealer_net_10d_extreme_pr756", "上市自營商Net 10日極高", "bullish", "10d", "listed_dealer_net_10", 756, 10, 95, 100,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來10日報酬偏高相關。目前僅代表統計條件命中，不代表未來必然上漲。", "listed_dealer"),
        ("otc_institutional_sell", "otc_total_sell_1d_low_pr504", "上櫃三大法人Sell 1日偏低", "bullish", "5-10d", "otc_total_sell_1", 504, 1, 5, 20,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來5至10日報酬偏高相關，可能反映上櫃法人賣壓降低。", "otc_total"),
        ("otc_institutional_sell", "otc_total_sell_5d_low_pr504", "上櫃三大法人Sell 5日偏低", "bullish", "5-10d", "otc_total_sell_5", 504, 5, 5, 20,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來5至10日報酬偏高相關，可能反映上櫃法人賣壓持續收縮。", "otc_total"),
        ("otc_institutional_sell", "otc_total_sell_5d_mid_high_pr756", "上櫃三大法人Sell 5日中度偏高", "bearish", "10d", "otc_total_sell_5", 756, 5, 60, 80,
         "符合Phase 2 A級偏空候選定義；歷史研究中與0050未來10日報酬偏低相關，可能反映上櫃市場賣壓持續。", "otc_total"),
        ("otc_institutional_sell", "otc_total_sell_10d_mid_high_pr756", "上櫃三大法人Sell 10日中度偏高", "bearish", "10d", "otc_total_sell_10", 756, 10, 60, 80,
         "符合Phase 2 A級偏空候選定義；歷史研究中與0050未來10日報酬偏低相關，可能反映上櫃市場賣壓持續。", "otc_total"),
        ("listed_foreign_net", "listed_foreign_net_5d_extreme_pr504", "上市外資Net 5日極高（504日）", "bullish", "10d", "listed_foreign_net_5", 504, 5, 95, 100,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來10日報酬偏高相關，但空頭期間樣本較少。", "listed_foreign"),
        ("listed_foreign_net", "listed_foreign_net_5d_extreme_pr756", "上市外資Net 5日極高（756日）", "bullish", "10d", "listed_foreign_net_5", 756, 5, 95, 100,
         "符合Phase 2 A級偏多候選定義；歷史研究中與0050未來10日報酬偏高相關，但空頭期間樣本較少。", "listed_foreign"),
    ]
    evidence: list[SpotEvidence] = []
    for family, trigger, label, direction, horizon, key, ref_window, days, lower, upper, statement, raw_key in specs:
        raw_buy, raw_sell, raw_turnover = common_args[raw_key]
        evidence.append(_evidence(
            family=family, trigger_id=trigger, label=label, direction=direction, horizon=horizon,
            ratio=ratios[key], percentile=percentiles[(key, ref_window)], reference_window=ref_window,
            accumulation_days=days, buy_amount=raw_buy, sell_amount=raw_sell, turnover=raw_turnover,
            lower=lower, upper=upper, statement=statement, flags=family_flags[raw_key],
        ))

    matched = [item for item in evidence if item.a_grade_status == "matched"]
    family_directions: dict[str, set[str]] = {}
    for item in matched:
        family_directions.setdefault(item.family, set()).add(item.direction)
    bullish = sum(directions == {"bullish"} for directions in family_directions.values())
    bearish = sum(directions == {"bearish"} for directions in family_directions.values())
    mixed = sum(len(directions) > 1 for directions in family_directions.values())
    if mixed or (bullish and bearish):
        family_state = "mixed"
    elif bullish:
        family_state = "bullish_evidence"
    elif bearish:
        family_state = "bearish_evidence"
    else:
        family_state = "no_a_grade_match"
    quality = "warning" if any(item.data_quality != "pass" for item in evidence) else "pass"
    return SpotFlowReport(common[-1].date().isoformat(), tuple(evidence), family_state, bullish, bearish, mixed, quality)


def load_live_spot_flow() -> SpotFlowReport:
    from finlab import data

    return build_spot_flow_report(
        data.get(BUY_TABLE), data.get(SELL_TABLE), data.get(NET_TABLE), data.get(TURNOVER_TABLE)
    )
