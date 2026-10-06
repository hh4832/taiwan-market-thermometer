"""Shared human-readable SignalEvent presentation for every Streamlit view."""
from __future__ import annotations

import math
import pandas as pd

from .research_registry import CANONICAL_SIGNALS
from .signal_engine import SignalEvent


REGISTRY_BY_ID = {signal.signal_id: signal for signal in CANONICAL_SIGNALS}
PERCENT_METRICS = {
    "up_ratio", "big_up_ratio_5d", "down_ratio",
    "foreign_net_oi_change_ratio_3d", "foreign_dealer_pr_divergence",
    "buy", "sell", "net", "gross",
}


def _known(value: object) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def format_metric_value(event: SignalEvent) -> str:
    if not _known(event.raw_value):
        return "無法判定"
    if event.metric in PERCENT_METRICS:
        return f"{float(event.raw_value):+.2%}"
    return f"{float(event.raw_value):g}"


def normalization_name(event: SignalEvent) -> str:
    normalization = event.normalization or getattr(REGISTRY_BY_ID.get(event.signal_id), "normalization", "")
    if "rolling_pr" in normalization:
        return "PR"
    if "rolling_z" in normalization:
        return "Z"
    if normalization == "raw":
        return "Raw"
    return normalization or "UNKNOWN"


def format_normalized_value(event: SignalEvent) -> str:
    if not _known(event.normalized_value):
        return "無法判定"
    label = normalization_name(event)
    return f"{label} {float(event.normalized_value):.2f}" if label != "Raw" else format_metric_value(event)


def format_percent(value: object, *, signed: bool = False, digits: int = 2) -> str:
    if not _known(value):
        return "無法判定"
    sign = "+" if signed else ""
    return f"{float(value):{sign}.{digits}%}"


def format_relative_return(value: object) -> str:
    if not _known(value):
        return "無法判定"
    return f"{float(value) * 100:+.3f} pp"


def format_number(value: object) -> str:
    if not _known(value):
        return "無法判定"
    return f"{float(value):.6g}"


def event_summary_record(event: SignalEvent) -> dict[str, object]:
    return {
        "訊號": event.plain_definition,
        "Signal Date": event.signal_date,
        "Horizon": f"C{event.horizon}",
        "方向": "多" if event.direction == "bullish" else "空",
        "指標原始值": format_metric_value(event),
        "PR / Z": format_normalized_value(event),
        "門檻": event.threshold or "無法判定",
        "結果": event.evaluation_status,
        "歷史勝率": format_percent(event.historical_win_rate, digits=1),
        "歷史平均報酬": format_percent(event.historical_mean_return, signed=True, digits=3),
        "Evidence": event.evidence_grade or "無法判定",
        "Origin": event.event_origin,
        "Target Date": event.target_date or "無法判定",
    }


def signal_summary_frame(events: tuple[SignalEvent, ...]) -> pd.DataFrame:
    return pd.DataFrame([event_summary_record(event) for event in events])


def event_audit_record(event: SignalEvent) -> dict[str, object]:
    return {
        "signal_date": event.signal_date,
        "target_date": event.target_date or "無法判定",
        "source": event.source,
        "subject": event.subject,
        "metric": event.metric or "無法判定",
        "economic_signal_id": event.economic_signal_id,
        "signal_id": event.signal_id,
        "direction": "多" if event.direction == "bullish" else "空",
        "horizon": f"O1→C{event.horizon}",
        "指標原始值": format_metric_value(event),
        "normalization_type": normalization_name(event),
        "normalized_value": format_normalized_value(event),
        "threshold": event.threshold or "無法判定",
        "matched": event.matched,
        "evaluation_status": event.evaluation_status,
        "historical_mean_return": format_percent(event.historical_mean_return, signed=True, digits=3),
        "historical_median_return": format_percent(event.historical_median_return, signed=True, digits=3),
        "historical_win_rate": format_percent(event.historical_win_rate, digits=1),
        "relative_mean_return": format_relative_return(event.relative_mean_return),
        "sample_size": "無法判定" if event.sample_size is None else int(event.sample_size),
        "evidence_grade": event.evidence_grade or "無法判定",
        "global_fdr": format_number(event.global_fdr),
        "family_fdr": format_number(event.family_fdr),
        "research_status": event.research_status,
        "research_commit": event.research_commit or "無法判定",
        "research_run": event.research_run or "無法判定",
        "event_origin": event.event_origin,
        "source_data_date": event.source_data_date or "無法判定",
        "availability_status": event.availability_status,
        "calculation_timestamp": event.calculation_timestamp,
        "plain_definition": event.plain_definition,
        "market_mechanism": event.market_mechanism,
        "risks": event.risks,
    }


def historical_validation_results(frame: pd.DataFrame) -> pd.DataFrame:
    """Add shared presentation outcomes without changing realized returns.

    This is the single implementation used by Streamlit and the Work Site
    publication layer.  actual_return remains the market return; directional
    sign handling is presentation-only.
    """
    result = frame.copy()
    if result.empty:
        result["directional_return"] = pd.Series(dtype="float64")
        result["outcome"] = pd.Series(dtype="object")
        return result

    required = {"direction", "actual_return", "maturity"}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(
            "historical validation presentation fields missing: "
            + ", ".join(sorted(missing))
        )

    def directional_result(row: pd.Series) -> tuple[object, str]:
        if row["maturity"] != "MATURED" or pd.isna(row["actual_return"]):
            return None, str(row["maturity"])
        actual_return = float(row["actual_return"])
        if row["direction"] == "bullish":
            directional_return = actual_return
        elif row["direction"] == "bearish":
            directional_return = -actual_return
        else:
            return None, "N/A"
        if directional_return > 0:
            outcome = "HIT"
        elif directional_return < 0:
            outcome = "MISS"
        else:
            outcome = "FLAT"
        return directional_return, outcome

    directional = result.apply(directional_result, axis=1)
    result["directional_return"] = directional.map(lambda value: value[0])
    result["outcome"] = directional.map(lambda value: value[1])
    return result


def historical_validation_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Format historical forecast outcomes for Streamlit without changing outcome semantics."""
    columns = {
        "signal_date": "Signal Date",
        "signal_id": "Signal",
        "direction": "方向",
        "horizon": "Horizon",
        "entry_date": "Entry Date",
        "target_date": "Target Date",
        "actual_return": "Actual Return",
        "directional_return": "Directional Return",
        "outcome": "Outcome",
        "maturity": "Maturity",
        "event_origin": "Event Origin",
    }
    if frame.empty:
        return pd.DataFrame(columns=columns.values())

    results = historical_validation_results(frame)
    shown = results.loc[:, list(columns)].copy()
    shown["direction"] = shown["direction"].map({"bullish": "偏多", "bearish": "偏空"}).fillna(shown["direction"])
    shown["horizon"] = shown["horizon"].map(lambda value: f"C{int(value)}")
    shown["actual_return"] = shown.apply(
        lambda row: f"{float(row['actual_return']):+.2%}"
        if row["maturity"] == "MATURED" and pd.notna(row["actual_return"])
        else "N/A",
        axis=1,
    )
    shown["directional_return"] = shown["directional_return"].map(
        lambda value: f"{float(value):+.2%}" if pd.notna(value) else "N/A"
    )
    return shown.loc[:, list(columns)].rename(columns=columns)
