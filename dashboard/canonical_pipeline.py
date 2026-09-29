"""Adapters from live services to the canonical registry engine."""
from __future__ import annotations

from datetime import datetime
import pandas as pd

from .signal_engine import SignalEvent, evaluate_signals
from .spot_flow_service import SpotFlowReport


def canonical_metrics(
    breadth: pd.DataFrame,
    futures: pd.DataFrame,
    spot: SpotFlowReport | None,
) -> dict[str, pd.Series]:
    metrics: dict[str, pd.Series] = {}
    for name in ("up_ratio", "big_up_ratio_5d"):
        if name in breadth:
            metrics[name] = breadth[name]
    for name in ("foreign_net_oi_change_ratio_3d", "foreign_dealer_pr_divergence"):
        if name in futures:
            metrics[name] = futures[name]
    if spot is not None and spot.canonical_metrics:
        metrics.update(spot.canonical_metrics)
    # Margin/short interactions remain intentionally absent until the canonical
    # research supplies a validated production hard cutoff.  Registry rows are
    # shown as UNVALIDATED_ACTIVATION_RULE and cannot vote.
    return metrics


def build_canonical_events(
    breadth: pd.DataFrame,
    futures: pd.DataFrame,
    spot: SpotFlowReport | None,
    sessions: pd.Index,
    calculation_timestamp: datetime | pd.Timestamp | None = None,
) -> tuple[SignalEvent, ...]:
    return evaluate_signals(canonical_metrics(breadth, futures, spot), sessions, calculation_timestamp)
