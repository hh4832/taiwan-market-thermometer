"""Canonical, versioned research registry for the Taiwan Market Thermometer.

This module is deliberately data-only.  Downstream code must not maintain a
second copy of thresholds or historical statistics.  ``None`` means that the
canonical research snapshot did not validate that field; it must never be
rendered as zero.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Direction = Literal["bullish", "bearish"]
ResearchStatus = Literal["RETAINED", "RETEST", "REJECTED"]
EvidenceScope = Literal["absolute", "relative"]
DirectionMode = Literal["fixed", "conditional"]
InteractionSign = Literal["positive", "negative"]


@dataclass(frozen=True)
class ResearchSignal:
    signal_id: str
    economic_signal_id: str
    source: str
    subject: str
    market: str
    metric: str
    formula: str
    accumulation_days: int
    rolling_window: int
    normalization: str
    normalization_reference: str
    threshold_lower: float | None
    threshold_upper: float | None
    prior_condition: str | None
    direction: Direction
    horizon: int
    evidence_grade: str
    research_status: ResearchStatus
    sample_size: int | None
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    relative_mean_return: float | None
    global_fdr: float | None
    family_fdr: float | None
    monotonicity: str
    extreme_validity: str
    annual_robustness: str
    nonoverlap_robustness: str
    signal_timing: str
    entry_timing: str
    outcome_definition: str
    price_adjustment: str
    plain_definition: str
    market_mechanism: str
    research_repo: str
    research_branch: str
    research_commit: str
    research_run: str
    research_as_of_date: str
    activation_ready: bool = True
    interaction_formula: str | None = None
    risks: str = "post-selection；尚無 untouched out-of-sample 驗證"
    threshold_lower_inclusive: bool = True
    threshold_upper_inclusive: bool = False
    evidence_scope: EvidenceScope = "absolute"
    direction_mode: DirectionMode = "fixed"
    interaction_sign: InteractionSign | None = None
    conditional_effect_description: str | None = None
    performance_scope: str = "registered signal-group descriptive statistics"
    evidence_test_scope: str = "registered signal evidence test"
    flow_ratio_type: str | None = None
    representative_scope: str = "canonical registered research row"
    extreme_evidence_grade: str = "UNKNOWN"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _signal(**values: object) -> ResearchSignal:
    defaults: dict[str, object] = {
        "market": "Taiwan",
        "normalization_reference": "rolling historical observations",
        "monotonicity": "non-monotonic",
        "extreme_validity": "only the registered interval is validated",
        "annual_robustness": "UNKNOWN",
        "nonoverlap_robustness": "UNKNOWN",
        "signal_timing": "d0 close after source data are available",
        "entry_timing": "next Taiwan trading session adjusted open (O1)",
        "outcome_definition": "adjusted_close[d0+h] / adjusted_open[d0+1] - 1",
        "price_adjustment": "FinLab adjusted open / adjusted close",
        "research_as_of_date": "2026-09-15",
    }
    defaults.update(values)
    return ResearchSignal(**defaults)  # type: ignore[arg-type]


_BREADTH_PROV = dict(
    research_repo="hh4832/taiwan-market-breadth-research",
    research_branch="feature/v11-breadth-mechanism-regime",
    research_commit="62d7aba6a2173a52e4b53afbadd1e3327ac8169a",
    research_run="20260915_150729_v11_breadth_mechanism_regime_62d7aba6a217",
)
_FUTURES_PROV = dict(
    research_repo="hh4832/taiwan-futures-oi-research",
    research_branch="research/futures-finite-grid-robustness",
    research_commit="c55c9bf91208f5b09912460ac65553d1a439f7e6",
    research_run="20260914_181109_v4_divergence_horizon_extension_c55c9bf9",
)
_SPOT_PROV = dict(
    research_repo="hh4832/-institutional-spot-flow-study",
    research_branch="phase25-prior-return-c20",
    research_commit="353fa7037505c8022db5a5b01601d0cdec370e6f",
    research_run="20260913_174226_353fa703",
)
_MARGIN_PROV = dict(
    research_repo="hh4832/taiwan-margin-short-0050-research",
    research_branch="research/margin-buy-sell-prior-return-joint",
    research_commit="4a6fb82795342a55d174dbfc9beb1ee80a93bb4c",
    research_run="20260913_102806_4a6fb82_margin_buy_sell_prior_return_joint",
)
_SHORT_PROV = dict(
    research_repo="hh4832/taiwan-margin-short-0050-research",
    research_branch="research/short-flow-prior-return-symmetry",
    research_commit="c3a89fb2f998ad69f6ea5e7ad362ea7fe2dfa696",
    research_run="20260913_112534_c3a89fb_short_flow_prior_return_symmetry",
)


def _breadth(signal_id: str, economic: str, metric: str, accumulation: int, window: int,
             low: float, high: float | None, direction: Direction, horizon: int,
             status: ResearchStatus, plain: str, mechanism: str, **stats: object) -> ResearchSignal:
    return _signal(signal_id=signal_id, economic_signal_id=economic, source="market_breadth",
        subject="listed_and_otc_breadth", metric=metric, formula=f"{metric} {accumulation}D level",
        accumulation_days=accumulation, rolling_window=window, normalization="rolling_pr_inclusive",
        threshold_lower=low, threshold_upper=high, prior_condition=None, direction=direction,
        horizon=horizon, evidence_grade=str(stats.pop("evidence_grade", "B")), research_status=status,
        sample_size=stats.pop("sample_size", None), mean_return=stats.pop("mean_return", None),
        median_return=stats.pop("median_return", None), win_rate=stats.pop("win_rate", None),
        relative_mean_return=stats.pop("relative_mean_return", None), global_fdr=stats.pop("global_fdr", None),
        family_fdr=stats.pop("family_fdr", None), plain_definition=plain, market_mechanism=mechanism,
        **_BREADTH_PROV, **stats)


def _futures(signal_id: str, economic: str, metric: str, direction: Direction, horizon: int,
             low: float, high: float | None, status: ResearchStatus, **stats: object) -> ResearchSignal:
    return _signal(signal_id=signal_id, economic_signal_id=economic, source="futures",
        subject="foreign_taiex_futures", metric=metric,
        formula="(NetOI[t]-NetOI[t-3]) / (LongOI[t-3]+ShortOI[t-3])" if metric == "foreign_net_oi_change_ratio_3d" else "foreign PR - dealer PR",
        accumulation_days=3, rolling_window=120,
        normalization="raw" if metric == "foreign_dealer_pr_divergence" else "rolling_pr_strict_prior",
        threshold_lower=low, threshold_upper=high, prior_condition=None, direction=direction,
        horizon=horizon, evidence_grade=str(stats.pop("evidence_grade", "A")), research_status=status,
        sample_size=stats.pop("sample_size", None), mean_return=stats.pop("mean_return", None),
        median_return=stats.pop("median_return", None), win_rate=stats.pop("win_rate", None),
        relative_mean_return=stats.pop("relative_mean_return", None), global_fdr=stats.pop("global_fdr", None),
        family_fdr=stats.pop("family_fdr", None), plain_definition=stats.pop("plain_definition", "外資期貨部位三日變化位於研究確認區間。"),
        market_mechanism=stats.pop("market_mechanism", "期貨未平倉部位反映法人風險方向與價格發現。"),
        **_FUTURES_PROV, **stats)


def _spot(signal_id: str, economic: str, subject: str, metric: str, accumulation: int, window: int,
          norm: str, low: float, high: float | None, direction: Direction, horizon: int,
          status: ResearchStatus, **stats: object) -> ResearchSignal:
    market = subject.split("_", 1)[0]
    lower_inclusive = norm != "pr"
    upper_inclusive = norm == "pr"
    return _signal(signal_id=signal_id, economic_signal_id=economic, source="spot_flow", subject=subject,
        market=market,
        metric=metric, formula=f"sum({metric} amount,{accumulation}D)/sum(market turnover,{accumulation}D)",
        accumulation_days=accumulation, rolling_window=window, normalization=f"rolling_{norm}_inclusive",
        threshold_lower=low, threshold_upper=high, prior_condition=None, direction=direction, horizon=horizon,
        threshold_lower_inclusive=stats.pop("threshold_lower_inclusive", lower_inclusive),
        threshold_upper_inclusive=stats.pop("threshold_upper_inclusive", upper_inclusive),
        evidence_grade=str(stats.pop("evidence_grade", "A")), research_status=status,
        sample_size=stats.pop("sample_size", None), mean_return=stats.pop("mean_return", None),
        median_return=stats.pop("median_return", None), win_rate=stats.pop("win_rate", None),
        relative_mean_return=stats.pop("relative_mean_return", None), global_fdr=stats.pop("global_fdr", None),
        family_fdr=stats.pop("family_fdr", None), plain_definition=stats.pop("plain_definition", "法人現貨流量進入研究確認區間。"),
        market_mechanism=stats.pop("market_mechanism", "歷史資料存在預測關聯，但市場機制尚未證實。"),
        monotonicity=stats.pop("monotonicity", "UNKNOWN"),
        extreme_validity=stats.pop("extreme_validity", "UNKNOWN"),
        **_SPOT_PROV, **stats)


def _interaction(signal_id: str, economic: str, subject: str, horizon: int, direction: Direction,
                 window: int, accumulation: int, sample: int, mean: float, median: float,
                 win: float, relative: float, global_fdr: float, family_fdr: float,
                 provenance: dict[str, str], interaction_sign: InteractionSign,
                 status: ResearchStatus = "RETAINED", extreme_validity: str = "NO",
                 extreme_evidence_grade: str = "No Evidence",
                 annual_robustness: str = "UNKNOWN") -> ResearchSignal:
    is_margin = subject in {"margin_buy", "margin_sell"}
    flow_name = {
        "margin_buy": "margin_balance:融資券總買進[上市融資交易金額 + 上櫃融資交易金額]",
        "margin_sell": "margin_balance:融資券總賣出[上市融資交易金額 + 上櫃融資交易金額]",
        "short_sell": "margin_transactions:融券賣出 lots × 1000",
        "short_cover": "margin_transactions:融券買進 lots × 1000",
        "short_repayment": "margin_transactions:融券現券償還 lots × 1000",
    }[subject]
    denominator = (
        "market_transaction_info:成交金額[TAIEX + OTC]"
        if is_margin else "market_transaction_info:成交股數[TAIEX + OTC]"
    )
    description = {
        "margin_buy": "Prior5D 越高，future effect 越正",
        "margin_sell": "Prior5D 越低，future effect 越正",
        "short_cover": "Prior5D 越低，future effect 越改善",
        "short_repayment": "Prior5D 越低，future effect 越改善",
        "short_sell": "局部證據顯示 Prior5D 越高 effect 越正；穩定性不足",
    }[subject]
    return _signal(signal_id=signal_id, economic_signal_id=economic, source="margin_short", subject=subject,
        market="listed_and_otc", metric=subject,
        formula=f"sum({flow_name},{accumulation}D) / sum({denominator},{accumulation}D)",
        interaction_formula=f"FlowHigh(PR95-100) × continuous Prior5D return",
        accumulation_days=accumulation, rolling_window=window, normalization="rolling_pr_inclusive",
        normalization_reference="current-inclusive trailing percentile of the flow ratio",
        threshold_lower=95.0, threshold_upper=100.0,
        threshold_lower_inclusive=True, threshold_upper_inclusive=True,
        prior_condition="continuous Prior5D interaction; production hard cutoff not validated",
        direction=direction, horizon=horizon, evidence_grade="A", research_status=status,
        sample_size=sample, mean_return=mean, median_return=median, win_rate=win,
        relative_mean_return=relative, global_fdr=global_fdr, family_fdr=family_fdr,
        plain_definition=f"{subject} 流量條件必須和前五日報酬交互解讀，不能單獨視為多空。",
        market_mechanism="信用／融券流量的意義取決於先前價格路徑。",
        direction_mode="conditional", interaction_sign=interaction_sign,
        conditional_effect_description=description,
        performance_scope="PR95-100 flow-group descriptive statistics",
        evidence_test_scope="FlowHigh × continuous Prior5D interaction",
        flow_ratio_type="amount_ratio" if is_margin else "volume_ratio",
        representative_scope="canonical representative research row; not the full parameter grid",
        monotonicity="non-monotonic", extreme_validity=extreme_validity,
        extreme_evidence_grade=extreme_evidence_grade,
        annual_robustness=annual_robustness,
        activation_ready=False, risks="尚未驗證 production hard cutoff；不得計入正式票數",
        **provenance)


CANONICAL_SIGNALS: tuple[ResearchSignal, ...] = (
    _breadth("breadth_up_ratio_1d_pr60_ge95_c1", "breadth_up_ratio_exhaustion", "up_ratio", 1, 60, 95, None, "bearish", 1, "RETAINED", "當日上漲家數比例位於近60日最極端5%。", "極端普漲後可能短線耗竭。", sample_size=256),
    _breadth("breadth_big_up_ratio_5d_pr60_60_80_c3", "breadth_big_up_continuation", "big_up_ratio_5d", 5, 60, 60, 80, "bullish", 3, "RETAINED", "五日強漲家數廣度位於近60日中高區間。", "中高強度而非極端的強漲廣度可能延續。", sample_size=722, relative_mean_return=.00199),
    _breadth("breadth_big_up_ratio_5d_pr60_60_80_c5", "breadth_big_up_continuation", "big_up_ratio_5d", 5, 60, 60, 80, "bullish", 5, "RETAINED", "五日強漲家數廣度位於近60日中高區間。", "中高強度而非極端的強漲廣度可能延續。", sample_size=722, relative_mean_return=.00362),
    _breadth("breadth_down_ratio_high_legacy", "breadth_down_ratio_mean_reversion", "down_ratio", 1, 252, 80, None, "bullish", 3, "RETEST", "舊版下跌家數高檔反彈條件。", "效果有市場狀態依賴，需修改後再測。"),

    _futures("futures_foreign_change_pr0_20_c1", "futures_foreign_change_bearish", "foreign_net_oi_change_ratio_3d", "bearish", 1, 0, 20, "RETAINED", threshold_upper_inclusive=True, monotonicity="continuous positive gradient; full-bin strict monotonicity not required", annual_robustness="15/20 years directionally consistent", sample_size=935, mean_return=-.00121, median_return=-.00073, win_rate=.5679, relative_mean_return=-.00134, global_fdr=.000102022, family_fdr=.00000514397),
    _futures("futures_foreign_change_pr0_20_c3", "futures_foreign_change_bearish", "foreign_net_oi_change_ratio_3d", "bearish", 3, 0, 20, "RETAINED", threshold_upper_inclusive=True, monotonicity="continuous positive gradient; full-bin strict monotonicity not required", sample_size=935, mean_return=-.00176, median_return=-.00074, win_rate=.5219, relative_mean_return=-.00309, global_fdr=.00301993, family_fdr=.000444108),
    _futures("futures_foreign_change_pr0_20_c5", "futures_foreign_change_bearish", "foreign_net_oi_change_ratio_3d", "bearish", 5, 0, 20, "RETAINED", threshold_upper_inclusive=True, monotonicity="continuous positive gradient; full-bin strict monotonicity not required", evidence_grade="B", sample_size=935, mean_return=-.00112, median_return=0.0, win_rate=.5005, relative_mean_return=-.00368, global_fdr=.0628217, family_fdr=.00435085),
    _futures("futures_foreign_change_pr0_20_c10", "futures_foreign_change_bearish", "foreign_net_oi_change_ratio_3d", "bearish", 10, 0, 20, "RETAINED", threshold_upper_inclusive=True, evidence_scope="relative", monotonicity="continuous positive gradient; full-bin strict monotonicity not required", sample_size=935, mean_return=.00049, median_return=.00174, win_rate=.4824, relative_mean_return=-.00504, global_fdr=.0386016, family_fdr=.00883042, risks="relative-only evidence；絕對平均報酬非負；不得說成保證下跌"),
    _futures("futures_foreign_change_pr80_100_c1", "futures_foreign_change_bullish", "foreign_net_oi_change_ratio_3d", "bullish", 1, 80, 100, "RETAINED", threshold_upper_inclusive=True, monotonicity="continuous positive gradient; full-bin strict monotonicity not required", annual_robustness="15/20 years directionally consistent", sample_size=912, mean_return=.00097, median_return=.00066, win_rate=.5154, relative_mean_return=.00084, global_fdr=.0378952, family_fdr=.00843523),
    _futures("futures_foreign_change_pr80_100_c3", "futures_foreign_change_bullish", "foreign_net_oi_change_ratio_3d", "bullish", 3, 80, 100, "RETEST", threshold_upper_inclusive=True, monotonicity="continuous positive gradient; full-bin strict monotonicity not required"),
    _futures("futures_divergence_le_m60_c1", "futures_foreign_dealer_divergence_bearish", "foreign_dealer_pr_divergence", "bearish", 1, -1, -.6, "RETAINED", threshold_upper_inclusive=True, monotonicity="4/4 ordered; higher divergence -> higher return", annual_robustness="15/20 years directionally consistent", sample_size=593, mean_return=-.00106, median_return=-.00073, win_rate=.5717, global_fdr=.00155866, family_fdr=.00124693),
    _futures("futures_divergence_ge60_c1", "futures_foreign_dealer_divergence_bullish", "foreign_dealer_pr_divergence", "bullish", 1, .6, 1, "RETAINED", threshold_upper_inclusive=True, monotonicity="4/4 ordered; higher divergence -> higher return", annual_robustness="14/20 years directionally consistent", sample_size=602, mean_return=.00095, median_return=.00059, win_rate=.5100, global_fdr=.0186971, family_fdr=.0169973),

    # Spot-flow registry is an exact transcription of Spotflow Master Summary.
    _spot("spot_otc_total_sell5_pr5_20_c3", "spot_otc_total_sell_pressure_low", "otc_total_institutional", "sell", 5, 504, "pr", 5, 20, "bullish", 3, "RETEST", sample_size=203, mean_return=.0060, median_return=.0057, win_rate=.6305, relative_mean_return=.0043, global_fdr=.0124204, family_fdr=.0207007),
    _spot("spot_otc_total_sell5_pr5_20_c5", "spot_otc_total_sell_pressure_low", "otc_total_institutional", "sell", 5, 504, "pr", 5, 20, "bullish", 5, "RETAINED", sample_size=203, mean_return=.013111, median_return=.010668, win_rate=.714286, relative_mean_return=.010030, global_fdr=.000491177, family_fdr=.000245588, annual_robustness="stable"),
    _spot("spot_otc_total_sell5_pr5_20_c10", "spot_otc_total_sell_pressure_low", "otc_total_institutional", "sell", 5, 504, "pr", 5, 20, "bullish", 10, "RETAINED", sample_size=203, mean_return=.02427, median_return=.0213, win_rate=.7931, relative_mean_return=.0169, global_fdr=.000529845, family_fdr=.000176615, annual_robustness="stable"),
    _spot("spot_otc_total_sell5_pr5_20_c20", "spot_otc_total_sell_pressure_low", "otc_total_institutional", "sell", 5, 504, "pr", 5, 20, "bullish", 20, "RETAINED", sample_size=203, mean_return=.0408, median_return=.0401, win_rate=.8374, relative_mean_return=.0245, global_fdr=.00650161, family_fdr=.0024381, annual_robustness="stable"),
    _spot("spot_otc_dealer_sell10_z_m25_m15_c3", "spot_otc_dealer_sell_pressure_extreme_low", "otc_dealer", "sell", 10, 504, "z", -2.5, -1.5, "bullish", 3, "RETEST", sample_size=94, mean_return=.0079, median_return=.0091, win_rate=.6596, relative_mean_return=.0061, global_fdr=.03135, family_fdr=.036575, extreme_validity="PARTIAL"),
    _spot("spot_otc_dealer_sell10_z_m25_m15_c5", "spot_otc_dealer_sell_pressure_extreme_low", "otc_dealer", "sell", 10, 504, "z", -2.5, -1.5, "bullish", 5, "RETAINED", sample_size=94, mean_return=.015215, median_return=.011435, win_rate=.744681, relative_mean_return=.011684, global_fdr=.0128201, family_fdr=.00925895, annual_robustness="partially stable", extreme_validity="PARTIAL"),
    _spot("spot_otc_dealer_sell10_z_m25_m15_c10", "spot_otc_dealer_sell_pressure_extreme_low", "otc_dealer", "sell", 10, 504, "z", -2.5, -1.5, "bullish", 10, "RETAINED", sample_size=94, mean_return=.0331, median_return=.0387, win_rate=.8085, relative_mean_return=.0254, global_fdr=.00108734, family_fdr=.000402087, annual_robustness="partially stable", extreme_validity="PARTIAL"),
    _spot("spot_otc_dealer_sell10_z_m25_m15_c20", "spot_otc_dealer_sell_pressure_extreme_low", "otc_dealer", "sell", 10, 504, "z", -2.5, -1.5, "bullish", 20, "RETAINED", sample_size=94, mean_return=.0717, median_return=.0665, win_rate=.9894, relative_mean_return=.0557, global_fdr=.00000308612, family_fdr=.000000514353, annual_robustness="partially stable", extreme_validity="PARTIAL"),
    _spot("spot_combined_foreign_sell10_z_ge25_c5", "spot_combined_foreign_sell_extreme", "combined_foreign", "sell", 10, 756, "z", 2.5, None, "bullish", 5, "RETEST", sample_size=54, mean_return=.0172, median_return=.0132, win_rate=.7222, relative_mean_return=.0153, global_fdr=.0128201, family_fdr=.00925895, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_combined_foreign_sell10_z_ge25_c10", "spot_combined_foreign_sell_extreme", "combined_foreign", "sell", 10, 756, "z", 2.5, None, "bullish", 10, "RETEST", sample_size=54, mean_return=.0448, median_return=.0412, win_rate=.8889, relative_mean_return=.0409, global_fdr=.0000131204, family_fdr=.00000437347, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_combined_foreign_sell10_z_ge25_c20", "spot_combined_foreign_sell_extreme", "combined_foreign", "sell", 10, 756, "z", 2.5, None, "bullish", 20, "RETEST", sample_size=54, mean_return=.0764, median_return=.0629, win_rate=.9074, relative_mean_return=.0683, global_fdr=.00108734, family_fdr=.000422854, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_listed_dealer_net10_pr95_100_c5", "spot_listed_dealer_net_high", "listed_dealer", "net", 10, 756, "pr", 95, 100, "bullish", 5, "REJECTED", evidence_grade="C", sample_size=109, mean_return=.0111, median_return=.0079, win_rate=.6697, relative_mean_return=.0073, global_fdr=.202107, family_fdr=.183237, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_listed_dealer_net10_pr95_100_c10", "spot_listed_dealer_net_high", "listed_dealer", "net", 10, 756, "pr", 95, 100, "bullish", 10, "RETAINED", sample_size=109, mean_return=.0309, median_return=.0279, win_rate=.8349, relative_mean_return=.0230, global_fdr=.0128201, family_fdr=.00523784, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_listed_dealer_net10_pr95_100_c20", "spot_listed_dealer_net_high", "listed_dealer", "net", 10, 756, "pr", 95, 100, "bullish", 20, "RETEST", sample_size=109, mean_return=.0460, median_return=.0372, win_rate=.8257, relative_mean_return=.0284, global_fdr=.0457146, family_fdr=.0228573, annual_robustness="stable", extreme_validity="PARTIAL"),
    _spot("spot_otc_total_sell5_pr60_80_c10", "spot_otc_total_sell_pressure_mid_high", "otc_total_institutional", "sell", 5, 756, "pr", 60, 80, "bearish", 10, "RETEST", sample_size=285, mean_return=-.00996, median_return=-.00956, win_rate=.4175, relative_mean_return=-.0191, global_fdr=.0013628, family_fdr=.000755033, annual_robustness="unstable"),
    _spot("spot_otc_foreign_buy5_z05_15_c5", "spot_otc_foreign_buy_high", "otc_foreign", "buy", 5, 756, "z", .5, 1.5, "bearish", 5, "REJECTED", evidence_grade="C", sample_size=977, mean_return=.0010, median_return=.0014, win_rate=.5261, relative_mean_return=-.0019, global_fdr=.243486, family_fdr=.186672, annual_robustness="unstable", evidence_scope="relative", risks="relative underperformance evidence；不得解讀為一定下跌"),
    _spot("spot_otc_foreign_buy5_z05_15_c10", "spot_otc_foreign_buy_high", "otc_foreign", "buy", 5, 756, "z", .5, 1.5, "bearish", 10, "RETEST", sample_size=977, mean_return=.0001, median_return=.0009, win_rate=.5107, relative_mean_return=-.0073, global_fdr=.00650161, family_fdr=.00233197, annual_robustness="unstable", evidence_scope="relative", risks="relative underperformance evidence；不得解讀為一定下跌"),
    _spot("spot_otc_foreign_buy5_z05_15_c20", "spot_otc_foreign_buy_high", "otc_foreign", "buy", 5, 756, "z", .5, 1.5, "bearish", 20, "RETEST", sample_size=972, mean_return=.0010, median_return=.0038, win_rate=.5319, relative_mean_return=-.0131, global_fdr=.000927084, family_fdr=.000386285, annual_robustness="unstable", evidence_scope="relative", risks="relative underperformance evidence；不得解讀為一定下跌"),
    _spot("spot_listed_foreign_net5_pr95_100_c5", "spot_listed_foreign_net_high", "listed_foreign", "net", 5, 756, "pr", 95, 100, "bullish", 5, "REJECTED", evidence_grade="C", sample_size=165, mean_return=.0054, median_return=.0042, win_rate=.5879, relative_mean_return=.0022, global_fdr=.364011, family_fdr=.273545, annual_robustness="stable", extreme_validity="NO"),
    _spot("spot_listed_foreign_net5_pr95_100_c10", "spot_listed_foreign_net_high", "listed_foreign", "net", 5, 756, "pr", 95, 100, "bullish", 10, "REJECTED", evidence_grade="C", sample_size=165, mean_return=.0108, median_return=.0061, win_rate=.6727, relative_mean_return=.0038, global_fdr=.328633, family_fdr=.237346, annual_robustness="stable", extreme_validity="NO"),
    _spot("spot_listed_foreign_net5_pr95_100_c20", "spot_listed_foreign_net_high", "listed_foreign", "net", 5, 756, "pr", 95, 100, "bullish", 20, "REJECTED", evidence_grade="C", sample_size=165, mean_return=.0185, median_return=.0167, win_rate=.7212, relative_mean_return=.0037, global_fdr=.568627, family_fdr=.458061, annual_robustness="stable", extreme_validity="NO"),

    # Margin / Short rows are canonical representatives, not the complete significant grid.
    # Performance fields describe the PR95-100 flow group; FDR/grade test the continuous-Prior5D interaction.
    _interaction("margin_sell_prior5_c3", "margin_sell_prior5", "margin_sell", 3, "bullish", 252, 10, 151, .00613, .00434, .56954, .00466, .000257944, .000128972, _MARGIN_PROV, "negative", extreme_validity="PARTIAL", extreme_evidence_grade="C"),
    _interaction("margin_sell_prior5_c5", "margin_sell_prior5", "margin_sell", 5, "bullish", 252, 10, 151, .00733, .00290, .56291, .00452, .0000182944, .0000091472, _MARGIN_PROV, "negative"),
    _interaction("margin_buy_prior5_c5", "margin_buy_prior5", "margin_buy", 5, "bullish", 252, 5, 181, .00483, .00363, .56906, .00209, .0381372, .0429044, _MARGIN_PROV, "positive"),
    _interaction("margin_buy_prior5_c10", "margin_buy_prior5", "margin_buy", 10, "bullish", 126, 5, 301, .00829, .00414, .54817, .00271, .0308675, .0337608, _MARGIN_PROV, "positive"),
    _interaction("margin_buy_prior5_c20", "margin_buy_prior5", "margin_buy", 20, "bullish", 126, 5, 301, .01192, .00821, .56146, .00010, .000475706, .00071356, _MARGIN_PROV, "positive"),
    _interaction("margin_sell_prior5_k5_c10", "margin_sell_prior5", "margin_sell", 10, "bullish", 252, 5, 153, .00909, .01026, .64706, .00308, .00670188, .00570337, _MARGIN_PROV, "negative"),
    _interaction("margin_sell_prior5_k5_c20", "margin_sell_prior5", "margin_sell", 20, "bullish", 252, 5, 153, .01644, .01398, .64706, .00380, .00489066, .0042947, _MARGIN_PROV, "negative"),
    _interaction("short_repayment_prior5_c5", "short_repayment_prior5", "short_repayment", 5, "bullish", 252, 10, 260, .00446, .00274, .54615, .00158, .0380226, .0268437, _SHORT_PROV, "negative"),
    _interaction("short_repayment_prior5_c10", "short_repayment_prior5", "short_repayment", 10, "bullish", 756, 10, 194, .00745, .00701, .60825, .00032, .00393724, .00262483, _SHORT_PROV, "negative"),
    _interaction("short_cover_prior5_c5", "short_cover_prior5", "short_cover", 5, "bearish", 504, 10, 181, -.00066, .00168, .50829, -.00357, .00204134, .000680448, _SHORT_PROV, "negative"),
    _interaction("short_cover_prior5_c10", "short_cover_prior5", "short_cover", 10, "bullish", 126, 5, 315, .00689, .01161, .64762, .00031, .0380226, .0235378, _SHORT_PROV, "negative", annual_robustness="PARTIAL"),
    _interaction("short_cover_prior5_k3_c20", "short_cover_prior5", "short_cover", 20, "bullish", 126, 3, 273, .01016, .01896, .64103, -.00362, .00393724, .00231071, _SHORT_PROV, "negative", annual_robustness="PARTIAL"),
    _interaction("short_repayment_prior5_k10_w126_c20", "short_repayment_prior5", "short_repayment", 20, "bullish", 126, 10, 375, .01170, .01418, .61333, -.00205, .00393724, .00262483, _SHORT_PROV, "negative", annual_robustness="FAILED_DIRECTIONAL_CONSISTENCY"),
    _interaction("short_sell_prior5_c5", "short_sell_prior5", "short_sell", 5, "bearish", 504, 10, 193, -.00106, -.00101, .48187, -.00397, .011362, .0302988, _SHORT_PROV, "positive", "RETEST"),
)


def registry(status: ResearchStatus | None = None) -> tuple[ResearchSignal, ...]:
    return tuple(s for s in CANONICAL_SIGNALS if status is None or s.research_status == status)


def validate_registry(signals: tuple[ResearchSignal, ...] = CANONICAL_SIGNALS) -> list[str]:
    errors: list[str] = []
    ids = [s.signal_id for s in signals]
    if len(ids) != len(set(ids)):
        errors.append("signal_id must be unique")
    for s in signals:
        if s.research_status not in {"RETAINED", "RETEST", "REJECTED"}:
            errors.append(f"{s.signal_id}: invalid research_status")
        if not s.economic_signal_id:
            errors.append(f"{s.signal_id}: missing economic_signal_id")
        if s.research_status == "RETAINED" and not (s.research_commit and s.research_run):
            errors.append(f"{s.signal_id}: retained signal missing provenance")
        if s.direction not in {"bullish", "bearish"} or s.horizon not in {1, 3, 5, 10, 20}:
            errors.append(f"{s.signal_id}: invalid direction or horizon")
        if s.threshold_lower is not None and s.threshold_upper is not None and s.threshold_lower >= s.threshold_upper:
            errors.append(f"{s.signal_id}: invalid threshold interval")
        if s.evidence_scope not in {"absolute", "relative"}:
            errors.append(f"{s.signal_id}: invalid evidence_scope")
        if s.direction_mode not in {"fixed", "conditional"}:
            errors.append(f"{s.signal_id}: invalid direction_mode")
        if s.direction_mode == "conditional" and s.interaction_sign not in {"positive", "negative"}:
            errors.append(f"{s.signal_id}: conditional signal missing interaction_sign")
    return errors
