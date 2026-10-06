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
LISTED_TRUST = "上市投信"
LISTED_DEALER_SELF = "上市自營商(自行買賣)"
LISTED_DEALER_HEDGE = "上市自營商(避險)"
OTC_FOREIGN = "上櫃外資及陸資(不含自營商)"
OTC_FOREIGN_FULLWIDTH = "上櫃外資及陸資（不含自營商）"
OTC_TRUST = "上櫃投信"
OTC_DEALER_SELF = "上櫃自營商(自行買賣)"
OTC_DEALER_HEDGE = "上櫃自營商(避險)"


@dataclass(frozen=True)
class SpotEvidence:
    """Deprecated projection of canonical events for legacy reports/sheets."""

    family: str
    economic_signal_id: str
    trigger_id: str
    label: str
    direction: str
    horizon: str
    evidence_grade: str
    research_status: str
    a_grade_status: str
    current_value: float | None
    normalized_value: float | None
    normalization: str
    reference_window: int
    accumulation_days: int
    threshold_label: str
    raw_buy_amount: float | None
    raw_sell_amount: float | None
    market_turnover: float | None
    historical_result: str
    plain_explanation: str
    evidence_scope: str = "absolute"
    data_quality: str = "pass"
    quality_flags: tuple[str, ...] = ()
    research_only: bool = True

    @property
    def percentile(self) -> float | None:
        return self.normalized_value if self.normalization == "pr" else None

    @property
    def percentile_lower(self) -> float:
        return float("nan")

    @property
    def percentile_upper(self) -> float:
        return float("nan")

    @property
    def evidence_statement(self) -> str:
        return self.historical_result

    def as_record(self, data_date: str, recorded_at_taipei: str = "", version: str = "", git_commit: str = "") -> dict[str, Any]:
        market = "otc" if self.family.startswith("otc_") else ("combined" if self.family.startswith("combined_") else "listed")
        institution = "foreign" if "foreign" in self.family else ("dealer" if "dealer" in self.family else "total_institutional")
        metric = "buy" if "_buy" in self.trigger_id else ("sell" if "_sell" in self.trigger_id else "net")
        record = asdict(self)
        record.update(
            data_date=data_date, recorded_at_taipei=recorded_at_taipei,
            market=market, institution=institution, metric=metric,
            percentile=self.percentile, evidence_statement=self.historical_result,
            quality_flags=";".join(self.quality_flags), version=version, git_commit=git_commit,
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
    canonical_metrics: dict[str, pd.Series] | None = None


def _frame(value: object, name: str) -> pd.DataFrame:
    frame = pd.DataFrame(value).copy()
    dates = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[~pd.isna(dates)]
    frame.index = dates[~pd.isna(dates)]
    if frame.empty:
        raise RuntimeError(f"FinLab資料表為空或沒有有效日期：{name}")
    return frame.loc[~frame.index.duplicated(keep="last")].sort_index()


def _col(frame: pd.DataFrame, names: tuple[str, ...], table: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    raise RuntimeError(f"{table}缺少精確欄位：{' 或 '.join(names)}")


def _sum_components(*series: pd.Series) -> pd.Series:
    """Require every canonical component rather than silently skipping one."""
    return pd.concat(series, axis=1).sum(axis=1, min_count=len(series))


def _ratio(amount: pd.Series, turnover: pd.Series, days: int) -> pd.Series:
    """Canonical rolling sum(amount) / rolling sum(turnover)."""
    return safe_divide(
        amount.rolling(days, min_periods=days).sum(),
        turnover.rolling(days, min_periods=days).sum(),
    )


def nonoverlapping_flow_change(level: pd.Series, days: int) -> pd.Series:
    return pd.to_numeric(level, errors="coerce") - pd.to_numeric(level, errors="coerce").shift(days)


def rolling_pr_inclusive(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")

    def rank(items: np.ndarray) -> float:
        current = items[-1]
        valid = items[np.isfinite(items)]
        return float(np.mean(valid <= current) * 100) if np.isfinite(current) and len(valid) == window else np.nan

    return values.rolling(window, min_periods=window).apply(rank, raw=True)


def rolling_z_inclusive(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    mean = values.rolling(window, min_periods=window).mean()
    std = values.rolling(window, min_periods=window).std(ddof=0)
    return safe_divide(values - mean, std)


_pr = rolling_pr_inclusive
_z = rolling_z_inclusive


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _reconstruct_subjects(frame: pd.DataFrame, table: str) -> dict[str, pd.Series]:
    listed_foreign = _col(frame, (LISTED_FOREIGN, LISTED_FOREIGN_FULLWIDTH), table)
    listed_trust = _col(frame, (LISTED_TRUST,), table)
    listed_dealer = _sum_components(
        _col(frame, (LISTED_DEALER_SELF,), table),
        _col(frame, (LISTED_DEALER_HEDGE,), table),
    )
    otc_foreign = _col(frame, (OTC_FOREIGN, OTC_FOREIGN_FULLWIDTH), table)
    otc_trust = _col(frame, (OTC_TRUST,), table)
    otc_dealer = _sum_components(
        _col(frame, (OTC_DEALER_SELF,), table),
        _col(frame, (OTC_DEALER_HEDGE,), table),
    )
    listed_total = _sum_components(listed_foreign, listed_trust, listed_dealer)
    otc_total = _sum_components(otc_foreign, otc_trust, otc_dealer)
    return {
        "listed_foreign": listed_foreign,
        "listed_investment_trust": listed_trust,
        "listed_dealer": listed_dealer,
        "listed_total_institutional": listed_total,
        "otc_foreign": otc_foreign,
        "otc_investment_trust": otc_trust,
        "otc_dealer": otc_dealer,
        "otc_total_institutional": otc_total,
        "combined_foreign": _sum_components(listed_foreign, otc_foreign),
        "combined_investment_trust": _sum_components(listed_trust, otc_trust),
        "combined_dealer": _sum_components(listed_dealer, otc_dealer),
        "combined_total_institutional": _sum_components(listed_total, otc_total),
    }


def _historical_result(event: object) -> str:
    parts: list[str] = []
    for label, attribute, percent in (
        ("N", "sample_size", False), ("平均", "historical_mean_return", True),
        ("中位數", "historical_median_return", True), ("勝率", "historical_win_rate", True),
        ("相對", "relative_mean_return", True), ("Global FDR", "global_fdr", False),
        ("Family FDR", "family_fdr", False),
    ):
        value = getattr(event, attribute)
        if value is None:
            continue
        rendered = str(int(value)) if label == "N" else (f"{float(value):.4%}" if percent else f"{float(value):.8g}")
        parts.append(f"{label} {rendered}")
    return "；".join(parts) if parts else "正式研究未提供可顯示統計值"


def build_spot_flow_report(inst_buy: object, inst_sell: object, inst_net: object, market_amount: object) -> SpotFlowReport:
    buy = _frame(inst_buy, BUY_TABLE)
    sell = _frame(inst_sell, SELL_TABLE)
    net = _frame(inst_net, NET_TABLE)
    turnover = _frame(market_amount, TURNOVER_TABLE)
    index = buy.index.intersection(sell.index).intersection(net.index).intersection(turnover.index)
    if index.empty:
        raise RuntimeError("法人現貨與市場成交金額沒有共同交易日")
    buy, sell, net, turnover = buy.loc[index], sell.loc[index], net.loc[index], turnover.loc[index]

    amounts = {
        "buy": _reconstruct_subjects(buy, BUY_TABLE),
        "sell": _reconstruct_subjects(sell, SELL_TABLE),
        # Net is reconstructed from the official 買賣超 table, never Buy-Sell.
        "net": _reconstruct_subjects(net, NET_TABLE),
    }
    listed_turnover = _col(turnover, ("TAIEX",), TURNOVER_TABLE)
    otc_turnover = _col(turnover, ("OTC",), TURNOVER_TABLE)
    turnovers = {
        "listed": listed_turnover,
        "otc": otc_turnover,
        "combined": _sum_components(listed_turnover, otc_turnover),
    }

    canonical_metrics: dict[str, pd.Series] = {}
    for subject in amounts["buy"]:
        market = subject.split("_", 1)[0]
        for metric in ("buy", "sell", "net"):
            for days in (1, 5, 10):
                canonical_metrics[f"{subject}_{metric}_{days}d"] = _ratio(amounts[metric][subject], turnovers[market], days)

    # Legacy rows are projected from the canonical registry/evaluator. They do
    # not maintain an independent set of thresholds or research statistics.
    from .research_registry import CANONICAL_SIGNALS
    from .signal_engine import evaluate_signals, is_production_vote

    specs = tuple(signal for signal in CANONICAL_SIGNALS if signal.source == "spot_flow")
    events = evaluate_signals(canonical_metrics, index, signals=specs, as_of_date=index[-1])
    specs_by_id = {signal.signal_id: signal for signal in specs}
    evidence: list[SpotEvidence] = []
    for event in events:
        spec = specs_by_id[event.signal_id]
        raw_buy = amounts["buy"][spec.subject].rolling(spec.accumulation_days, min_periods=spec.accumulation_days).sum()
        raw_sell = amounts["sell"][spec.subject].rolling(spec.accumulation_days, min_periods=spec.accumulation_days).sum()
        raw_turnover = turnovers[spec.market].rolling(spec.accumulation_days, min_periods=spec.accumulation_days).sum()
        status = "insufficient_data" if event.normalized_value is None else ("matched" if event.matched else "not_matched")
        evidence.append(SpotEvidence(
            family=spec.subject, economic_signal_id=spec.economic_signal_id,
            trigger_id=spec.signal_id, label=spec.plain_definition,
            direction=spec.direction, horizon=f"O1→C{spec.horizon}",
            evidence_grade=spec.evidence_grade, research_status=spec.research_status,
            a_grade_status=status, current_value=event.raw_value,
            normalized_value=event.normalized_value,
            normalization="pr" if "rolling_pr" in spec.normalization else "z",
            reference_window=spec.rolling_window, accumulation_days=spec.accumulation_days,
            threshold_label=event.threshold, raw_buy_amount=_finite(raw_buy.iloc[-1]),
            raw_sell_amount=_finite(raw_sell.iloc[-1]), market_turnover=_finite(raw_turnover.iloc[-1]),
            historical_result=_historical_result(event),
            plain_explanation="白話說：" + spec.market_mechanism,
            evidence_scope=spec.evidence_scope,
        ))

    matched_votes = [event for event in events if is_production_vote(event)]
    directions: dict[str, set[str]] = {}
    for event in matched_votes:
        directions.setdefault(event.economic_signal_id, set()).add(event.direction)
    bullish = sum(value == {"bullish"} for value in directions.values())
    bearish = sum(value == {"bearish"} for value in directions.values())
    mixed = sum(len(value) > 1 for value in directions.values())
    family_state = "mixed" if mixed or (bullish and bearish) else (
        "bullish_evidence" if bullish else ("bearish_evidence" if bearish else "no_canonical_match")
    )
    return SpotFlowReport(index[-1].date().isoformat(), tuple(evidence), family_state, bullish, bearish, mixed, "pass", True, canonical_metrics)


def load_live_spot_flow() -> SpotFlowReport:
    from finlab import data

    return build_spot_flow_report(data.get(BUY_TABLE), data.get(SELL_TABLE), data.get(NET_TABLE), data.get(TURNOVER_TABLE))
